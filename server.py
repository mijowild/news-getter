#!/usr/bin/env python3
"""news-getter: tiny local RSS/Atom reader. Stdlib only. Run: python3 server.py"""
import collections, csv, html, json, math, os, re, sqlite3, threading, time, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "news.db")
PORT = int(os.environ.get("PORT", "8765"))
REFRESH_EVERY = 30 * 60
KEEP_PER_SOURCE = 300
UA = "Mozilla/5.0 (compatible; news-getter/1.0)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, feed TEXT NOT NULL UNIQUE,
  lang TEXT DEFAULT 'en', category TEXT DEFAULT '', note TEXT DEFAULT '',
  position INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
  etag TEXT, modified TEXT, last_fetch INTEGER, last_error TEXT);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, guid TEXT NOT NULL,
  title TEXT, short_title TEXT, url TEXT, published INTEGER, summary TEXT, content TEXT,
  read INTEGER DEFAULT 0, UNIQUE(source_id, guid));
CREATE INDEX IF NOT EXISTS items_pub ON items(published DESC);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def mute_regex(terms):
    """Terms match at the start of a word ('fussball' hits 'Fussballer', not 'Transport');
    a leading '*' matches anywhere in a word ('*sport' hits 'Wintersport')."""
    parts = []
    for t in terms:
        t = t.strip().casefold()
        if not t:
            continue
        parts.append(re.escape(t[1:]) if t.startswith("*") and len(t) > 1 else r"(?<!\w)" + re.escape(t))
    return re.compile("|".join(parts)) if parts else None


# Per-article paywall markers that feeds really carry: title prefixes (Golem "(g+)", heise "heise+ |",
# Spiegel "(S+)", Tagesspiegel "(T+)") and category tags (Balkan Insight "... - Premium Only").
PAY_TITLE = re.compile(r"^\s*(\(\s*[gGsStT]\+\s*\)|heise\+\s*[|:]|\[\+\]|\(€\)|\(\+\))")
PAY_TAG = re.compile(r"premium only|paywall|members only|subscribers? only|plus-artikel|bezahlinhalt", re.I)


def is_paywalled(title, tags):
    return 1 if PAY_TITLE.search(title or "") or PAY_TAG.search(tags or "") else 0


STOP = set("""
about above after again against also always among another because been before being between both build but can could did does doing done down during each even ever every
from further get gets getting going had has have having here how however into its itself just keep know like made make makes many may might more most much must need never new next
not now off once only other our out over own part per same say says should since some such than that the their them then there these they this those through today too under until
upon use used uses using very want was way well were what when where which while who whom why will with within without would year years yes you your
aber alle allem allen aller alles also andere anderen anderer anderes auch dass dann dazu dein deine denn der dem den des die das diese diesem diesen dieser dieses doch durch eine einem einen einer eines
euch eure für gegen haben hatte hier hinter ihre ihrem ihren ihrer ihres ist jede jedem jeden jeder jedes jetzt kann kein keine keinen keiner können machen mehr mein meine muss müssen nach neue neuen neuer neues nicht noch nur oder ohne sagt sein seine seinen seiner seit sich sie sind soll sollen sondern über um und uns unser unsere unter viel viele vom von vor war waren warum was weil weiter wenn wer werden wie wieder wieso wird wir wollen wurde wurden zum zur zwischen
mal macht gibt geht zeigt steht liegt bleibt wegen bereits immer schon sehr ganz etwa rund laut heute drei zwei
https http www points ycombinator first time appeared post posts read reading watch video comment comments item items link links click here story weiterlesen anzeige beitrag erschien zuerst
""".split())
BOILERPLATE = re.compile(r"(The post .*? appeared first on .*|Der Beitrag .*? erschien zuerst .*|Continue reading.*|Weiterlesen.*)$", re.I | re.S)
GENERIC_TAGS = {"allgemein", "uncategorized", "artikel", "news", "blog", "general", "nachrichten", "article", "articles", "sonstiges"}
WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]{3,}")


def topics(rows, source_name, n=3):
    """Top tags (used >=2x), then words that recur across >=2 articles (headline + teaser), for rows of one source."""
    skip_words = {w.lower() for w in WORD_RE.findall(source_name)}
    tag_count, tag_form = collections.Counter(), {}
    word_df, word_form = collections.Counter(), collections.defaultdict(collections.Counter)
    for r in rows:
        for t in {t.strip() for t in (r["tags"] or "").split("|") if t.strip()}:
            k = t.casefold()
            if k in GENERIC_TAGS or k == source_name.casefold() or PAY_TAG.search(t) or not 3 <= len(t) <= 30 or k.isdigit():
                continue
            tag_count[k] += 1
            tag_form.setdefault(k, t)
        teaser = BOILERPLATE.sub("", re.sub(r"<[^>]+>", " ", html.unescape(r["summary"] or "")))[:400]
        text = (r["title"] or "") + " " + teaser
        seen = set()
        for w in WORD_RE.findall(text):
            k = w.strip("-").casefold()
            if len(k) < 4 or k in STOP or k in skip_words:
                continue
            word_form[k][w] += 1
            seen.add(k)
        word_df.update(seen)
    out = [tag_form[k] for k, c in tag_count.most_common(n) if c >= 2]
    tag_keys = {t.casefold() for t in out}
    for k, c in word_df.most_common(n * 4):
        if len(out) >= n:
            break
        if c >= 2 and k not in tag_keys:
            out.append(word_form[k].most_common(1)[0][0])
    return out[:n]


class Conn(sqlite3.Connection):
    mute_terms = ()


def db():
    c = sqlite3.connect(DB_PATH, timeout=30, factory=Conn)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    row = c.execute("SELECT value FROM settings WHERE key='mute'").fetchone() if c.execute(
        "SELECT name FROM sqlite_master WHERE name='settings'").fetchone() else None
    c.mute_terms = json.loads(row["value"]) if row else []
    rx = mute_regex(c.mute_terms)
    c.create_function("muted", 2, lambda t, g: 1 if rx and rx.search(((t or "") + " " + (g or "").replace("|", " ")).casefold()) else 0)
    return c


def init_db():
    c = db()
    c.executescript(SCHEMA)
    if "tags" not in [r["name"] for r in c.execute("PRAGMA table_info(items)")]:
        c.execute("ALTER TABLE items ADD COLUMN tags TEXT DEFAULT ''")
    if "site" not in [r["name"] for r in c.execute("PRAGMA table_info(sources)")]:
        c.execute("ALTER TABLE sources ADD COLUMN site TEXT")
    if "paywalled" not in [r["name"] for r in c.execute("PRAGMA table_info(items)")]:
        c.execute("ALTER TABLE items ADD COLUMN paywalled INTEGER DEFAULT 0")
    if "cluster" not in [r["name"] for r in c.execute("PRAGMA table_info(items)")]:
        c.execute("ALTER TABLE items ADD COLUMN cluster INTEGER")
    if "paywall_mode" not in [r["name"] for r in c.execute("PRAGMA table_info(sources)")]:
        c.execute("ALTER TABLE sources ADD COLUMN paywall_mode INTEGER DEFAULT 0")
        # outlets whose feeds only carry teasers of member-only articles
        c.execute("UPDATE sources SET paywall_mode=1 WHERE name IN ('Republik (CH)','Krautreporter')")
    for r in c.execute("SELECT id,title,tags,paywalled FROM items").fetchall():
        p = is_paywalled(r["title"], r["tags"])
        if p != r["paywalled"]:
            c.execute("UPDATE items SET paywalled=? WHERE id=?", (p, r["id"]))
    if c.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0:
        path = os.path.join(ROOT, "sources.tsv")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for i, r in enumerate(csv.DictReader(f, delimiter="\t")):
                    c.execute("INSERT OR IGNORE INTO sources(name,feed,lang,category,note,position) VALUES(?,?,?,?,?,?)",
                              (r["name"], r["feed"], r["lang"], r["category"], r["note"], i))
    c.commit()
    c.close()


# ---------- story clustering ----------

CLUSTER_GAP = 36 * 3600         # items further apart than this are not the same story
CLUSTER_MIN_SIM = 0.4
CLUSTER_RARE = 12               # a match needs a shared word in at most this many headlines (per 3000 stored)
CLUSTER_TITLE_NOISE = re.compile(r"^(anzeige|podcast|video|liveblog|live|kommentar|analyse|ticker)\s*[:|-]\s*", re.I)


def story_tokens(title):
    """Stems (first 6 letters) of the distinctive words of a headline. Feed tags are ignored: they are shared
    by unrelated articles of one source and would glue them together."""
    out = set()
    for w in WORD_RE.findall(CLUSTER_TITLE_NOISE.sub("", title or "")):
        k = w.strip("-").casefold()
        if len(k) >= 4 and k not in STOP:
            out.add(k[:6])
    return out


def cluster_items(rows):
    """rows: dicts with id, title, tags, published. Returns {id: cluster_id} for items that share a story with
    at least one other item. Two items match when they share >=2 distinctive word stems and their IDF-weighted
    cosine similarity is high enough; matches chain (single link) within CLUSTER_GAP of each other."""
    toks = {r["id"]: story_tokens(r["title"]) for r in rows}
    ident = {r["id"]: (r["source_id"], (r["title"] or "").casefold()) for r in rows}
    df = collections.Counter(t for ts in toks.values() for t in ts)
    n = len(rows)
    idf = {t: 1 + max(0.0, math.log(n / c)) for t, c in df.items()}
    norm = {i: sum(idf[t] ** 2 for t in ts) ** 0.5 for i, ts in toks.items()}
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pub = {r["id"]: r["published"] for r in rows}
    by_tok = collections.defaultdict(list)
    for r in sorted(rows, key=lambda r: r["published"]):
        i = r["id"]
        shared = collections.defaultdict(list)
        for t in toks[i]:
            if df[t] > max(40, n // 20):   # words in a large share of all headlines carry no story signal
                continue
            for j in by_tok[t]:
                shared[j].append(t)
            by_tok[t].append(i)
        for j, ts in shared.items():
            if len(ts) < 2 or abs(pub[i] - pub[j]) > CLUSTER_GAP or ident[i] == ident[j]:
                continue   # identical titles from one source are a recurring series, not a story
            if min(df[t] for t in ts) > max(CLUSTER_RARE, CLUSTER_RARE * n // 3000):
                continue   # no name/place/event word in common, only generic vocabulary
            sim = sum(idf[t] ** 2 for t in ts) / (norm[i] * norm[j])
            if sim >= CLUSTER_MIN_SIM:
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i in parent:
        groups[find(i)].append(i)
    return {i: min(g) for g in groups.values() if len(g) > 1 for i in g}


def recluster(c):
    """Cluster every stored item of an enabled source."""
    rows = [dict(r) for r in c.execute("SELECT i.id,i.source_id,i.title,i.published FROM items i "
                                       "JOIN sources s ON s.id=i.source_id WHERE s.enabled=1")]
    assign = cluster_items(rows)
    c.execute("UPDATE items SET cluster=NULL WHERE cluster IS NOT NULL")
    c.executemany("UPDATE items SET cluster=? WHERE id=?", [(cl, i) for i, cl in assign.items()])
    c.commit()


# ---------- text helpers ----------

def shorten(t, n=90):
    t = html.unescape(re.sub(r"\s+", " ", t or "")).strip()
    t = re.sub(r"\s+[|–—]\s+[^|–—]{2,40}$", "", t)
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return t


ALLOWED = {"p", "br", "a", "img", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
           "em", "strong", "b", "i", "code", "pre", "figure", "figcaption", "hr", "table", "thead",
           "tbody", "tr", "td", "th", "sup", "sub", "span", "div"}
DROP = {"script", "style", "iframe", "object", "embed", "noscript", "svg", "form", "head"}
VOID = {"br", "img", "hr"}


class Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if re.match(r"(https?://|mailto:)", href, re.I):
                self.out.append(f'<a href="{html.escape(href, True)}" target="_blank" rel="noopener noreferrer">')
            else:
                self.out.append("<a>")
        elif tag == "img":
            pass  # images are never kept or loaded
        else:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in DROP:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in ALLOWED and tag not in VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))


def sanitize(s):
    p = Sanitizer()
    try:
        p.feed(s or "")
        p.close()
    except Exception:
        return html.escape(re.sub(r"<[^>]+>", " ", s or ""))
    return "".join(p.out)


# ---------- feed parsing ----------

def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def inner(el):
    if el is None:
        return ""
    if len(el):
        return (el.text or "") + "".join(ET.tostring(c, encoding="unicode") for c in el)
    return el.text or ""


def parse_date(s):
    now = int(time.time())
    if not s:
        return now
    s = s.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return now
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return min(int(dt.timestamp()), now)


def parse_feed(data):
    data = data.lstrip()
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        text = data.decode("utf-8", "replace")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        text = re.sub(r"^<\?xml[^>]*\?>", "", text)
        root = ET.fromstring(text)
    out = []
    site = ""
    head = root.find("channel") if local(root.tag) == "rss" else root
    for ch in (head if head is not None else []):
        if local(ch.tag) == "link":
            href = ch.get("href")
            if href and ch.get("rel") in (None, "alternate"):
                site = href
            elif ch.text and ch.text.strip().startswith("http"):
                site = ch.text.strip()
            if site:
                break
    for e in root.iter():
        if local(e.tag) not in ("item", "entry"):
            continue
        f = {}
        link = ""
        tags = []
        for ch in e:
            n = local(ch.tag)
            if n in ("category", "subject"):
                t = (ch.get("term") or ch.text or "").strip()
                if t and t.lower() not in [x.lower() for x in tags]:
                    tags.append(t)
            elif n == "link":
                href = ch.get("href")
                if href:
                    if ch.get("rel") in (None, "alternate") or not link:
                        link = href
                elif ch.text and not link:
                    link = ch.text.strip()
            elif n in ("title", "guid", "id", "pubDate", "published", "updated", "date",
                       "description", "summary", "content", "encoded") and n not in f:
                f[n] = inner(ch) if n in ("description", "summary", "content", "encoded") else (ch.text or "").strip()
        title = f.get("title", "")
        out.append({
            "guid": f.get("guid") or f.get("id") or link or title,
            "title": html.unescape(title),
            "url": link,
            "published": parse_date(f.get("pubDate") or f.get("published") or f.get("date") or f.get("updated")),
            "summary": f.get("description") or f.get("summary") or "",
            "content": f.get("encoded") or f.get("content") or "",
            "tags": "|" + "|".join(tags) + "|" if tags else "",
        })
    return out, site


# ---------- fetching ----------

refresh_lock = threading.Lock()
status = {"refreshing": False, "last": None}


def fetch_source(s):
    req = urllib.request.Request(s["feed"], headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, */*"})
    if s["site"]:  # no conditional GET until the outlet's homepage URL is known
        if s["etag"]:
            req.add_header("If-None-Match", s["etag"])
        if s["modified"]:
            req.add_header("If-Modified-Since", s["modified"])
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(8_000_000)
            items, site = parse_feed(data)
            return s["id"], items, r.headers.get("ETag"), r.headers.get("Last-Modified"), None, site
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return s["id"], [], s["etag"], s["modified"], None, None
        return s["id"], [], None, None, f"HTTP {e.code}", None
    except Exception as e:
        return s["id"], [], None, None, f"{type(e).__name__}: {e}"[:200], None


def refresh_all():
    if not refresh_lock.acquire(blocking=False):
        return
    status["refreshing"] = True
    try:
        c = db()
        srcs = c.execute("SELECT * FROM sources WHERE enabled=1").fetchall()
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(fetch_source, srcs))
        for sid, items, etag, mod, err, site in results:
            for it in items:
                c.execute("""INSERT INTO items(source_id,guid,title,short_title,url,published,summary,content,tags,paywalled)
                             VALUES(?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(source_id,guid) DO UPDATE SET tags=excluded.tags, paywalled=excluded.paywalled""",
                          (sid, it["guid"], it["title"], shorten(it["title"]), it["url"], it["published"],
                           sanitize(it["summary"]), sanitize(it["content"]), it["tags"],
                           is_paywalled(it["title"], it["tags"])))
            c.execute("UPDATE sources SET etag=?, modified=?, last_fetch=?, last_error=?, site=COALESCE(?, site) WHERE id=?",
                      (etag, mod, int(time.time()), err, site or None, sid))
            c.execute("""DELETE FROM items WHERE source_id=? AND id NOT IN
                         (SELECT id FROM items WHERE source_id=? ORDER BY published DESC LIMIT ?)""",
                      (sid, sid, KEEP_PER_SOURCE))
        c.commit()
        recluster(c)
        c.close()
        status["last"] = int(time.time())
    finally:
        status["refreshing"] = False
        refresh_lock.release()


def scheduler():
    while True:
        try:
            refresh_all()
        except Exception as e:
            print("refresh error:", e)
        time.sleep(REFRESH_EVERY)


# ---------- HTTP ----------

def site_url(s):
    origin = "{0.scheme}://{0.netloc}/".format(urlparse(s["feed"]))
    site = s["site"] or origin
    return origin if re.search(r"feed|rss", urlparse(site).path, re.I) else site


def item_json(r):
    return {"id": r["id"], "source_id": r["source_id"], "source": r["sname"], "title": r["short_title"] or r["title"],
            "orig": r["title"], "url": r["url"], "published": r["published"], "read": bool(r["read"]), "cluster": r["cluster"],
            "tags": [t for t in (r["tags"] or "").split("|") if t],
            "paywalled": bool(r["paywalled"] or r["pmode"])}


ITEM_SQL = """SELECT i.*, s.name AS sname, s.paywall_mode AS pmode FROM items i JOIN sources s ON s.id=i.source_id"""
NOPAY_SQL = "NOT (i.paywalled=1 OR s.paywall_mode=1)"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json", filename=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(b)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self):
        try:
            return json.loads(self.body() or b"{}")
        except Exception:
            return {}

    def route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        c = db()
        try:
            if method == "GET" and p == "/":
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    return self.send(f.read(), ctype="text/html")
            if method == "GET" and p == "/api/status":
                return self.send(status)
            if method == "GET" and p == "/api/mute":
                hidden = c.execute("SELECT COUNT(*) FROM items i JOIN sources s ON s.id=i.source_id "
                                   "WHERE s.enabled=1 AND muted(i.title, i.tags)").fetchone()[0]
                return self.send({"terms": c.mute_terms, "hidden": hidden})
            if method == "POST" and p == "/api/mute":
                terms = [str(t).strip() for t in self.json_body().get("terms", []) if str(t).strip()]
                c.execute("INSERT OR REPLACE INTO settings VALUES('mute',?)", (json.dumps(terms, ensure_ascii=False),))
                c.commit()
                return self.send({"ok": True})
            if method == "POST" and p == "/api/refresh":
                threading.Thread(target=refresh_all, daemon=True).start()
                return self.send({"ok": True})
            if method == "GET" and p == "/api/sources":
                rows = c.execute("""SELECT s.*, (SELECT COUNT(*) FROM items WHERE source_id=s.id) AS n
                                    FROM sources s ORDER BY position, id""").fetchall()
                return self.send([{k: r[k] for k in ("id", "name", "feed", "lang", "category", "note", "position",
                                                     "enabled", "last_fetch", "last_error", "n", "paywall_mode")} | {"site": site_url(r)} for r in rows])
            if method == "POST" and p == "/api/sources":
                b = self.json_body()
                if not str(b.get("feed", "")).startswith(("http://", "https://")):
                    return self.send({"error": "feed URL required"}, 400)
                pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM sources").fetchone()[0]
                c.execute("INSERT OR IGNORE INTO sources(name,feed,lang,category,position) VALUES(?,?,?,?,?)",
                          (b.get("name") or b["feed"], b["feed"], b.get("lang", "en"), b.get("category", ""), pos))
                c.commit()
                threading.Thread(target=refresh_all, daemon=True).start()
                return self.send({"ok": True})
            if method == "POST" and p == "/api/sources/enable-all":
                n = c.execute("UPDATE sources SET enabled=1 WHERE enabled=0").rowcount
                c.commit()
                if n:
                    threading.Thread(target=refresh_all, daemon=True).start()
                return self.send({"enabled": n})
            if method == "POST" and p == "/api/sources/reorder":
                for pos, sid in enumerate(self.json_body().get("ids", [])):
                    c.execute("UPDATE sources SET position=? WHERE id=?", (pos, int(sid)))
                c.commit()
                return self.send({"ok": True})
            if method == "GET" and p == "/api/export-opml":
                rows = c.execute("SELECT * FROM sources ORDER BY position, id").fetchall()
                top = ET.Element("opml", version="2.0")
                ET.SubElement(ET.SubElement(top, "head"), "title").text = "news-getter sources"
                body = ET.SubElement(top, "body")
                for r in rows:
                    ET.SubElement(body, "outline", type="rss", text=r["name"], title=r["name"], xmlUrl=r["feed"],
                                  htmlUrl=site_url(r), language=r["lang"] or "", category=r["category"] or "")
                ET.indent(top)
                return self.send(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(top, encoding="utf-8"),
                                 ctype="text/x-opml", filename="news-getter-sources.opml")
            if method == "POST" and p == "/api/import-opml":
                root = ET.fromstring(self.body())
                n = 0
                pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM sources").fetchone()[0]
                for o in root.iter("outline"):
                    if o.get("xmlUrl"):
                        lang = o.get("language") if o.get("language") in ("de", "en") else "en"
                        cur = c.execute("INSERT OR IGNORE INTO sources(name,feed,lang,category,position) VALUES(?,?,?,?,?)",
                                        (o.get("title") or o.get("text") or o.get("xmlUrl"), o.get("xmlUrl"), lang,
                                         o.get("category") or "", pos + n))
                        n += cur.rowcount
                c.commit()
                threading.Thread(target=refresh_all, daemon=True).start()
                return self.send({"added": n})
            m = re.fullmatch(r"/api/sources/(\d+)", p)
            if m:
                sid = int(m.group(1))
                if method == "DELETE":
                    c.execute("DELETE FROM items WHERE source_id=?", (sid,))
                    c.execute("DELETE FROM sources WHERE id=?", (sid,))
                    c.commit()
                    return self.send({"ok": True})
                if method == "POST":
                    b = self.json_body()
                    for k in ("name", "category", "lang", "enabled", "paywall_mode"):
                        if k in b:
                            c.execute(f"UPDATE sources SET {k}=? WHERE id=?", (b[k], sid))
                    c.commit()
                    return self.send({"ok": True})
            if method == "GET" and p == "/api/stats":
                week_ago = int(time.time()) - 7 * 86400
                flt = " AND NOT muted(i.title, i.tags)" + (" AND " + NOPAY_SQL if q.get("nopay") else "")
                out = []
                for s in c.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY position, id").fetchall():
                    if q.get("lang") and s["lang"] != q["lang"]:
                        continue
                    base = "FROM items i JOIN sources s ON s.id=i.source_id WHERE i.source_id=?" + flt
                    last = c.execute("SELECT MAX(i.published) " + base, (s["id"],)).fetchone()[0]
                    rows = c.execute("SELECT i.title, i.tags, i.summary " + base + " AND i.published>=?",
                                     (s["id"], week_ago)).fetchall()
                    out.append({"id": s["id"], "name": s["name"], "lang": s["lang"], "last": last, "week": len(rows),
                                "topics": topics(rows, s["name"]) if rows else [], "error": s["last_error"],
                                "paywall": bool(s["paywall_mode"])})
                return self.send(out)
            if method == "GET" and p == "/api/overview":
                per = min(int(q.get("per", 8)), 50)
                srcs = c.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY position, id").fetchall()
                out = []
                for s in srcs:
                    if q.get("lang") and s["lang"] != q["lang"]:
                        continue
                    rows = c.execute(ITEM_SQL + " WHERE i.source_id=? AND NOT muted(i.title, i.tags)"
                                        + (" AND " + NOPAY_SQL if q.get("nopay") else "")
                                        + " ORDER BY i.published DESC LIMIT ?", (s["id"], per)).fetchall()
                    out.append({"source": {"id": s["id"], "name": s["name"], "lang": s["lang"], "category": s["category"],
                                           "note": s["note"], "error": s["last_error"],
                                           "site": site_url(s)},
                                "items": [item_json(r) for r in rows]})
                return self.send(out)
            if method == "GET" and p == "/api/items":
                where, args = ["s.enabled=1", "NOT muted(i.title, i.tags)"], []
                if q.get("source"):
                    where.append("i.source_id=?"); args.append(int(q["source"]))
                if q.get("lang"):
                    where.append("s.lang=?"); args.append(q["lang"])
                if q.get("unread"):
                    where.append("i.read=0")
                if q.get("nopay"):
                    where.append(NOPAY_SQL)
                if q.get("q"):
                    where.append("(i.title LIKE ? OR i.short_title LIKE ? OR i.tags LIKE ?)"); args += [f"%{q['q']}%"] * 3
                if q.get("sort") == "size":
                    # whole stories, most outlets first; paged by story so none is split across pages
                    rows = c.execute(f"{ITEM_SQL} WHERE {' AND '.join(where)} ORDER BY i.published DESC", args).fetchall()
                    stories = {}
                    for r in rows:
                        stories.setdefault(r["cluster"] or -r["id"], []).append(r)
                    ranked = sorted(stories.values(), key=lambda g: (-len({r["source_id"] for r in g}), -len(g), -g[0]["published"]))
                    off, lim, out, k = int(q.get("offset", 0)), min(int(q.get("limit", 60)), 200), [], int(q.get("offset", 0))
                    while k < len(ranked) and len(out) < lim:
                        out += [item_json(r) for r in ranked[k]]
                        k += 1
                    return self.send({"items": out, "next": k if k < len(ranked) else None})
                sql = f"{ITEM_SQL} WHERE {' AND '.join(where)} ORDER BY i.published DESC LIMIT ? OFFSET ?"
                rows = c.execute(sql, args + [min(int(q.get("limit", 60)), 200), int(q.get("offset", 0))]).fetchall()
                return self.send([item_json(r) for r in rows])
            m = re.fullmatch(r"/api/item/(\d+)", p)
            if method == "GET" and m:
                r = c.execute(ITEM_SQL + " WHERE i.id=?", (int(m.group(1)),)).fetchone()
                if not r:
                    return self.send({"error": "not found"}, 404)
                c.execute("UPDATE items SET read=1 WHERE id=?", (r["id"],))
                c.commit()
                body = re.sub(r"<img\b[^>]*>", "", r["content"] or r["summary"] or "")
                return self.send({**item_json(r), "html": body, "full": bool(r["content"]) and len(r["content"]) > 1500})
            self.send({"error": "not found"}, 404)
        except Exception as e:
            self.send({"error": str(e)}, 500)
        finally:
            c.close()

    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_DELETE(self): self.route("DELETE")


if __name__ == "__main__":
    init_db()
    _c = db()
    recluster(_c)
    _c.close()
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"news-getter on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

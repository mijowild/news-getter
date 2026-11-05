# news-getter

**Your feed, your rules.**

Most news comes through feeds ranked by someone else's idea of what should hold your attention: engagement, outrage, whatever keeps you scrolling. news-getter is the opposite. You pick the sources and their order. It shows short headlines and nothing else: no ranking algorithm, no recommendations, no images, no notifications, no account, no tracking.

It runs on your own machine, reads the RSS/Atom feeds of independent outlets and blogs (German and English), and stores everything in a single local SQLite file. When several outlets report the same story, it folds them into one row, so a day of news is a list of stories, not 40 copies of the same headline.

Note: Youtube's feed per channel can be added as well. Just add:
```https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxxxxxxxxxxxxxxxx```
Find the channel ID: on the channel page, open the description or "About" and look for "Share channel" --> "Copy channel ID". 

Python 3 standard library only. No dependencies, no build step, one HTML file for the front end.

## Run

    python3 server.py        # then open http://127.0.0.1:8765

- Listens on `127.0.0.1` only, so only your machine can reach it.
- `PORT=9000 python3 server.py` changes the port.
- Feeds refresh on start and then every 30 minutes. The ↻ button fetches immediately.
- After updating the code, restart the server and hard-refresh the browser (Cmd+Shift+R).

Add your feeds in the sources view or import your own OPML file.

## What you get

### Three ways to look at the news
- **Overview** (default): one card per source, in your order, with when it last published, how many articles came in over 7 days, and up to three recurring topics. Quiet sources are dimmed and failing feeds are outlined. Click a card to see that source's articles.
- **By source**: your sources in your order, each with its latest 8 headlines.
- **Timeline**: everything merged by date. Two switches change how it reads:
  - **Group stories** folds headlines about the same story into one row: the newest headline plus a "+N more · M sources" pill that unfolds the rest.
  - **Most covered** sorts stories by how many different outlets report them, so the biggest stories come first.

### Reading
- Click a headline to expand it in place (feed text, tags, no images). Click again or press Esc to close. The ↗ opens the original.
- Opened items are dimmed. **Unread** shows only the ones you haven't opened.
- Search headlines and tags, and filter by language (DE + EN, Deutsch, English).
- Headlines are the publisher's own, trimmed (trailing "| Sitename" removed, capped at about 90 characters). Hover to see the original.
- Images are never loaded. They are stripped from article text on the server.

### Taking control
- **Order and enable**: drag sources into your order, switch them on or off, add one by feed URL or remove it.
- **Muted words**: hide items whose headline or feed tags contain a term. `fußball` matches at the start of a word (also "Fußballer"), `*sport` matches anywhere ("Transport"). Click a tag in an expanded item to mute it. Nothing is deleted, and you can unmute any time.
- **Hide paywalled**: removes articles behind a paywall, where the feed marks them. Feeds with mixed free and paid content and no marker cannot be told apart.
- **Exclude this source** inside an expanded item switches a source off, with a short Undo.

## How story grouping works

Matching is deliberately simple, offline and explainable:

- Headlines of all stored items are compared on their distinctive words (stop words dropped, words cut to 6 letters so "Midterm" and "Midterms" match).
- Two headlines are linked when they share at least two such words, one of them rare (a name, place or event, not "test" or "neue"), the overlap is strong enough when weighted by rarity, and they are less than 36 hours apart. Links chain, so a story that develops over a day stays together.
- Feed tags are ignored (they are shared by unrelated articles of one source). Identical titles from one source count as a recurring series, not as a story.
- It runs after each refresh and at startup, and is stored per item.

The trade-offs: it won't join German and English versions of a story unless a name is shared, or paraphrases that share no names ("Trump-Klatsche" vs "Wahlen in den USA"). An occasional unrelated item can ride along on a chain. The thresholds are constants at the top of the clustering section in `server.py`.

## Files

| File | Purpose |
|---|---|
| `server.py` | Server: feed fetching, parsing, clustering, storage, JSON API, scheduler |
| `index.html` | Front end (plain HTML/CSS/JS, light and dark mode) |
| `news.db` | Your data (sources, order, items, muted words). Delete it to reset. Not for version control |

## How it works

- Feeds are fetched with a 20-second timeout, 8 in parallel, using conditional requests (ETag / Last-Modified) once a source's homepage URL is known.
- RSS 2.0 and Atom are parsed with the standard library. Malformed XML gets a tolerant second attempt (stray `&`, control characters, leading whitespace).
- Article HTML from feeds is sanitised against an allow-list. Scripts, iframes, forms and images are dropped.
- Each source keeps its newest 300 items.
- Dates in the future are clamped to the fetch time. Items without a date get the fetch time, so they can appear near the top.
- Overview topics are a source's most-used tags of the last 7 days (used at least twice), filled up with words that recur across at least two headlines or teasers.

## API (all JSON, under `/api`)

| Route | Purpose |
|---|---|
| `GET /stats?lang=&nopay=1` | Per-source overview: last article, 7-day count, topics |
| `GET /overview?per=8&lang=&nopay=1` | Sources in order with their latest items |
| `GET /items?limit=&offset=&lang=&unread=1&nopay=1&q=&source=` | Timeline items, newest first, each with its `cluster` id |
| `GET /items?sort=size&…` | Same filters, returns `{items, next}`: whole stories, most outlets first. Pass `next` as `offset` for more |
| `GET /item/<id>` | Item text; marks it read |
| `GET /sources`, `POST /sources` | List or add (`name`, `feed`, `lang`, `category`) |
| `POST /sources/<id>` | Update `name`, `category`, `lang`, `enabled`, `paywall_mode` |
| `DELETE /sources/<id>` | Remove a source and its items |
| `POST /sources/reorder` | `{"ids": [...]}` in the new order |
| `POST /sources/enable-all` | Re-enable all sources |
| `POST /import-opml` | Request body is the OPML file |
| `GET`/`POST /mute` | Muted terms (`{"terms": [...]}`) |
| `POST /refresh`, `GET /status` | Fetch now, refresh state |

## Known limits

- Many feeds provide only an excerpt. For those, use the ↗ link to read the full article. There is no full-text extraction.
- Muted-word matching works on headlines and tags only, not on article text.
- Overview topics are a simple count, not language analysis, so some labels are noisy (feed boilerplate, generic tags). Sources without tags show only recurring words, and sources with very few articles show none.
- Single user, local only. There is no login and it is not meant to be exposed to a network.
- The interface has been tested through the API and a scripted page check, not in an automated browser test.

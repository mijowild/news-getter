# news-getter

**Your feed, your rules.**

Most news comes through feeds ranked by someone else's idea of what should hold your attention: engagement, outrage, whatever keeps you scrolling. news-getter is the opposite. You pick the sources and their order. It shows short headlines and nothing else: no ranking algorithm, no recommendations, no images, no notifications, no account, no tracking.

It runs on your own machine, reads the RSS/Atom feeds of independent outlets and blogs (German and English), and stores everything in local SQLite files, one per profile. When several outlets report the same story, it folds them into one row, so a day of news is a list of stories, not 40 copies of the same headline.

Python 3 standard library only. No dependencies, no build step, one HTML file for the front end.

## Run

    python3 server.py        # then open http://127.0.0.1:8765

- Listens on `127.0.0.1` only, so only your machine can reach it.
- `PORT=9000 python3 server.py` changes the port.
- Feeds refresh on start and then every 30 minutes. The ↻ button fetches immediately.
- After updating the code, restart the server and hard-refresh the browser (Cmd+Shift+R).

Set your feeds to your taste in the **Sources** screen, or import an OPML or youtube subscription.csv file.

## Profiles

A profile is a separate database with its own sources, articles, muted words, read state and view. Use one for a **broad view of the world**, where many outlets report the same events and the topic map and story grouping shine, and others for **specialised feeds** (say physics), where there are few duplicates and a plain timeline reads better.

- Pick the active profile in the dropdown at the top left (it appears once you have more than one). Everything on screen belongs to that profile. The choice and the last view per profile are remembered; a specialised profile opens on the Timeline, the world profile on Topics.
- **Sources screen:** it has its own "of profile" selector at the top. Choose a profile there and the source list, order, muted words, import and export all switch to it, so you add, enable, reorder and delete sources for that profile only.
- **Create** one under Sources → Profiles: give it a name and optionally an OPML file of sources. If a file `sources-<name>.opml` sits next to `server.py` (like e.g. `sources-physics.opml`), a profile of that name is seeded from it automatically. **Delete** the selected profile with the Delete profile button at the top of the Sources screen.
- `/?profile=physics` opens a profile directly, and `/?profile=physics#sources` opens its Sources screen, so you can bookmark them.
- Your existing data is the profile **World** (`news.db`); nothing to migrate. Other profiles live in `news-<name>.db`.
- All profiles refresh on the same schedule. A source that is in two profiles is fetched twice, and its read state is separate.

## What you get

### Four ways to look at the news
- **Topics** (default): a bubble map of what the news is about, clustered from the last 1 / 3 / 7 days. See [Topic map](#topic-map).
- **Overview**: one card per source, in your order, with when it last published, how many articles came in over 7 days, and up to three recurring topics. Quiet sources are dimmed and failing feeds are outlined. Click a card to see that source's articles.
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
- **Import and export**: *Export OPML* downloads all your sources (in your order, with language and category) as a file you can back up, share or open in any other feed reader. *Import* takes such an OPML file, or a YouTube `subscriptions.csv` (see below).
- **Muted words**: hide items whose headline or feed tags contain a term. `fußball` matches at the start of a word (also "Fußballer"), `*sport` matches anywhere ("Transport"). Click a tag in an expanded item to mute it. Nothing is deleted, and you can unmute any time.
- **Hide paywalled**: removes articles behind a paywall, where the feed marks them. Paywalled items carry a red **paid** tag either way. Per source, the **paywall** checkbox marks everything as paywalled, for feeds that don't flag it. Feeds with mixed free and paid content and no marker cannot be told apart.
- **Exclude this source** inside an expanded item switches a source off, with a short Undo.

## Following YouTube channels

Every YouTube channel has an RSS/Atom feed: `https://www.youtube.com/feeds/videos.xml?channel_id=<ID>` (the ID starts with `UC`; playlists work with `playlist_id=`). Paste it into Sources → Add. Titles, dates and descriptions come through; thumbnails are not loaded. YouTube Shorts (links containing `/shorts/`) are skipped on fetch and never stored. The source filter in the top bar switches between **All sources**, **No YouTube** and **YouTube only** (API: `yt=no` / `yt=only`, accepted by `/stats`, `/overview`, `/items` and `/topics`); it applies to every view, including the topic map.

To import all your subscriptions at once, export them from [Google Takeout](https://takeout.google.com) (YouTube and YouTube Music → only *subscriptions*), unzip, and import `subscriptions.csv` in Sources → Import. Channels land in the category "YouTube". Channels that have been deleted show a 404 in the Sources list; remove them with ✕.

## Topic map

The **Topics** view (the default) shows at one glance what the news is about. It takes the headlines of the last 24 h / 3 days / 7 days and draws each topic as a bubble.

- **Size** is the number of headlines in the topic; a **stronger fill** means more of it appeared in the last 12 hours.
- **Click a bubble** to read its headlines underneath, grouped by sub-story ("midterm · Trump · Democrats" unfolds into the individual stories about it). Articles open in place, like everywhere else.
- **The grey dashed bubble, "Everything else"**, holds the headlines that fit no topic (typically around half: single product tests, one-off blog posts). Click it to browse them grouped by source. It is drawn smaller than its true size so it does not dominate; the exact count is on the bubble.
- The **language, paywall and YouTube filters** and the **search box** apply here too (search dims the map down to topics with matching headlines and hides the grey bubble).

### How the clustering works

Implemented in `build_topics` in `server.py`, served by `GET /api/topics`. Standard library only, no external services; it runs on every request and takes about a second for 3,000 headlines.

1. **Vectors.** Each headline becomes a TF-IDF vector over its words (stemmed to 6 letters; headline words count double, teaser words once). The idf is measured over the window *plus the previous 60 days of headlines*, so everyday vocabulary ("Jahren", "heute") carries no weight, while words that suddenly appear a lot do.
2. **Graph.** Every headline is linked to its closest few neighbours by cosine similarity.
3. **Stories.** Label propagation finds communities in that graph: a group of headlines about the same event. A story needs at least 3 headlines.
4. **Leftovers.** A headline outside every story joins the story it resembles most, if it resembles one at all.
5. **Topics.** Related stories are merged (average-link on their centroids) into broader topics. Stories are kept as the drill-down level.
6. **Labels.** A topic is named by its three heaviest words that recur across its headlines.

Tunables are the `TOPIC_*` constants at the top of that section: `TOPIC_MERGE` and `TOPIC_ATTACH` (lower = fewer, broader topics and a smaller grey bubble, at the cost of vaguer groups), `TOPIC_EDGE` (how similar two headlines must be to be linked) and `TOPIC_MIN` (smallest story).

Limits:
- German and English coverage of the same event is **not** merged (there is no translation), so you get e.g. "Bombendrohungen · Europa" and "bomb · threats · Europe" as two bubbles.
- Recurring series (daily news summaries, live-music streams, puzzles) show up as topics of their own.
- Clustering is by shared vocabulary, not meaning: a topic can occasionally contain a loosely related headline.

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
| `news.db` | The World profile: sources, order, items, muted words. Delete it to reset. Created automatically |
| `news-<name>.db` | One file per additional profile |
| `sources-<name>.opml` | Optional. Seeds a new profile of that name with these sources |

## How it works

- Feeds are fetched with a 20-second timeout, 8 in parallel, using conditional requests (ETag / Last-Modified) once a source's homepage URL is known.
- RSS 2.0 and Atom are parsed with the standard library. Malformed XML gets a tolerant second attempt (stray `&`, control characters, leading whitespace).
- Article HTML from feeds is sanitised against an allow-list. Scripts, iframes, forms and images are dropped.
- Each source keeps its newest 300 items.
- Dates in the future are clamped to the fetch time. Items without a date get the fetch time, so they can appear near the top.
- Overview topics are a source's most-used tags of the last 7 days (used at least twice), filled up with words that recur across at least two headlines or teasers.

## API (all JSON, under `/api`)

Every route acts on one profile, chosen with the `X-Profile` request header or a `?profile=` query parameter (default `world`; unknown names get a 404).

| Route | Purpose |
|---|---|
| `GET /profiles`, `POST /profiles` | List profiles; create one (`{"name", "opml"?}`) |
| `DELETE /profiles/<id>` | Delete a profile and its database (not `world`) |
| `GET /topics?days=3&lang=&nopay=1&yt=` | Topic clusters: `{total, topics[{label, n, sources, fresh, unread, items, stories}], other}` |
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
| `POST /import-opml` | Request body is an OPML file or a YouTube `subscriptions.csv` |
| `GET /export-opml` | Download all sources as OPML |
| `GET`/`POST /mute` | Muted terms (`{"terms": [...]}`) |
| `POST /refresh`, `GET /status` | Fetch now, refresh state |

## Known limits

- Many feeds provide only an excerpt. For those, use the ↗ link to read the full article. There is no full-text extraction.
- Muted-word matching works on headlines and tags only, not on article text.
- Overview topics are a simple count, not language analysis, so some labels are noisy (feed boilerplate, generic tags). Sources without tags show only recurring words, and sources with very few articles show none.
- Single user, local only. There is no login and it is not meant to be exposed to a network.
- The interface has been tested through the API and a scripted page check, not in an automated browser test.

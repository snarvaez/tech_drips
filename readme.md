# Transcript Search

Two pieces live in this repo:

1. **Transcript Search** — a Flask app that stores chunked passages in MongoDB Atlas and searches them together: podcast episodes, YouTube captions, blog posts, documentation, and GitHub source.
2. `podcast_transcripts.py` — download Apple Podcast transcripts when a show publishes one.

Passages live in `TechDrip.passages` (schema version 3). Each document is one bounded chunk, so a long episode or manual page never becomes one growing array.

Stack: **Flask**, **PyMongo**, **Jinja2 / HTML / jQuery**, **Gunicorn + Nginx**, **MongoDB Atlas** (Atlas Search, Vector Search `autoEmbed`, `$rankFusion`).

## Asset types

| `asset.type` | `parent.type` | What ingest writes | A hit opens |
|---|---|---|---|
| `episode` | `podcast` | The MongoDB Podcast, from RSS SRT or Whisper | Spotify or Apple `?t=`, otherwise `/listen` |
| `video` | `channel` | [MongoDB on YouTube](https://www.youtube.com/user/mongodb/videos) captions | `youtube.com/watch?v=…&t=` |
| `post` | `blog` | [MongoDB Blog](https://www.mongodb.com/company/blog), English sitemap | The article URL |
| `doc` | `manual` | Current English books on mongodb.com/docs | The page URL plus the heading anchor |
| `file` | `repository` | One GitHub repo at a time (default org `mongodb-developer`) | The blob URL plus `#L` at the chunk's line |
| `repo` | none | Allowed by the validator. No ingest command writes it | |
| `snippet` | none | Allowed by the validator. No ingest command writes it | |

`episode`, `video`, `post`, `doc`, and `file` always have a parent. `repo` and `snippet` must not. Parent title is copied onto every passage, so a hit does not need `$lookup`.

`text` is required on every passage and is the Voyage `voyage-4` field. `code` is optional. Source files and fenced documentation examples store the sample on `code`, which `passage_code_index` embeds with `voyage-code-4`. A passage with no `code` field is left out of that index. Markdown inside a repository stays on `text` only.

Platform ids (Spotify, Apple, YouTube), blog slug, docs product and heading, and the git path, blob sha, language, and start line live under `attrs`.

```json
{
  "schema_version": 3,
  "asset": {
    "type": "doc",
    "id": "/docs/manual/core/indexes/index-types",
    "title": "Index Types",
    "url": "https://www.mongodb.com/docs/manual/core/indexes/index-types"
  },
  "parent": {
    "type": "manual",
    "id": "manual",
    "title": "Database Manual",
    "url": "https://www.mongodb.com/docs/manual/"
  },
  "chunk_index": 4,
  "text": "Create a compound index. db.collection.createIndex",
  "code": "db.collection.createIndex({ item: 1, stock: 1 })",
  "attrs": {
    "product": "manual",
    "heading": "Create a compound index",
    "anchor": "create-a-compound-index"
  }
}
```

Spoken passages use the same shape with `start_ms` / `end_ms` and no `code`. Prose is packed at about 900 characters, with the section heading prefixed onto each docs or blog chunk. Source is split on top-level symbols, then on windows of about 80 lines with a 10-line overlap, and capped near 3,500 characters. The collection validator rejects `text` or `code` longer than 8,000 characters.

## Search

One query runs three retrievers:

- **Keyword.** Atlas Search on `text`, `asset.title`, `parent.title`, `code`, and `attrs.path`. English analyzer, plus a `lucene.standard` fuzzy multi so a first-letter typo still matches. Highlights come back on the text fields.
- **Meaning.** Vector Search `autoEmbed` on `text` with Voyage `voyage-4`. Atlas embeds at index time and again at query time. The app does not store vectors and does not call Voyage itself.
- **Code.** The same auto-embed path on `code` with `voyage-code-4`, when `passage_code_index` is present.

`$rankFusion` blends keyword (weight 1.0) and `voyage-4` (weight 1.2). Code hits are then folded in with reciprocal rank fusion at weight 0.8, and the list is cut to `SEARCH_LIMIT` (default 10). The page reports the mode, usually `rankFusion+code`. If the cluster has no `$rankFusion`, the app runs the pipelines separately and fuses them itself (`rrf`). Up to `SNIPPETS_PER_EPISODE` passages (default 3) are kept per asset. Assets are grouped under their parent: show, channel, blog, repository, or manual.

## UI

The search page uses the MongoDB Unpacked palette: navy `#001E2B`, leaf green `#00ED64`, and the Outfit, Chakra Petch, and JetBrains Mono fonts.

- Sample chips under the search box run a query.
- **Play** seeks the in-page player. **Watch** opens YouTube at `&t=`. Blog, docs, and source hits open their URL.
- Copy puts the playable link and the passage text on the clipboard.
- **Drip Pack** keeps chosen passages in `localStorage` (`podcast-clip-bag`) and can copy the whole pack.
- `/listen?asset=&t=` plays an episode from `audio_url` when there is no Spotify or Apple timestamp link. `GET /api/clip` returns that asset.

`GET /health` pings Atlas.

## Setup

Atlas needs Vector Search. Automated embeddings are a preview feature. Create a database user and allow your IP under Network Access.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set MONGODB_URI
```

| Variable | Default |
|---|---|
| `MONGODB_DB` | `TechDrip` |
| `MONGODB_COLLECTION` | `passages` |
| `SEARCH_INDEX` | `passage_search_index` |
| `VECTOR_INDEX` | `passage_vector_index` |
| `CODE_VECTOR_INDEX` | `passage_code_index` |
| `EMBEDDING_MODEL` | `voyage-4` |
| `CODE_EMBEDDING_MODEL` | `voyage-code-4` |
| `SEARCH_LIMIT` | `10` |
| `SNIPPETS_PER_EPISODE` | `3` |
| `XAI_API_KEY` | unset; code ingest then writes the grounded paragraph itself |
| `DESCRIBE_MODEL` | `grok-4.5` when `XAI_API_KEY` is set |

Seed the fictional test shows before the first index build when you want the sample chips (`ransomware`, `CRISPR`, `sticky inflation`, `passkeys`) to return something. Automated Embedding's initial sync is faster on a collection that already has documents.

```bash
python -m search_app.seed
```

Dev server:

```bash
flask --app wsgi:app run --debug --port 5000
```

On macOS, port 5000 is often AirPlay Receiver. If bind fails, pick another port. Open the printed URL.

Search API — the same grouped JSON the page renders (`groups` → `assets` → `passages`, with `href` on each passage):

```bash
curl -sS 'http://127.0.0.1:5000/api/search?q=vector+search'
curl -sS -X POST http://127.0.0.1:5000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"q":"vector search"}'
```

`q` and `query` are accepted as a query parameter or a JSON field.

### Indexes

| Name | Type | Role |
|---|---|---|
| `passage_search_index` | Atlas Search | Lexical search, highlights, fuzzy multi-fields, `code`, `attrs.path` |
| `passage_vector_index` | Vector Search `autoEmbed` | `voyage-4` on `text` |
| `passage_code_index` | Vector Search `autoEmbed` | `voyage-code-4` on `code` |
| `asset_chunk` | Unique | `(asset.type, asset.id, chunk_index)` |
| `asset_id` | Classic | Clip lookup |
| `parent_published` | Classic, partial | List by parent |
| `asset_type_published` | Classic | List by asset type |

`python -m search_app.seed` and `python -m search_app.code_ingest` create or update these search indexes. Blog, docs, podcast, and YouTube ingest write passages and, for blog and docs, create the collection if it is missing. They expect the indexes to exist already.

`python -m search_app.migrate_schema` renames an existing `podcasts` collection to `passages` when needed, rewrites each document to schema 3, and rebuilds the indexes:

```bash
python -m search_app.migrate_schema
```

## Ingest

Each command is safe to re-run. Finished assets are skipped unless `--force` is set.

### The MongoDB Podcast

```bash
python -m search_app.ingest
python -m search_app.ingest --transcribe
```

Reads the RSS feed for [The MongoDB Podcast](https://podcasts.apple.com/us/podcast/the-mongodb-podcast/id1500452446) and stores Spotify `podcast:transcript` SRT files as timed chunks (`asset.type: episode`, `parent.id: mongodb-podcast`). `--transcribe` fills episodes that have no SRT, on Apple Silicon, with ffmpeg and mlx-whisper.

```bash
python -m search_app.enrich
```

Backfills Spotify and Apple episode ids so share links can use `?t=`.

### YouTube

```bash
python -m search_app.youtube_ingest
python -m search_app.youtube_ingest --max-new 40 --delay 8
```

Uses `yt-dlp` to list the MongoDB channel and timed English captions (`asset.type: video`, `parent.id: mongodb-youtube`). Captions only; no audio download. YouTube blocks datacenter IPs and bursts, so run this from a residential network.

Overnight loop (skips finished videos; on an IP block, waits 15 minutes, then 30, up to 6 hours):

```bash
python -m search_app.youtube_loop --max-new 40 --delay 8 --batch-pause 600
```

Ctrl+C stops the loop.

### Blog

```bash
python -m search_app.blog_ingest
```

Reads https://www.mongodb.com/sitemap-blog-pages.xml and keeps English posts (`asset.type: post`, `parent.id: mongodb-blog`). A post already stored at that sitemap `lastmod` is skipped. `--max-new` caps new posts. `--delay` defaults to 0.4 seconds. Hits open the article.

### Documentation

```bash
python -m search_app.docs_ingest --product manual
python -m search_app.docs_ingest --product ''
```

Discovers pages from https://www.mongodb.com/docs/sitemap-index-full.xml and fetches each page's official Markdown (`path + .md`). Current English books only. Versioned paths such as `/v7.0/` and sitemap URLs with query strings are skipped. `--product manual` is the Database Manual. An empty `--product` walks every current book.

Prose chunks go on `text` (`voyage-4`). Fenced examples go on `code` (`voyage-code-4`) with a short `text` label of the heading and the first identifier. There is no model rewrite. Hits open the docs page at the heading anchor. `--delay` defaults to 0.2 seconds.

### GitHub

```bash
python -m search_app.code_ingest --repo typescript-multiplayer-gaming-example
```

Clones one repo from `mongodb-developer` (override with `--org`). Markdown is stored as written on `text`. Source files (the languages in `SOURCE_EXT`) are stored on `code`, and the first chunk's `text` is one grounded paragraph: the path, identifiers that occur in the file, and the README lead. Set `XAI_API_KEY` to have Grok write that paragraph, at most 80 words, from the file plus the README only. A paragraph that names none of the file's identifiers is discarded. Re-runs skip a file whose `attrs.ref` still matches the git blob sha. Hits open the file on GitHub at the chunk's line. Lockfiles, `node_modules`, and files over 200 KB are skipped.

## Deploy

```bash
gunicorn --config gunicorn.conf.py wsgi:app
```

Gunicorn binds `127.0.0.1:8000`. `deploy/nginx.conf` reverse-proxies port 80 to that process. `deploy/podcast-search.service` is a systemd unit. Point `EnvironmentFile` and `WorkingDirectory` at the deploy path, and keep `MONGODB_URI` in `.env`.

Each Gunicorn worker owns one `MongoClient` (created after fork; `preload_app = False`). Pool settings are in `search_app/db.py`.

## Tests

No Atlas required:

```bash
python -m pytest
```

## Apple Podcast downloader

`podcast_transcripts.py` is the original downloader. It expects `requests` and `feedparser` (and Whisper / ffmpeg if you transcribe audio). Transcript availability varies by show.

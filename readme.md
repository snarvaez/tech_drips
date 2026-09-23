# Apple Podcast transcripts

Two pieces live in this repo:

1. `podcast_transcripts.py` — download / transcribe Apple Podcast episodes when a transcript exists.
2. **Transcript Search** — a Flask + PyMongo app that stores test transcripts in MongoDB Atlas, indexes them with Atlas Search and Voyage AI auto-embeddings, and lets you search by topic.

## Transcript Search

Stack: **Flask**, **PyMongo**, **Jinja2 / HTML / jQuery**, **Gunicorn + Nginx**, **MongoDB Atlas**.

Search is hybrid:

- **Keyword:** Atlas Search on `text`, `asset.title`, and `parent.title` (English analyzer, fuzzy, highlights).
- **Semantic:** MongoDB Vector Search `autoEmbed` on `text` with the Voyage AI `voyage-4` model. Atlas generates embeddings at index time and again at query time, so the app never stores vectors or calls Voyage directly.

### Data model

Episode transcripts are unbounded, so they are **not** stored as a growing array on a show document (that would blow past the 16MB BSON limit and wreck the working set). Each searchable passage is its own small document in `TechDrip.passages`. `asset` is the episode, video, repo, or snippet. `parent` is present only for an episode (podcast) or a video (channel). A repo or a code snippet has no parent. Show and channel titles are copied onto each passage so a search hit does not need `$lookup`.

```json
{
  "schema_version": 3,
  "asset": {
    "type": "episode",
    "id": "sn-1043",
    "title": "Double Extortion Hits Regional Hospitals",
    "url": "https://twit.tv/shows/security-now"
  },
  "parent": {
    "type": "podcast",
    "id": "security-now",
    "title": "Security Now",
    "author": "Steve Gibson"
  },
  "published_at": { "$date": "2025-03-18T00:00:00Z" },
  "chunk_index": 0,
  "text": "Ransomware crews no longer just encrypt file servers..."
}
```

A YouTube video uses `asset.type: "video"` and `parent.type: "channel"`. A blog post uses `asset.type: "post"` and `parent.type: "blog"`. A GitHub repo (`repo`) or a code snippet (`snippet`) omits `parent`. Platform ids (Spotify, Apple, YouTube) and blog slug, channel, and sitemap date live under `attrs`.

Indexes:

| Name | Type | Purpose |
|------|------|---------|
| `passage_search_index` | Atlas Search | Lexical search + highlighting + fuzzy multi-fields |
| `passage_vector_index` | Vector Search `autoEmbed` | Voyage `voyage-4` embeddings on `text` |
| `asset_chunk` | Classic unique | `(asset.type, asset.id, chunk_index)` |
| `asset_id` | Classic | Clip lookup by asset id |
| `parent_published` | Classic | Listing by show or channel |
| `asset_type_published` | Classic | Listing by asset type |

Existing `podcasts` documents move to `passages` with:

```bash
python -m search_app.migrate_schema
```

### Setup

Atlas needs Vector Search (automated embeddings is a preview feature on Atlas). Use a database user and allow your IP under Network Access.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set MONGODB_URI
```

Seed test transcripts **before** the first index build when you can — Automated Embedding’s initial sync is faster on a prepopulated collection:

```bash
python -m search_app.seed
```

Dev server:

```bash
flask --app wsgi:app run --debug --port 5000
```

Open http://127.0.0.1:5000 and search for topics such as `ransomware`, `CRISPR`, `sticky inflation`, or `passkeys`. Matches are grouped under their show, channel, or top-level asset.

Health check: `GET /health`.

Search API — same grouped JSON the page renders (`groups` → `assets` → `passages`, with the playable `href` on each passage):

```bash
curl -sS 'http://127.0.0.1:5000/api/search?q=ransomware'
curl -sS -X POST http://127.0.0.1:5000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"q":"ransomware"}'
```

`q` and `query` are accepted as a query parameter or a JSON field.

### Gunicorn + Nginx

```bash
gunicorn --config gunicorn.conf.py wsgi:app
```

Gunicorn binds `127.0.0.1:8000`. `deploy/nginx.conf` reverse-proxies port 80 to that process; `deploy/podcast-search.service` is a systemd unit. Point `EnvironmentFile` and `WorkingDirectory` at the deploy path and keep `MONGODB_URI` in `.env` (never in git).

Each Gunicorn worker process owns one `MongoClient` (created after fork; `preload_app = False`). Pool settings are in `search_app/db.py`.

### Ingest The MongoDB Podcast

```bash
python -m search_app.ingest
```

Pulls the RSS feed for [The MongoDB Podcast](https://podcasts.apple.com/us/podcast/the-mongodb-podcast/id1500452446) and stores Spotify `podcast:transcript` SRT files as chunked documents. Episodes without an SRT can be transcribed on Apple Silicon (ffmpeg + mlx-whisper):

```bash
python -m search_app.ingest --transcribe
```

YouTube (MongoDB channel captions, same chunk + auto-embed + timestamp flow):

```bash
python -m search_app.youtube_ingest
```

Uses `yt-dlp` to list https://www.youtube.com/user/mongodb and timed English captions. Share links are `https://www.youtube.com/watch?v=ID&t=123s`.

YouTube blocks datacenter IPs and bursts. Run from a home/residential network, captions only (no audio download), in small daily batches:

```bash
python -m search_app.youtube_ingest --max-new 40 --delay 8
```

To keep going overnight (skips finished videos; exponential backoff on IP block: 15 min → 30 min → … cap 6 h):

```bash
python -m search_app.youtube_loop --max-new 40 --delay 8 --batch-pause 600
```

Ctrl+C stops the loop. Already-ingested videos are skipped.

MongoDB Blog (same passage collection, chunk size, and hybrid ranker). Hits open the article:

```bash
python -m search_app.blog_ingest
```

Reads the English catalog at https://www.mongodb.com/sitemap-blog-pages.xml. Posts already stored at that sitemap `lastmod` are skipped. `--max-new` caps how many new posts to write; `--delay` defaults to 0.4 seconds between fetches.

GitHub source (one repository at a time). Markdown stays on the `voyage-4` index. Source is embedded with `voyage-code-4` on the `code` field. A grounded paragraph on each source file is what a use-case query matches:

```bash
python -m search_app.code_ingest --repo typescript-multiplayer-gaming-example
```

Hits open the file on GitHub at the chunk's line. Set `XAI_API_KEY` to write those paragraphs with Grok; without it, the paragraph is the file path, identifiers that occur in the file, and the README's opening description.

### Tests that do not need Atlas

```bash
python -m pytest tests/test_search.py
```

---

The original downloader still expects `pip install requests feedparser` (and Whisper / ffmpeg if you transcribe audio). Transcript availability varies by show.

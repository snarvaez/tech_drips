# Tech Drips

Tech Drips is a searchable store of technical content. Podcasts, YouTube videos, blog posts, documentation, and source code are cut into small passages and kept in one MongoDB collection, `TechDrip.passages`. A hybrid search page ranks those passages together.

The first sources checked in are MongoDB's own podcast, YouTube channel, blog, documentation, and developer repositories. The passage model is not tied to one publisher. A new show, channel, site, or repository is another asset with the same fields. `code_ingest` already takes any GitHub `--org` and `--repo`. `youtube_ingest --url` takes one video URL. The other commands read the MongoDB feed, channel, or sitemap named in that module.

## Search

Stack: **Flask**, **PyMongo**, **Jinja2 / HTML / jQuery**, **Gunicorn + Nginx**, **MongoDB Atlas**.

Search is hybrid:

- **Keyword:** Atlas Search on `text`, `asset.title`, `parent.title`, and `code` (English analyzer, fuzzy, highlights).
- **Semantic:** MongoDB Vector Search `autoEmbed` on `text` with Voyage `voyage-4`. Atlas embeds at index time and at query time. The app does not store vectors or call Voyage itself.
- **Code:** a second auto-embed index on `code` with Voyage `voyage-code-4`. Prose stays on `text`. A file with no `code` field is not embedded there.

Hits are grouped under their parent (a podcast, channel, blog, repository, or manual). A saved set of passages is a Drip Pack in the page. A podcast or YouTube hit opens at that passage's timestamp. A post, doc, or file hit opens the article, heading, or source line.

### Data model

A transcript, page, or file is unbounded, so it is stored as many small documents instead of one growing array. Each document is one passage. `asset` is the episode, video, post, file, doc, repo, or snippet. `parent` is the container, when there is one. A repo or a snippet has no parent. The parent title is copied onto the passage, so a hit does not need `$lookup`.

| `asset.type` | `parent.type` | What a passage is |
|---|---|---|
| `episode` | `podcast` | Timed transcript chunk |
| `video` | `channel` | Timed caption chunk |
| `post` | `blog` | Article chunk |
| `doc` | `manual` | Documentation chunk |
| `file` | `repository` | Source file chunk (`text` is a short label, `code` is the source) |
| `repo` | none | A repository with no parent |
| `snippet` | none | A standalone snippet |

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

Platform ids (Spotify, Apple, YouTube), blog slug, doc heading, and the source path live under `attrs`.

Indexes:

| Name | Type | Purpose |
|------|------|---------|
| `passage_search_index` | Atlas Search | Lexical search, highlighting, fuzzy multi-fields |
| `passage_vector_index` | Vector Search `autoEmbed` | Voyage `voyage-4` embeddings on `text`. Filterable by `asset.type`, `asset.id`, `parent.id`, and `parent.type` |
| `passage_code_index` | Vector Search `autoEmbed` | Voyage `voyage-code-4` embeddings on `code` |
| `asset_chunk` | Classic unique | `(asset.type, asset.id, chunk_index)` |
| `asset_id` | Classic | Clip lookup by asset id |
| `parent_published` | Classic | Listing by parent |
| `asset_type_published` | Classic | Listing by asset type |

`python -m search_app.seed` and `python -m search_app.migrate_schema` create or update these definitions. An index added on the cluster has to be added to `search_app/indexes.py`, or the next setup run will drop it.

Existing `podcasts` documents move to `passages` with:

```bash
python -m search_app.migrate_schema
```

### Setup

Atlas needs Vector Search. Automated embeddings are a preview feature. Use a database user and allow your IP under Network Access.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set MONGODB_URI
```

Seed sample passages before the first index build when you can. Automated embedding's initial sync is faster on a collection that already has documents:

```bash
python -m search_app.seed
```

Dev server:

```bash
flask --app wsgi:app run --debug --port 5000
```

Open http://127.0.0.1:5000 and search. Matches are grouped under their show, channel, blog, manual, or repository.

Health check: `GET /health`.

Search API — the same grouped JSON the page renders (`groups` → `assets` → `passages`, with a playable `href` on each passage):

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

## Ingest

Each ingest skips content it already treats as current. `--force` writes it again. Within an asset that is rewritten, extra trailing chunks are deleted. An asset that disappears from the source is left in the collection. Podcast, YouTube, and blog walk newest first, so `--max-new` takes the latest items that are not already stored.

### Podcast

```bash
python -m search_app.podcast_ingest
python -m search_app.podcast_ingest --transcribe
```

Reads the RSS feed named in `search_app/rss.py` (The MongoDB Podcast, newest episode first) and stores `podcast:transcript` SRT files as timed chunks. `--transcribe` covers episodes that have no SRT, using ffmpeg and mlx-whisper on Apple Silicon. An episode that already has a timed chunk is skipped, including when the SRT later changes.

### YouTube

```bash
python -m search_app.youtube_ingest
python -m search_app.youtube_ingest --max-new 40 --delay 8
python -m search_app.youtube_ingest --url 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The catalog walk lists the channel in `search_app/youtube.py` (MongoDB on YouTube) and pulls timed English captions with `yt-dlp`, newest video first. `--url` ingests that video only. Watch, `youtu.be`, embed, shorts, and live links all work. A video already stored complete is skipped unless you pass `--force`. Share links are `https://www.youtube.com/watch?v=ID&t=123s`.

YouTube blocks datacenter IPs and bursts. Run from a home network, captions only, in small batches. To keep going overnight (skips finished videos; backoff on an IP block runs from 15 minutes up to 6 hours):

```bash
python -m search_app.youtube_loop --max-new 40 --delay 8 --batch-pause 600
```

Ctrl+C stops the loop.

### Blog

```bash
python -m search_app.blog_ingest
```

Reads the sitemap named in `search_app/blog.py` (the English MongoDB Blog) and walks it newest `lastmod` first. A post is skipped when its chunks are complete and already at that sitemap date. `--max-new` caps how many posts to write. `--delay` defaults to 0.4 seconds between fetches. Hits open the article.

### Documentation

```bash
python -m search_app.docs_ingest --product manual
```

Reads the sitemap index in `search_app/docs.py` (current English MongoDB docs; old versioned manuals are skipped). `--product manual` is the Database Manual. `--product ''` walks every current book. Each page is fetched as Markdown. Prose uses `voyage-4`. Fenced examples use `voyage-code-4`. A page is skipped when chunk 0 already has that sitemap date. Hits open the page at the section heading.

### Source code

```bash
python -m search_app.code_ingest --org mongodb-developer --repo typescript-multiplayer-gaming-example
```

One repository at a time. `--org` and `--repo` choose the GitHub repository. Markdown stays on the `voyage-4` index. Source is embedded with `voyage-code-4` on `code`. A file whose blob sha is unchanged is skipped. Hits open the file on GitHub at the chunk's line. Set `XAI_API_KEY` to write the grounded paragraph with Grok. Without it, that paragraph is the file path, identifiers that occur in the file, and the README's opening description.

## Tests

```bash
python -m pytest tests/test_search.py
```

These tests do not need Atlas.

`podcast_transcripts.py` is the original episode downloader. It expects `requests` and `feedparser`, and Whisper plus ffmpeg when you transcribe audio. Transcript availability varies by show.

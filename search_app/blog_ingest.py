"""Ingest the MongoDB blog into the passages collection.

Reads https://www.mongodb.com/sitemap-blog-pages.xml and stores each English
article as chunked passages under parent ``mongodb-blog``. Atlas Search and
Voyage auto-embed index ``text`` the same way they index episodes and videos.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
from datetime import datetime, timezone

from pymongo import UpdateOne
from pymongo.collection import Collection

from .blog import (
    BLOG_AUTHOR,
    BLOG_ID,
    BLOG_TITLE,
    BLOG_URL,
    SITEMAP_URL,
    BlogArticle,
    article_from_html,
    fetch_text,
    parse_sitemap,
    slug_from_url,
)
from .chunking import chunk_sections
from .config import Config
from .indexes import apply_validator, ensure_collection
from .ingest import CHUNK_CHARS, _client, retry_mongo
from .schema import legacy_unset, passage_document, passage_filter

SOURCE = "mongodb-blog"


def upsert_post(
    collection: Collection,
    article: BlogArticle,
    *,
    sitemap_lastmod: str | None,
) -> int:
    chunks = chunk_sections(article.sections, max_chars=CHUNK_CHARS)
    if not chunks:
        return 0
    now = datetime.now(timezone.utc)
    ops = []
    for index, text in enumerate(chunks):
        doc = passage_document(
            asset_type="post",
            asset_id=article.uid,
            title=article.title,
            url=article.url,
            author=article.author,
            parent={
                "type": "blog",
                "id": BLOG_ID,
                "title": BLOG_TITLE,
                "author": BLOG_AUTHOR,
                "url": BLOG_URL,
            },
            chunk_index=index,
            text=text,
            published_at=article.published_at,
            attrs={
                "slug": article.slug,
                "channel": article.channel,
                "locale": article.locale,
                "sitemap_lastmod": sitemap_lastmod,
            },
            ingest_source=SOURCE,
            ingested_at=now,
            ingest_complete=True,
        )
        ops.append(
            UpdateOne(
                passage_filter("post", article.uid, index),
                {"$set": doc, "$unset": legacy_unset()},
                upsert=True,
            )
        )
    collection.bulk_write(ops, ordered=False)
    collection.delete_many(
        {
            **passage_filter("post", article.uid),
            "chunk_index": {"$gte": len(chunks)},
        }
    )
    return len(chunks)


def load_ingested(collection: Collection) -> dict[str, dict]:
    """slug -> indexes, lastmods, and ingest_complete flags already stored."""
    found: dict[str, dict] = {}
    cursor = collection.find(
        {"asset.type": "post"},
        {
            "attrs.slug": 1,
            "attrs.sitemap_lastmod": 1,
            "chunk_index": 1,
            "ingest_complete": 1,
        },
    )
    for doc in cursor:
        slug = ((doc.get("attrs") or {}).get("slug")) or ""
        if not slug:
            continue
        bucket = found.setdefault(
            slug, {"indexes": [], "lastmods": set(), "complete": []}
        )
        bucket["indexes"].append(int(doc.get("chunk_index") or 0))
        bucket["lastmods"].add((doc.get("attrs") or {}).get("sitemap_lastmod") or "")
        bucket["complete"].append(bool(doc.get("ingest_complete")))
    return found


def post_state(bucket: dict | None, lastmod: str | None, *, force: bool) -> str:
    """complete | stale | partial | missing."""
    if force or not bucket:
        return "missing"
    indexes = sorted(bucket["indexes"])
    contiguous = bool(indexes) and indexes == list(range(len(indexes)))
    stored = bucket["lastmods"]
    fresh = bool(lastmod) and "" not in stored and all(item >= lastmod for item in stored)
    if contiguous and fresh and all(bucket["complete"]):
        return "complete"
    if not contiguous:
        return "partial"
    return "stale"


def _fetch_article(url: str) -> str:
    last: Exception | None = None
    for attempt in range(5):
        try:
            return fetch_text(url)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError as exc:
            last = exc
        time.sleep(min(30, 2**attempt))
    assert last is not None
    raise last


def _prepare_collection(client):
    database = client[Config.MONGODB_DB]
    name = Config.MONGODB_COLLECTION
    if name not in database.list_collection_names():
        return ensure_collection(database, name)
    collection = database[name]
    apply_validator(collection)
    return collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max posts to consider (0 = all)")
    parser.add_argument(
        "--max-new",
        type=int,
        default=0,
        help="Stop after this many newly written posts (0 = no cap)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.4,
        help="Seconds to wait between article fetches",
    )
    args = parser.parse_args(argv)

    print(f"Reading {SITEMAP_URL}")
    entries = parse_sitemap(fetch_text(SITEMAP_URL))
    if args.limit:
        entries = entries[: args.limit]
    print(f"Found {len(entries)} blog posts")

    client = _client()
    try:
        collection = _prepare_collection(client)
        ingested_index = {} if args.force else load_ingested(collection)
        ingested = skipped = failed = rewritten = 0
        catalog_total = len(entries)
        for index, entry in enumerate(entries, start=1):
            if args.max_new and ingested >= args.max_new:
                print(f"Reached --max-new={args.max_new}", flush=True)
                break
            state = post_state(
                ingested_index.get(slug_from_url(entry.url)),
                entry.lastmod,
                force=args.force,
            )
            if state == "complete":
                skipped += 1
                if skipped == 1 or skipped % 100 == 0:
                    print(f"... skipped {skipped} already-current posts", flush=True)
                continue
            if state in ("partial", "stale"):
                rewritten += 1
            print(
                f"[{index}/{catalog_total}] fetching {entry.url}",
                flush=True,
            )
            try:
                article = article_from_html(_fetch_article(entry.url), entry.url)
                if article is None:
                    raise ValueError("not a blog_article")
                written = retry_mongo(
                    lambda article=article, lastmod=entry.lastmod: upsert_post(
                        collection, article, sitemap_lastmod=lastmod
                    ),
                    attempts=5,
                    label="upsert",
                )
                if not written:
                    raise ValueError("empty article")
                ingested += 1
                print(
                    f"[{index}/{catalog_total}] {written} chunks  {article.title[:90]}",
                    flush=True,
                )
            except Exception as exc:
                failed += 1
                print(
                    f"[{index}/{catalog_total}] FAIL {entry.url}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(max(0.0, args.delay))
        blog_filter = {"asset.type": "post", "parent.id": BLOG_ID}
        total = collection.count_documents(blog_filter)
        posts = collection.distinct("asset.id", blog_filter)
        print(
            f"Blog done ingested={ingested} rewritten={rewritten} "
            f"skipped={skipped} failed={failed}"
        )
        print(
            f"Atlas {Config.MONGODB_DB}.{collection.name}: "
            f"{len(posts)} blog posts, {total} chunks"
        )
        return 1 if failed and not ingested else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

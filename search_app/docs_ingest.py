"""Ingest current MongoDB documentation into the passages collection.

Fetches the official Markdown for each page. Prose is embedded with
voyage-4. Fenced examples are embedded with voyage-code-4. No model call.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
from datetime import datetime, timezone
from urllib.parse import urlparse

from pymongo import UpdateOne
from pymongo.collection import Collection

from .blog import fetch_text
from .config import Config
from .docs import (
    SITEMAP_INDEX,
    DocPageRef,
    is_current_docs_url,
    manual_title,
    markdown_url,
    parse_page_sitemap,
    parse_sitemap_index,
    passages_from_markdown,
    product_id,
)
from .indexes import apply_validator, ensure_collection
from .ingest import _client, retry_mongo
from .schema import legacy_unset, passage_document, passage_filter

SOURCE = "mongodb-docs"


def _fetch(url: str) -> str:
    last: Exception | None = None
    for attempt in range(4):
        try:
            return fetch_text(url, timeout=45)
        except urllib.error.HTTPError as exc:
            last = exc
            # The docs host sometimes answers 404 for a page that exists.
            if exc.code not in (404, 429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError as exc:
            last = exc
        time.sleep(min(20, 2**attempt))
    assert last is not None
    raise last


def current_sitemaps(index_xml: str, product: str | None) -> list[str]:
    found = []
    for loc in parse_sitemap_index(index_xml):
        if not is_current_docs_url(loc):
            continue
        if product and product_id(loc) != product:
            continue
        found.append(loc)
    return found


def upsert_page(
    collection: Collection,
    page: DocPageRef,
    *,
    title: str,
    passages: list[dict],
) -> int:
    if not passages or not title:
        return 0
    path = urlparse(page.url).path.rstrip("/") or page.url
    parent_id = page.product
    now = datetime.now(timezone.utc)
    ops = []
    for index, passage in enumerate(passages):
        code = passage.get("code")
        if code and len(code) > 8000:
            code = code[:8000]
        text = passage["text"]
        if len(text) > 8000:
            text = text[:8000]
        doc = passage_document(
            asset_type="doc",
            asset_id=path,
            title=title,
            url=page.url.split("#", 1)[0],
            parent={
                "type": "manual",
                "id": parent_id,
                "title": manual_title(parent_id),
                "url": f"https://www.mongodb.com/docs/{parent_id}/",
            },
            chunk_index=index,
            text=text,
            code=code,
            attrs={
                "slug": path,
                "product": parent_id,
                "heading": passage.get("heading"),
                "anchor": passage.get("anchor"),
                "language": passage.get("language"),
                "sitemap_lastmod": page.lastmod,
            },
            ingest_source=SOURCE,
            ingested_at=now,
            ingest_complete=True,
        )
        ops.append(
            UpdateOne(
                passage_filter("doc", path, index),
                {"$set": doc, "$unset": legacy_unset()},
                upsert=True,
            )
        )
    collection.bulk_write(ops, ordered=False)
    collection.delete_many(
        {**passage_filter("doc", path), "chunk_index": {"$gte": len(passages)}}
    )
    return len(passages)


def stored_lastmod(collection: Collection, page_url: str) -> str | None:
    path = urlparse(page_url).path.rstrip("/")
    doc = collection.find_one(
        passage_filter("doc", path, 0),
        {"attrs.sitemap_lastmod": 1},
    )
    if not doc:
        return None
    return ((doc.get("attrs") or {}).get("sitemap_lastmod")) or None


def _fresh(stored: str | None, lastmod: str | None) -> bool:
    return bool(stored and lastmod and stored >= lastmod)


def _prepare(client) -> Collection:
    database = client[Config.MONGODB_DB]
    name = Config.MONGODB_COLLECTION
    if name not in database.list_collection_names():
        return ensure_collection(database, name)
    collection = database[name]
    apply_validator(collection)
    return collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="manual", help="Book id, e.g. manual. Empty string walks every current book.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max pages to consider (0 = all)")
    parser.add_argument("--max-new", type=int, default=0, help="Stop after this many newly written pages (0 = no cap)")
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args(argv)
    product = args.product or None

    print(f"Reading {SITEMAP_INDEX}")
    sitemaps = current_sitemaps(_fetch(SITEMAP_INDEX), product)
    print(f"{len(sitemaps)} current sitemap(s)" + (f" for {product}" if product else ""))
    pages: list[DocPageRef] = []
    for loc in sitemaps:
        book = product_id(loc)
        pages.extend(parse_page_sitemap(_fetch(loc), book))
    if args.limit:
        pages = pages[: args.limit]
    print(f"Found {len(pages)} pages")

    client = _client()
    try:
        collection = _prepare(client)
        ingested = skipped = failed = chunks = 0
        total = len(pages)
        for index, page in enumerate(pages, start=1):
            if args.max_new and ingested >= args.max_new:
                print(f"Reached --max-new={args.max_new}", flush=True)
                break
            if not args.force and _fresh(stored_lastmod(collection, page.url), page.lastmod):
                skipped += 1
                if skipped == 1 or skipped % 100 == 0:
                    print(f"... skipped {skipped} already-current pages", flush=True)
                continue
            print(f"[{index}/{total}] {page.url}", flush=True)
            try:
                markdown = _fetch(markdown_url(page.url))
                title, passages = passages_from_markdown(markdown)
                if not title or not passages:
                    raise ValueError("empty page")
                written = retry_mongo(
                    lambda page=page, title=title, passages=passages: upsert_page(
                        collection, page, title=title, passages=passages
                    ),
                    attempts=5,
                    label="upsert",
                )
                if not written:
                    raise ValueError("empty page")
                ingested += 1
                chunks += written
                print(f"[{index}/{total}] {written} chunks  {title[:90]}", flush=True)
            except Exception as exc:
                failed += 1
                print(f"[{index}/{total}] FAIL {page.url}: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(0.0, args.delay))
        filt = {"asset.type": "doc"}
        if product:
            filt["parent.id"] = product
        stored = collection.count_documents(filt)
        print(f"Docs done ingested={ingested} skipped={skipped} failed={failed} chunks_written={chunks}")
        print(f"Atlas {Config.MONGODB_DB}.{collection.name}: {stored} doc chunks")
        return 1 if failed and not ingested else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

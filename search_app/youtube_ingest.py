"""Ingest MongoDB YouTube captions into the passages collection."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from pymongo import UpdateOne
from pymongo.collection import Collection

from .chunking import chunk_cues
from .config import Config
from .ingest import CHUNK_CHARS, _client, retry_mongo
from .schema import legacy_unset, passage_document, passage_filter
from .youtube import (
    CHANNEL_AUTHOR,
    CHANNEL_ID,
    CHANNEL_KEY,
    CHANNEL_TITLE,
    CHANNEL_URL,
    YoutubeVideo,
    fetch_caption_cues,
    list_channel_videos,
)


def upsert_video(collection: Collection, video: YoutubeVideo, cues, source: str) -> int:
    chunks = chunk_cues(cues, max_chars=CHUNK_CHARS)
    if not chunks:
        return 0
    now = datetime.now(timezone.utc)
    duration = ""
    if video.duration:
        duration = str(int(video.duration))
    ops = []
    for index, chunk in enumerate(chunks):
        doc = passage_document(
            asset_type="video",
            asset_id=video.video_id,
            title=video.title,
            url=f"https://www.youtube.com/watch?v={video.video_id}",
            parent={
                "type": "channel",
                "id": CHANNEL_KEY,
                "title": CHANNEL_TITLE,
                "author": CHANNEL_AUTHOR,
                "url": CHANNEL_URL,
            },
            chunk_index=index,
            text=chunk["text"],
            published_at=video.published_at,
            start_ms=chunk.get("start_ms"),
            end_ms=chunk.get("end_ms"),
            attrs={
                "youtube_video_id": video.video_id,
                "youtube_channel_id": CHANNEL_ID,
                "duration": duration or None,
            },
            ingest_source=source,
            ingested_at=now,
            ingest_complete=True,
        )
        ops.append(
            UpdateOne(
                passage_filter("video", video.video_id, index),
                {"$set": doc, "$unset": legacy_unset()},
                upsert=True,
            )
        )
    collection.bulk_write(ops, ordered=False)
    collection.delete_many(
        {
            **passage_filter("video", video.video_id),
            "chunk_index": {"$gte": len(chunks)},
        }
    )
    return len(chunks)


def youtube_progress(collection: Collection, video_id: str) -> str:
    """complete | missing | partial

    Caption files arrive all at once. A mid-video YouTube block almost always
    means the fetch failed and nothing was written. If a bulk write died after
    some chunks, indexes are not 0..n-1 and we rewrite the whole video.
    """
    docs = list(
        collection.find(
            passage_filter("video", video_id),
            {"chunk_index": 1, "ingest_complete": 1},
        )
    )
    if not docs:
        return "missing"
    if any(doc.get("ingest_complete") for doc in docs):
        return "complete"
    indexes = sorted(int(doc.get("chunk_index") or 0) for doc in docs)
    if indexes and indexes == list(range(len(indexes))):
        return "complete"
    return "partial"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max videos to process (0 = all)")
    parser.add_argument(
        "--max-new",
        type=int,
        default=40,
        help="Stop after this many newly ingested videos (daily-batch default 40)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=8.0,
        help="Seconds to wait between caption fetches",
    )
    args = parser.parse_args(argv)

    print("Listing MongoDB YouTube channel…")
    videos = list_channel_videos(limit=args.limit or None)
    print(f"Found {len(videos)} videos")

    client = _client()
    try:
        collection = client[Config.MONGODB_DB][Config.MONGODB_COLLECTION]
        ingested = skipped = failed = rewritten = 0
        blocked = 0
        rate_limited = False
        hit_batch_cap = False
        catalog_total = len(videos)
        already = 0
        if not args.force:
            already = sum(
                1
                for video in videos
                if youtube_progress(collection, video.video_id) == "complete"
            )
        print(
            f"Progress: {already}/{catalog_total} already in Atlas, "
            f"{catalog_total - already} remaining this catalog.",
            flush=True,
        )
        for i, video in enumerate(videos, start=1):
            if args.max_new and ingested >= args.max_new:
                print(
                    f"Reached --max-new={args.max_new}; "
                    f"catalog {already + ingested}/{catalog_total}.",
                    flush=True,
                )
                hit_batch_cap = True
                break
            state = "missing" if args.force else youtube_progress(collection, video.video_id)
            if state == "complete":
                skipped += 1
                if skipped in (1, already) or skipped % 100 == 0:
                    print(
                        f"... skipped {skipped} already-complete "
                        f"({already}/{catalog_total} in Atlas)",
                        flush=True,
                    )
                continue
            if state == "partial":
                print(
                    f"[{i}/{catalog_total}] incomplete; rewriting {video.title[:80]}",
                    flush=True,
                )
                rewritten += 1
            batch_goal = args.max_new or catalog_total
            print(
                f"[{i}/{catalog_total}] fetching captions: {video.title[:80]}  "
                f"(batch {ingested + 1}/{batch_goal}, "
                f"catalog {already + ingested}/{catalog_total})",
                flush=True,
            )
            try:
                cues = fetch_caption_cues(video.video_id)
                if not cues:
                    raise ValueError("empty captions")
                n = retry_mongo(
                    lambda v=video, c=cues: upsert_video(
                        collection, v, c, "youtube-captions"
                    ),
                    attempts=5,
                    label="upsert",
                )
                ingested += 1
                blocked = 0
                print(
                    f"[{i}/{catalog_total}] done {n} chunks — {video.title[:80]}  "
                    f"catalog {already + ingested}/{catalog_total}  "
                    f"this-batch {ingested}/{batch_goal}",
                    flush=True,
                )
            except Exception as exc:
                failed += 1
                msg = str(exc)
                print(
                    f"[{i}/{catalog_total}] FAIL {video.video_id} {video.title[:50]}: {msg[:180]}",
                    file=sys.stderr,
                    flush=True,
                )
                if any(
                    token in msg.lower()
                    for token in ("429", "too many requests", "ip has been blocked", "ipblocked")
                ):
                    blocked += 1
                    wait = min(300, 30 * blocked)
                    print(f"YouTube rate limit; sleeping {wait}s (consecutive={blocked})", file=sys.stderr)
                    if blocked >= 3:
                        print(
                            "Stopping after repeated IP/rate-limit errors. "
                            "Rerun later from a residential network.",
                            file=sys.stderr,
                        )
                        rate_limited = True
                        break
                    time.sleep(wait)
                    continue
            time.sleep(max(0.0, args.delay))
        channel_filter = {"asset.type": "video", "parent.id": CHANNEL_KEY}
        total = collection.count_documents(channel_filter)
        shows = collection.distinct("asset.id", channel_filter)
        print(
            f"YouTube done ingested={ingested} rewritten={rewritten} "
            f"skipped={skipped} failed={failed}"
        )
        print(
            f"Atlas {Config.MONGODB_DB}.{collection.name}: "
            f"{len(shows)} YouTube videos, {total} chunks"
        )
        if rate_limited:
            print("STATUS rate_limit")
            return 2
        remaining = len(videos) - skipped - ingested
        if not hit_batch_cap and remaining <= 0:
            print("STATUS complete")
            return 0
        print("STATUS batch")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

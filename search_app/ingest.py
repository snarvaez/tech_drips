"""Ingest The MongoDB Podcast into Atlas transcript chunks.

Prefers RSS `podcast:transcript` SRT files. Remaining episodes can be
transcribed from audio with mlx-whisper when --transcribe is set.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.server_api import ServerApi

from .chunking import chunk_cues, chunk_transcript
from .config import Config
from .rss import FEED_URL, Episode, fetch_bytes, fetch_text, parse_feed
from .schema import legacy_unset, passage_document, passage_filter
from .srt import Cue, parse_srt_cues, srt_to_text, whisper_segments_to_cues

APPLE_URL = "https://podcasts.apple.com/us/podcast/the-mongodb-podcast/id1500452446"
SHOW_ID = "mongodb-podcast"
ITUNES_ID = "1500452446"
CHUNK_CHARS = 900


def _client() -> MongoClient:
    if not Config.MONGODB_URI:
        raise SystemExit("Set MONGODB_URI")
    return MongoClient(
        Config.MONGODB_URI,
        server_api=ServerApi("1"),
        maxPoolSize=5,
        minPoolSize=0,
        serverSelectionTimeoutMS=45_000,
        connectTimeoutMS=20_000,
        socketTimeoutMS=60_000,
        retryWrites=True,
        appname="podcast-transcript-ingest",
    )


def retry_mongo(fn, *, attempts: int = 8, label: str = "mongo"):
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            wait = min(5 * i, 30)
            print(f"{label} retry {i}/{attempts} in {wait}s: {exc}", file=sys.stderr)
            time.sleep(wait)
    raise last


def upsert_episode(
    collection: Collection,
    *,
    episode: Episode,
    show_title: str,
    author: str,
    text: str | None = None,
    cues: list[Cue] | None = None,
    source: str,
) -> int:
    timed = chunk_cues(cues, max_chars=CHUNK_CHARS) if cues else []
    if not timed and text:
        timed = [
            {"text": chunk, "start_ms": None, "end_ms": None}
            for chunk in chunk_transcript(text, max_chars=CHUNK_CHARS)
        ]
    if not timed:
        return 0
    ops = []
    now = datetime.now(timezone.utc)
    for index, chunk in enumerate(timed):
        doc = passage_document(
            asset_type="episode",
            asset_id=episode.guid,
            title=episode.title,
            url=episode.link or APPLE_URL,
            parent={
                "type": "podcast",
                "id": SHOW_ID,
                "title": show_title,
                "author": author,
                "url": APPLE_URL,
            },
            chunk_index=index,
            text=chunk["text"],
            audio_url=episode.audio_url or None,
            published_at=episode.published_at,
            start_ms=chunk.get("start_ms"),
            end_ms=chunk.get("end_ms"),
            attrs={
                "itunes_id": ITUNES_ID,
                "duration": episode.duration,
                "spotify_episode_id": episode.spotify_episode_id,
                "anchor_id": episode.anchor_id,
            },
            ingest_source=source,
            ingested_at=now,
        )
        ops.append(
            UpdateOne(
                passage_filter("episode", episode.guid, index),
                {"$set": doc, "$unset": legacy_unset()},
                upsert=True,
            )
        )
    collection.bulk_write(ops, ordered=False)
    collection.delete_many(
        {
            **passage_filter("episode", episode.guid),
            "chunk_index": {"$gte": len(timed)},
        }
    )
    return len(timed)


def has_timed_chunks(collection: Collection, episode_id: str) -> bool:
    def _count():
        return (
            collection.count_documents(
                {
                    **passage_filter("episode", episode_id),
                    "start_ms": {"$gte": 0},
                },
                limit=1,
            )
            > 0
        )

    try:
        return retry_mongo(_count, attempts=5, label="count")
    except Exception as exc:
        print(f"count failed for {episode_id}: {exc}", file=sys.stderr)
        return False


def ingest_srt_episodes(
    collection: Collection,
    episodes: list[Episode],
    show_title: str,
    author: str,
    *,
    force: bool,
) -> tuple[int, int, int]:
    ingested = skipped = failed = 0
    with_srt = [ep for ep in episodes if ep.transcript_url]
    for i, episode in enumerate(with_srt, start=1):
        if not force and has_timed_chunks(collection, episode.guid):
            skipped += 1
            continue
        try:
            srt = fetch_text(episode.transcript_url, timeout=45)
            cues = parse_srt_cues(srt)
            if not cues:
                text = srt_to_text(srt)
                if not text:
                    raise ValueError("empty transcript")
                cues = None
            else:
                text = None
            n = upsert_episode(
                collection,
                episode=episode,
                show_title=show_title,
                author=author,
                text=text,
                cues=cues,
                source="spotify-srt",
            )
            ingested += 1
            print(f"[{i}/{len(with_srt)}] {n} chunks  {episode.title[:90]}")
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(with_srt)}] FAIL {episode.title[:70]}: {exc}", file=sys.stderr)
        time.sleep(0.15)
    return ingested, skipped, failed


def transcribe_audio(path: str, model: str) -> dict:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=model,
        language="en",
        verbose=False,
        word_timestamps=False,
    )
    return result or {}


def ingest_missing_with_whisper(
    collection: Collection,
    episodes: list[Episode],
    show_title: str,
    author: str,
    *,
    force: bool,
    model: str,
    workdir: str,
) -> tuple[int, int, int]:
    import os
    import tempfile

    os.makedirs(workdir, exist_ok=True)
    missing = [ep for ep in episodes if not ep.transcript_url]
    ingested = skipped = failed = 0
    for i, episode in enumerate(missing, start=1):
        if not force and has_timed_chunks(collection, episode.guid):
            skipped += 1
            continue
        if not episode.audio_url:
            failed += 1
            print(f"[{i}/{len(missing)}] no audio  {episode.title[:70]}", file=sys.stderr)
            continue
        import json

        cache_path = os.path.join(workdir, f"{episode.guid}.segments.json")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=workdir)
        tmp.close()
        try:
            cues: list[Cue] = []
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                cues = whisper_segments_to_cues(cached.get("segments") or [])
                print(f"[{i}/{len(missing)}] resume from cache {episode.title[:80]}")
            if not cues:
                print(f"[{i}/{len(missing)}] downloading {episode.title[:80]}")
                audio = fetch_bytes(episode.audio_url, timeout=180)
                with open(tmp.name, "wb") as fh:
                    fh.write(audio)
                print(f"[{i}/{len(missing)}] transcribing {len(audio)} bytes")
                result = transcribe_audio(tmp.name, model)
                cues = whisper_segments_to_cues(result.get("segments") or [])
                if not cues:
                    raise ValueError("empty whisper output")
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "text": result.get("text") or "",
                            "segments": [
                                {
                                    "start": float(seg.get("start") or 0),
                                    "end": float(seg.get("end") or 0),
                                    "text": seg.get("text") or "",
                                }
                                for seg in (result.get("segments") or [])
                            ],
                        },
                        fh,
                    )
            n = retry_mongo(
                lambda: upsert_episode(
                    collection,
                    episode=episode,
                    show_title=show_title,
                    author=author,
                    cues=cues,
                    source=f"whisper:{model}",
                ),
                attempts=8,
                label="upsert",
            )
            ingested += 1
            print(f"[{i}/{len(missing)}] {n} chunks  {episode.title[:90]}")
            time.sleep(0.5)
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(missing)}] FAIL {episode.title[:70]}: {exc}", file=sys.stderr)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    return ingested, skipped, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="Transcribe episodes that have no RSS transcript (requires ffmpeg + mlx-whisper)",
    )
    parser.add_argument(
        "--whisper-model",
        default="mlx-community/whisper-tiny",
    )
    parser.add_argument("--workdir", default="/tmp/mongodb-podcast-audio")
    args = parser.parse_args(argv)

    print(f"Fetching feed {FEED_URL}")
    xml_bytes = fetch_bytes(FEED_URL)
    show_title, author, episodes = parse_feed(xml_bytes)
    with_srt = sum(1 for ep in episodes if ep.transcript_url)
    print(f"{show_title}: {len(episodes)} episodes, {with_srt} with SRT")

    client = _client()
    try:
        collection = client[Config.MONGODB_DB][Config.MONGODB_COLLECTION]
        ingested, skipped, failed = ingest_srt_episodes(
            collection, episodes, show_title, author, force=args.force
        )
        print(f"SRT done ingested={ingested} skipped={skipped} failed={failed}")
        if args.transcribe:
            w_in, w_skip, w_fail = ingest_missing_with_whisper(
                collection,
                episodes,
                show_title,
                author,
                force=args.force,
                model=args.whisper_model,
                workdir=args.workdir,
            )
            print(f"Whisper done ingested={w_in} skipped={w_skip} failed={w_fail}")
            failed += w_fail
        show_filter = {"asset.type": "episode", "parent.id": SHOW_ID}
        total = collection.count_documents(show_filter)
        shows = collection.distinct("asset.id", show_filter)
        print(f"Atlas {Config.MONGODB_DB}.{collection.name}: {len(shows)} episodes, {total} chunks")
        return 1 if failed else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

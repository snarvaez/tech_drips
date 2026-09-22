"""Backfill Spotify / Apple episode IDs onto existing transcript chunks."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

from pymongo import MongoClient
from pymongo.server_api import ServerApi

from .config import Config
from .rss import FEED_URL, fetch_bytes, parse_feed
from .share import (
    APPLE_COLLECTION_ID,
    anchor_id_from_episode_url,
    spotify_episode_id_from_transcript_url,
)


def _norm(title: str) -> str:
    text = (title or "").replace("\u2028", " ").replace("\u2029", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _itunes_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "podcast-transcript-search/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def apple_episodes() -> list[dict]:
    lookup = _itunes_json(
        "https://itunes.apple.com/lookup?id="
        f"{APPLE_COLLECTION_ID}&entity=podcastEpisode&limit=200"
    )
    found = [
        r
        for r in lookup.get("results") or []
        if r.get("kind") == "podcast-episode"
    ]
    return found


def apple_search(title: str) -> dict | None:
    term = f"MongoDB {title}"[:80]
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {
            "term": term,
            "entity": "podcastEpisode",
            "limit": 8,
            "country": "US",
        }
    )
    data = _itunes_json(url)
    want = _norm(title)
    for row in data.get("results") or []:
        if str(row.get("collectionId")) != str(APPLE_COLLECTION_ID):
            continue
        if _norm(row.get("trackName") or "") == want:
            return row
    for row in data.get("results") or []:
        if str(row.get("collectionId")) != str(APPLE_COLLECTION_ID):
            continue
        name = _norm(row.get("trackName") or "")
        if want and (want in name or name in want):
            return row
    return None


def main() -> int:
    if not Config.MONGODB_URI:
        print("Set MONGODB_URI", file=sys.stderr)
        return 1
    print("Fetching RSS")
    _, _, episodes = parse_feed(fetch_bytes(FEED_URL))
    by_guid = {ep.guid: ep for ep in episodes}
    print(f"RSS episodes {len(by_guid)}")

    print("Fetching Apple Podcasts catalog")
    apple_rows = apple_episodes()
    apple_by_title = {_norm(r.get("trackName") or ""): r for r in apple_rows}
    print(f"Apple lookup {len(apple_by_title)}")

    client = MongoClient(
        Config.MONGODB_URI,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=20_000,
        appname="podcast-share-enrich",
    )
    try:
        coll = client[Config.MONGODB_DB][Config.MONGODB_COLLECTION]
        show = {"asset.type": "episode", "parent.id": "mongodb-podcast"}
        episode_ids = coll.distinct("asset.id", show)
        print(f"Mongo episodes {len(episode_ids)}")
        updated = 0
        missing_apple = []
        for eid in episode_ids:
            rss_ep = by_guid.get(eid)
            spotify_id = None
            apple_id = None
            title = None
            if rss_ep:
                title = rss_ep.title
                spotify_id = spotify_episode_id_from_transcript_url(rss_ep.transcript_url)
            sample = coll.find_one(
                {"asset.type": "episode", "asset.id": eid},
                {"asset.title": 1, "asset.url": 1, "attrs": 1},
            )
            asset = (sample or {}).get("asset") or {}
            title = title or asset.get("title")
            apple_row = apple_by_title.get(_norm(title or ""))
            if not apple_row and title:
                missing_apple.append((eid, title))
            else:
                apple_id = (apple_row or {}).get("trackId")
            fields = {
                "attrs.anchor_id": anchor_id_from_episode_url(
                    (rss_ep.link if rss_ep else None) or asset.get("url")
                ),
                "attrs.itunes_id": APPLE_COLLECTION_ID,
            }
            if spotify_id:
                fields["attrs.spotify_episode_id"] = spotify_id
            if apple_id:
                fields["attrs.apple_track_id"] = str(apple_id)
            result = coll.update_many(
                {"asset.type": "episode", "asset.id": eid},
                {"$set": fields},
            )
            updated += result.modified_count

        print(f"Need Apple search for {len(missing_apple)} titles")
        for i, (eid, title) in enumerate(missing_apple, start=1):
            try:
                row = apple_search(title)
            except Exception as exc:
                print(f"  apple search fail {title[:50]}: {exc}", file=sys.stderr)
                row = None
            if row and row.get("trackId"):
                coll.update_many(
                    {"asset.type": "episode", "asset.id": eid},
                    {"$set": {"attrs.apple_track_id": str(row["trackId"])}},
                )
                print(f"  [{i}/{len(missing_apple)}] apple {row['trackId']} {title[:60]}")
            else:
                print(f"  [{i}/{len(missing_apple)}] no apple id {title[:60]}")
            time.sleep(0.15)

        with_spotify = coll.distinct(
            "asset.id",
            {**show, "attrs.spotify_episode_id": {"$exists": True}},
        )
        with_apple = coll.distinct(
            "asset.id",
            {**show, "attrs.apple_track_id": {"$exists": True}},
        )
        print(
            f"Done. chunks touched ~{updated}. "
            f"episodes with Spotify id {len(with_spotify)}, Apple id {len(with_apple)}"
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

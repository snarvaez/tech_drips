"""Native timestamp URLs for Spotify and Apple Podcasts.

Spotify honors ?t=<seconds> on open.spotify.com/episode/{id}.
Apple Podcasts honors ?t=<seconds> on podcasts.apple.com episode links
(opened in the Podcasts app / website; support varies by client).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

SPOTIFY_SHOW_ID = "0ibUtrJG4JVgwfvB2MXMSb"
APPLE_COLLECTION_ID = "1500452446"


def seconds_from_ms(start_ms: Any) -> int:
    try:
        ms = float(start_ms or 0)
    except (TypeError, ValueError):
        return 0
    if ms > 100_000_000:
        ms = ms / 1
    return max(0, int(ms // 1000))


def spotify_episode_id_from_transcript_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path.strip("/").split("/")
    # /{showId}/{episodeId}/transcript.srt
    if len(path) >= 2 and path[0] and path[1] not in {"transcript.srt", "transcript"}:
        return path[1]
    return None


def anchor_id_from_episode_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"-(e[0-9a-z]+)/?$", urlparse(url).path, re.I)
    return match.group(1) if match else None


def with_time_param(url: str, seconds: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("uo", None)
    query["t"] = str(max(0, int(seconds)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def spotify_listen_url(episode_id: str, start_ms: Any) -> str:
    return with_time_param(
        f"https://open.spotify.com/episode/{episode_id}",
        seconds_from_ms(start_ms),
    )


def apple_listen_url(track_id: str | int, start_ms: Any, collection_id: str | None = None) -> str:
    cid = collection_id or APPLE_COLLECTION_ID
    return with_time_param(
        f"https://podcasts.apple.com/us/podcast/id{cid}?i={track_id}",
        seconds_from_ms(start_ms),
    )


def youtube_listen_url(video_id: str, start_ms: Any) -> str:
    seconds = seconds_from_ms(start_ms)
    return f"https://www.youtube.com/watch?v={video_id}&t={seconds}s"


def _stored(doc: dict[str, Any], key: str) -> Any:
    if doc.get(key):
        return doc[key]
    attrs = doc.get("attrs") or {}
    if attrs.get(key):
        return attrs[key]
    return None


def primary_share_url(doc: dict[str, Any], start_ms: Any = None) -> str:
    """YouTube &t=Ns, then Spotify ?t=, then Apple ?t=, else empty for /listen."""
    ms = start_ms if start_ms is not None else doc.get("start_ms")
    asset = doc.get("asset") or {}
    youtube_id = _stored(doc, "youtube_video_id")
    if not youtube_id and asset.get("type") == "video":
        youtube_id = asset.get("id")
    if youtube_id:
        return youtube_listen_url(str(youtube_id), ms)
    spotify_id = _stored(doc, "spotify_episode_id")
    if spotify_id:
        return spotify_listen_url(str(spotify_id), ms)
    apple_id = _stored(doc, "apple_track_id")
    if apple_id:
        return apple_listen_url(
            apple_id, ms, _stored(doc, "itunes_id") or APPLE_COLLECTION_ID
        )
    return ""


def passage_href(asset: dict[str, Any], start_ms: Any, *, listen_base: str = "") -> str:
    """Playable link the search page opens for this passage."""
    native = primary_share_url(
        {"asset": asset, "attrs": asset.get("attrs") or {}},
        start_ms,
    )
    if native:
        return native
    asset_id = asset.get("id")
    if not asset_id:
        return ""
    path = "/listen?asset=" + quote(str(asset_id), safe="")
    if start_ms is not None and start_ms != "":
        path += "&t=" + str(seconds_from_ms(start_ms))
    base = (listen_base or "").rstrip("/")
    return f"{base}{path}" if base else path


def with_passage_hrefs(payload: dict[str, Any], listen_base: str = "") -> dict[str, Any]:
    for group in payload.get("groups") or []:
        for asset in group.get("assets") or []:
            for passage in asset.get("passages") or []:
                passage["href"] = passage_href(
                    asset, passage.get("start_ms"), listen_base=listen_base
                )
    return payload

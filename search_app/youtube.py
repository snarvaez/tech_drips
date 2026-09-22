"""List MongoDB YouTube videos and fetch timed captions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .srt import Cue

CHANNEL_URL = "https://www.youtube.com/user/mongodb/videos"
CHANNEL_ID = "UCK_m2976Yvbx-TyDLw7n1WA"
CHANNEL_KEY = "mongodb-youtube"
CHANNEL_TITLE = "MongoDB on YouTube"
CHANNEL_AUTHOR = "MongoDB"


@dataclass
class YoutubeVideo:
    video_id: str
    title: str
    url: str
    duration: Optional[float]
    published_at: Optional[datetime]


def list_channel_videos(limit: int | None = None) -> list[YoutubeVideo]:
    import yt_dlp

    opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "no_warnings": True,
    }
    if limit:
        opts["playlistend"] = limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL_URL, download=False)
    videos: list[YoutubeVideo] = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        upload = entry.get("upload_date") or entry.get("release_date")
        published = None
        if upload and len(str(upload)) >= 8:
            raw = str(upload)[:8]
            try:
                published = datetime(
                    int(raw[:4]), int(raw[4:6]), int(raw[6:8]), tzinfo=timezone.utc
                )
            except ValueError:
                published = None
        videos.append(
            YoutubeVideo(
                video_id=entry["id"],
                title=(entry.get("title") or entry["id"]).strip(),
                url=entry.get("url")
                or f"https://www.youtube.com/watch?v={entry['id']}",
                duration=entry.get("duration"),
                published_at=published,
            )
        )
    return videos


def fetch_caption_cues(video_id: str) -> list[Cue]:
    # yt-dlp innertube first. youtube-transcript-api hits timedtext from
    # datacenter IPs and is what got this environment blocked.
    try:
        cues = fetch_caption_cues_ytdlp(video_id)
        if cues:
            return cues
    except Exception as exc:
        raise RuntimeError(f"yt-dlp: {exc}") from exc
    raise RuntimeError("no captions")


def fetch_caption_cues_ytdlp(video_id: str) -> list[Cue]:
    """Pull timed English subs via yt-dlp innertube (works when the public API is IP-blocked)."""
    import json
    import urllib.request

    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-orig"],
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )
    tracks = []
    for bucket in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        for lang in ("en", "en-US", "en-orig", "en-GB"):
            tracks.extend(bucket.get(lang) or [])
    url = None
    for track in tracks:
        if track.get("ext") == "json3" and track.get("url"):
            url = track["url"]
            break
    if not url:
        for track in tracks:
            if track.get("url"):
                url = track["url"]
                break
    if not url:
        return []
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = response.read()
    if b'"events"' in payload[:200] or payload.strip().startswith(b"{"):
        data = json.loads(payload)
        return _json3_to_cues(data)
    return _vtt_to_cues(payload.decode("utf-8", "replace"))


def _json3_to_cues(data: dict) -> list[Cue]:
    cues: list[Cue] = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        text = re.sub(
            r"\s+",
            " ",
            "".join(seg.get("utf8") or "" for seg in segs).replace("\n", " "),
        ).strip()
        if not text:
            continue
        start_ms = int(event.get("tStartMs") or 0)
        duration_ms = int(event.get("dDurationMs") or 0)
        cues.append(
            Cue(start_ms=start_ms, end_ms=start_ms + duration_ms, text=text)
        )
    return cues


def _vtt_to_cues(vtt: str) -> list[Cue]:
    from .srt import parse_srt_cues

    # WebVTT is close enough to SRT after dropping WEBVTT headers.
    body = []
    for line in vtt.replace(".", ",").splitlines():
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        body.append(line)
    return parse_srt_cues("\n".join(body))


def _fetched_to_cues(fetched) -> list[Cue]:
    snippets = getattr(fetched, "snippets", fetched)
    cues: list[Cue] = []
    for snippet in snippets or []:
        text = getattr(snippet, "text", None)
        if text is None and isinstance(snippet, dict):
            text = snippet.get("text")
        start = getattr(snippet, "start", None)
        if start is None and isinstance(snippet, dict):
            start = snippet.get("start")
        duration = getattr(snippet, "duration", None)
        if duration is None and isinstance(snippet, dict):
            duration = snippet.get("duration")
        text = re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()
        if not text:
            continue
        start_s = float(start or 0)
        end_s = start_s + float(duration or 0)
        cues.append(
            Cue(
                start_ms=max(0, int(start_s * 1000)),
                end_ms=max(0, int(end_s * 1000)),
                text=text,
            )
        )
    return cues

"""Parse a podcast RSS feed into episode records."""

from __future__ import annotations

import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
}

USER_AGENT = "podcast-transcript-search/1.0 (+https://github.com/mongodb)"
FEED_URL = "https://anchor.fm/s/1004c5698/podcast/rss"


@dataclass
class Episode:
    guid: str
    title: str
    link: str
    published_at: Optional[datetime]
    duration: str
    audio_url: str
    transcript_url: Optional[str]
    description: str
    spotify_episode_id: Optional[str] = None
    anchor_id: Optional[str] = None


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: int = 60) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", "replace")


def parse_feed(xml_bytes: bytes) -> tuple[str, str, list[Episode]]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel missing")
    show_title = (channel.findtext("title") or "").strip()
    author = (
        channel.findtext("itunes:author", default="", namespaces=NS) or ""
    ).strip()
    episodes: list[Episode] = []
    for item in channel.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        title = unescape((item.findtext("title") or "").strip())
        if not guid or not title:
            continue
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url") if enclosure is not None else ""
        transcript = item.find("podcast:transcript", NS)
        transcript_url = transcript.get("url") if transcript is not None else None
        spotify_episode_id = None
        if transcript_url:
            parts = urlparse(transcript_url).path.strip("/").split("/")
            if len(parts) >= 2:
                spotify_episode_id = parts[1]
        link = (item.findtext("link") or "").strip()
        anchor_match = re.search(r"-(e[0-9a-z]+)/?$", link, re.I)
        anchor_id = anchor_match.group(1) if anchor_match else None
        pub = item.findtext("pubDate")
        published_at = None
        if pub:
            try:
                published_at = parsedate_to_datetime(pub)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, IndexError):
                published_at = None
        episodes.append(
            Episode(
                guid=guid,
                title=title,
                link=link,
                published_at=published_at,
                duration=item.findtext("itunes:duration", default="", namespaces=NS)
                or "",
                audio_url=audio_url or "",
                transcript_url=transcript_url,
                description=item.findtext("description") or "",
                spotify_episode_id=spotify_episode_id,
                anchor_id=anchor_id,
            )
        )
    return show_title, author, episodes

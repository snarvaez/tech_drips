"""Passage schema for shows, channels, blogs, repos, and snippets.

One collection, ``passages``. Each document is a bounded searchable chunk
(``text``) of an asset. ``asset`` is the episode, video, post, repo, or
snippet. ``parent`` is set only when that asset sits inside a container (a
podcast, a channel, or a blog). ``text`` stays the Voyage auto-embed path.

Schema version 3 does not write ``podcast_*``, ``episode_*``, ``source_*``,
or ``item_*``. ``passage_from_legacy`` still reads those names so existing
documents can be migrated.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 3

ASSET_TYPES = ("episode", "video", "post", "file", "doc", "repo", "snippet")
PARENT_TYPES = ("podcast", "channel", "blog", "repository", "manual")
PARENT_OF = {
    "episode": "podcast",
    "video": "channel",
    "post": "blog",
    "file": "repository",
    "doc": "manual",
}
TOP_LEVEL = ("repo", "snippet")

ATTR_KEYS = (
    "itunes_id",
    "apple_track_id",
    "spotify_episode_id",
    "anchor_id",
    "duration",
    "youtube_video_id",
    "youtube_channel_id",
    "host",
    "full_name",
    "path",
    "ref",
    "language",
    "slug",
    "channel",
    "locale",
    "sitemap_lastmod",
    "start_line",
    "product",
    "heading",
    "anchor",
)

LEGACY_FIELDS = (
    "podcast_id",
    "podcast_title",
    "podcast_author",
    "episode_id",
    "episode_title",
    "episode_url",
    "source_kind",
    "source_id",
    "source_title",
    "source_author",
    "item_id",
    "item_title",
    "item_url",
    "media_kind",
    "source",
    "spotify_episode_id",
    "apple_track_id",
    "itunes_id",
    "youtube_video_id",
    "anchor_id",
    "duration",
)

# $project for search pipelines. Legacy names are absent on purpose.
PASSAGE_PROJECT = {
    "schema_version": 1,
    "asset": 1,
    "parent": 1,
    "audio_url": 1,
    "attrs": 1,
    "start_ms": 1,
    "end_ms": 1,
    "published_at": 1,
    "chunk_index": 1,
    "text": 1,
    "code": 1,
}


def legacy_unset() -> dict[str, str]:
    return {name: "" for name in LEGACY_FIELDS}


def passage_filter(
    asset_type: str, asset_id: str, chunk_index: int | None = None
) -> dict[str, Any]:
    filt: dict[str, Any] = {"asset.type": asset_type, "asset.id": asset_id}
    if chunk_index is not None:
        filt["chunk_index"] = chunk_index
    return filt


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _node(
    type_: str,
    id_: str,
    title: str,
    url: str | None = None,
    author: str | None = None,
) -> dict[str, str]:
    node = {"type": type_, "id": id_, "title": title}
    if _present(url):
        node["url"] = str(url)
    if _present(author):
        node["author"] = str(author)
    return node


def _clean_attrs(attrs: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        if _present(value):
            cleaned[key] = value
    return cleaned


def passage_document(
    *,
    asset_type: str,
    asset_id: str,
    title: str,
    chunk_index: int,
    text: str,
    url: str | None = None,
    author: str | None = None,
    parent: dict[str, Any] | None = None,
    published_at: Any = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    audio_url: str | None = None,
    attrs: dict[str, Any] | None = None,
    ingest_source: str | None = None,
    ingested_at: Any = None,
    ingest_complete: bool | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"Unknown asset type {asset_type!r}")
    if not _present(asset_id) or not _present(title):
        raise ValueError("asset id and title are required")
    if not _present(text):
        raise ValueError("passage text is required")

    if asset_type in TOP_LEVEL:
        if parent:
            raise ValueError(f"{asset_type} passages have no parent")
        parent_node = None
    else:
        expected = PARENT_OF[asset_type]
        if not parent:
            raise ValueError(f"{asset_type} passages require a parent")
        if parent.get("type") != expected:
            raise ValueError(f"{asset_type} parent type must be {expected}")
        if not _present(parent.get("id")) or not _present(parent.get("title")):
            raise ValueError("parent id and title are required")
        parent_node = _node(
            expected,
            str(parent["id"]),
            str(parent["title"]),
            parent.get("url"),
            parent.get("author"),
        )

    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "asset": _node(asset_type, str(asset_id), str(title), url, author),
        "chunk_index": int(chunk_index),
        "text": text,
    }
    if parent_node:
        doc["parent"] = parent_node
    if published_at is not None:
        doc["published_at"] = published_at
    if start_ms is not None:
        doc["start_ms"] = start_ms
    if end_ms is not None:
        doc["end_ms"] = end_ms
    if _present(audio_url):
        doc["audio_url"] = audio_url
    cleaned_attrs = _clean_attrs(attrs)
    if cleaned_attrs:
        doc["attrs"] = cleaned_attrs
    if _present(ingest_source):
        doc["ingest_source"] = ingest_source
    if ingested_at is not None:
        doc["ingested_at"] = ingested_at
    if ingest_complete is not None:
        doc["ingest_complete"] = bool(ingest_complete)
    if _present(code):
        doc["code"] = code
    return doc


def _first(doc: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = doc.get(key)
        if _present(value):
            return value
    return None


def passage_from_legacy(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a version-3 passage. Already-migrated documents pass through."""
    asset_in = dict(doc.get("asset") or {})
    parent_in = dict(doc.get("parent") or {})
    attrs = dict(doc.get("attrs") or {})

    kind = _first(doc, "source_kind", "media_kind")
    if asset_in.get("type") in ASSET_TYPES:
        asset_type = asset_in["type"]
    elif kind == "youtube" or _present(doc.get("youtube_video_id")) or _present(attrs.get("youtube_video_id")):
        asset_type = "video"
    elif kind in ("github", "repo"):
        asset_type = "repo"
    elif kind == "snippet":
        asset_type = "snippet"
    else:
        asset_type = "episode"

    asset_id = _first(asset_in, "id") or _first(doc, "item_id", "episode_id")
    title = _first(asset_in, "title") or _first(doc, "item_title", "episode_title") or (
        str(asset_id) if asset_id else ""
    )
    url = _first(asset_in, "url") or _first(doc, "item_url", "episode_url")
    author = asset_in.get("author") if _present(asset_in.get("author")) else None

    for key in ATTR_KEYS:
        if not _present(attrs.get(key)) and _present(doc.get(key)):
            attrs[key] = doc[key]
    if asset_type == "video" and _present(asset_id) and not _present(attrs.get("youtube_video_id")):
        attrs["youtube_video_id"] = asset_id

    parent_node = None
    if asset_type not in TOP_LEVEL:
        parent_type = parent_in.get("type") or PARENT_OF[asset_type]
        parent_id = _first(parent_in, "id") or _first(doc, "source_id", "podcast_id")
        parent_title = (
            _first(parent_in, "title")
            or _first(doc, "source_title", "podcast_title")
            or parent_id
        )
        parent_author = _first(parent_in, "author") or _first(doc, "source_author", "podcast_author")
        parent_url = _first(parent_in, "url")
        if _present(parent_id) and _present(parent_title):
            parent_node = _node(
                str(parent_type),
                str(parent_id),
                str(parent_title),
                parent_url,
                parent_author,
            )

    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    if "_id" in doc:
        out["_id"] = doc["_id"]
    if _present(asset_id) and _present(title):
        out["asset"] = _node(asset_type, str(asset_id), str(title), url, author)
    if parent_node:
        out["parent"] = parent_node

    for field in ("published_at", "chunk_index", "start_ms", "end_ms", "text", "ingested_at"):
        if doc.get(field) is not None:
            out[field] = doc[field]
    if _present(doc.get("audio_url")):
        out["audio_url"] = doc["audio_url"]
    ingest_source = doc.get("ingest_source")
    if not _present(ingest_source) and isinstance(doc.get("source"), str):
        ingest_source = doc["source"]
    if _present(ingest_source):
        out["ingest_source"] = ingest_source
    if doc.get("ingest_complete") is not None:
        out["ingest_complete"] = bool(doc["ingest_complete"])
    cleaned = _clean_attrs(attrs)
    if cleaned:
        out["attrs"] = cleaned

    consumed = set(out) | set(LEGACY_FIELDS) | set(ATTR_KEYS) | {"asset", "parent", "attrs"}
    for key, value in doc.items():
        if key not in consumed:
            out[key] = value
    return out


# Search hits still call this. It only produces the version-3 shape.
coerce = passage_from_legacy

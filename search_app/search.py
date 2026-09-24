"""Hybrid topic search: Atlas Search (lexical) + Voyage auto-embed (semantic)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import re

from pymongo.collection import Collection
from pymongo.errors import OperationFailure, PyMongoError

from .schema import PASSAGE_PROJECT, coerce

LEXICAL_MAX_TIME_MS = 15_000
VECTOR_MAX_TIME_MS = 20_000
RANK_FUSION_MAX_TIME_MS = 35_000

VECTOR_CANDIDATES_MULTIPLIER = 10

# prefixLength 0 is required for first-letter typos ("gsming" vs "gaming").
# maxEdits 2 is the Atlas Search cap (Levenshtein).
FUZZY = {"maxEdits": 2, "prefixLength": 0, "maxExpansions": 50}


def _hit_project(score_meta: str, *, highlights: bool = False) -> dict[str, Any]:
    proj = dict(PASSAGE_PROJECT)
    proj["score"] = {"$meta": score_meta}
    if highlights:
        proj["highlights"] = {"$meta": "searchHighlights"}
    return {"$project": proj}


def _title_should(query: str) -> list[dict[str, Any]]:
    return [
        {"text": {"query": query, "path": "text"}},
        {
            "text": {
                "query": query,
                "path": "asset.title",
                "score": {"boost": {"value": 2.0}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "asset.title.fuzzy",
                "fuzzy": FUZZY,
                "score": {"boost": {"value": 2.2}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "parent.title",
                "score": {"boost": {"value": 1.3}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "parent.title.fuzzy",
                "fuzzy": FUZZY,
                "score": {"boost": {"value": 1.1}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "text.fuzzy",
                "fuzzy": FUZZY,
                "score": {"boost": {"value": 0.8}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "code",
                "score": {"boost": {"value": 1.0}},
            }
        },
        {
            "text": {
                "query": query,
                "path": "attrs.path",
                "score": {"boost": {"value": 1.3}},
            }
        },
    ]


def lexical_pipeline(query: str, index: str, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "$search": {
                "index": index,
                "compound": {"should": _title_should(query)},
                "highlight": {
                    "path": ["text", "asset.title", "parent.title", "code"],
                    "maxNumPassages": 2,
                },
            }
        },
        {"$limit": limit},
        _hit_project("searchScore", highlights=True),
    ]


def vector_pipeline(
    query: str, index: str, model: str, limit: int, *, path: str = "text"
) -> list[dict[str, Any]]:
    num_candidates = min(max(limit * VECTOR_CANDIDATES_MULTIPLIER, 40), 10_000)
    return [
        {
            "$vectorSearch": {
                "index": index,
                "path": path,
                "query": {"text": query},
                "model": model,
                "numCandidates": num_candidates,
                "limit": limit,
            }
        },
        _hit_project("vectorSearchScore"),
    ]


def rank_fusion_pipeline(
    query: str,
    *,
    search_index: str,
    vector_index: str,
    model: str,
    limit: int,
) -> list[dict[str, Any]]:
    num_candidates = min(max(limit * VECTOR_CANDIDATES_MULTIPLIER, 40), 10_000)
    return [
        {
            "$rankFusion": {
                "input": {
                    "pipelines": {
                        "vectorPipeline": [
                            {
                                "$vectorSearch": {
                                    "index": vector_index,
                                    "path": "text",
                                    "query": {"text": query},
                                    "model": model,
                                    "numCandidates": num_candidates,
                                    "limit": limit,
                                }
                            }
                        ],
                        "textPipeline": [
                            {
                                "$search": {
                                    "index": search_index,
                                    "compound": {"should": _title_should(query)},
                                    "highlight": {
                                        "path": ["text", "asset.title", "parent.title", "code"],
                                        "maxNumPassages": 2,
                                    },
                                }
                            },
                            {"$limit": limit},
                        ],
                    }
                },
                "combination": {
                    "weights": {"vectorPipeline": 1.2, "textPipeline": 1.0}
                },
            }
        },
        {"$limit": limit},
        _hit_project("score", highlights=True),
    ]


def reciprocal_rank_fusion(
    lexical: list[dict[str, Any]],
    vector: list[dict[str, Any]],
    *,
    code: list[dict[str, Any]] | None = None,
    k: int = 60,
    lexical_weight: float = 1.0,
    vector_weight: float = 1.2,
    code_weight: float = 0.8,
    preserve_lexical_types: bool = False,
) -> list[dict[str, Any]]:
    """RRF merge when the cluster does not support $rankFusion (needs 8.0+)."""
    scores: dict[Any, float] = defaultdict(float)
    docs: dict[Any, dict[str, Any]] = {}
    sources: dict[Any, set[str]] = defaultdict(set)

    def absorb(ranked: list[dict[str, Any]], weight: float, label: str, *, keep: bool) -> None:
        for rank, doc in enumerate(ranked, start=1):
            doc_id = doc["_id"]
            scores[doc_id] += weight * (1.0 / (k + rank))
            if doc_id in docs:
                if not docs[doc_id].get("highlights") and doc.get("highlights"):
                    docs[doc_id]["highlights"] = doc["highlights"]
            else:
                docs[doc_id] = doc
            if keep and doc.get("match_types"):
                sources[doc_id].update(doc["match_types"])
            else:
                sources[doc_id].add(label)

    absorb(lexical, lexical_weight, "keyword", keep=preserve_lexical_types)
    absorb(vector, vector_weight, "semantic", keep=False)
    absorb(code or [], code_weight, "code", keep=False)

    ranked = []
    for doc_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        merged = dict(docs[doc_id])
        merged["score"] = score
        merged["match_types"] = sorted(sources[doc_id])
        ranked.append(merged)
    return ranked


def highlight_html(highlights: list[dict[str, Any]] | None, fallback: str) -> str:
    if not highlights:
        text = fallback.strip()
        if len(text) > 420:
            text = text[:420].rsplit(" ", 1)[0] + "…"
        return _escape(text)

    passages = []
    for hl in highlights:
        parts = []
        for piece in hl.get("texts", []):
            value = _escape(piece.get("value", ""))
            if piece.get("type") == "hit":
                parts.append(f"<mark>{value}</mark>")
            else:
                parts.append(value)
        joined = "".join(parts).strip()
        if joined:
            passages.append(joined)
    return " … ".join(passages) if passages else _escape(fallback[:420])


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _published(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    published = doc.get("published_at")
    doc = coerce(doc)
    asset = doc.get("asset") or {}
    return {
        "id": str(doc.get("_id", "")),
        "asset": {
            "type": asset.get("type"),
            "id": asset.get("id"),
            "title": asset.get("title"),
            "url": asset.get("url"),
            "author": asset.get("author"),
        },
        "parent": doc.get("parent"),
        "audio_url": doc.get("audio_url") or "",
        "attrs": doc.get("attrs") or {},
        "start_ms": doc.get("start_ms"),
        "end_ms": doc.get("end_ms"),
        "published_at": _published(published),
        "chunk_index": doc.get("chunk_index"),
        "text": doc.get("text"),
        "score": float(doc.get("score") or 0),
        "match_types": doc.get("match_types") or [],
        "snippet_html": highlight_html(doc.get("highlights"), _display_text(doc)),
    }


def group_passages(
    docs: list[dict[str, Any]], passages_per_asset: int
) -> list[dict[str, Any]]:
    """Group chunks by asset, then by parent. Top-level assets are their own group."""
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for doc in docs:
        asset = doc.get("asset") or {}
        asset_id = asset.get("id")
        asset_type = asset.get("type") or "episode"
        if not asset_id:
            continue
        key = (asset_type, asset_id)
        if key not in assets:
            order.append(key)
            assets[key] = {
                "type": asset_type,
                "id": asset_id,
                "title": asset.get("title"),
                "url": asset.get("url"),
                "author": asset.get("author"),
                "parent": doc.get("parent"),
                "audio_url": doc.get("audio_url") or "",
                "attrs": doc.get("attrs") or {},
                "published_at": doc.get("published_at"),
                "score": doc["score"],
                "match_types": set(doc.get("match_types") or []),
                "passages": [],
            }
        bucket = assets[key]
        bucket["score"] = max(bucket["score"], doc["score"])
        bucket["match_types"].update(doc.get("match_types") or [])
        if len(bucket["passages"]) < passages_per_asset:
            bucket["passages"].append(
                {
                    "chunk_index": doc["chunk_index"],
                    "snippet_html": doc["snippet_html"],
                    "score": doc["score"],
                    "match_types": doc.get("match_types") or [],
                    "start_ms": doc.get("start_ms"),
                    "end_ms": doc.get("end_ms"),
                    "start_line": (doc.get("attrs") or {}).get("start_line"),
                    "anchor": (doc.get("attrs") or {}).get("anchor"),
                    "audio_url": doc.get("audio_url") or "",
                }
            )

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    group_order: list[tuple[str, str]] = []
    for key in order:
        asset = assets[key]
        asset["match_types"] = sorted(asset["match_types"])
        parent = asset.get("parent") or {}
        if parent.get("id"):
            gkey = (parent.get("type") or "podcast", parent["id"])
            header = {
                "type": parent.get("type"),
                "id": parent["id"],
                "title": parent.get("title"),
                "author": parent.get("author"),
                "url": parent.get("url"),
            }
        else:
            gkey = (asset["type"], asset["id"])
            header = {
                "type": asset["type"],
                "id": asset["id"],
                "title": asset.get("title"),
                "author": asset.get("author"),
                "url": asset.get("url"),
            }
        if gkey not in groups:
            group_order.append(gkey)
            groups[gkey] = {**header, "score": asset["score"], "assets": []}
        group = groups[gkey]
        group["score"] = max(group["score"], asset["score"])
        group["assets"].append(asset)

    return [groups[key] for key in group_order]


def _annotate_match_types(
    docs: list[dict[str, Any]], lexical_ids: set[Any], vector_ids: set[Any]
) -> None:
    for doc in docs:
        types = []
        if doc["_id"] in lexical_ids:
            types.append("keyword")
        if vector_ids:
            if doc["_id"] in vector_ids:
                types.append("semantic")
        elif doc["_id"] not in lexical_ids:
            types.append("semantic")
        doc["match_types"] = types


def _run_aggregate(collection: Collection, pipeline: list[dict[str, Any]], max_time_ms: int):
    return list(collection.aggregate(pipeline, maxTimeMS=max_time_ms))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            ins, delete, sub = curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)
            curr.append(min(ins, delete, sub))
        prev = curr
    return prev[-1]


_FALLBACK_PROJECTION = {
    "asset": 1,
    "parent": 1,
    "attrs": 1,
    "audio_url": 1,
    "start_ms": 1,
    "end_ms": 1,
    "published_at": 1,
    "chunk_index": 1,
    "text": 1,
    "podcast_id": 1,
    "podcast_title": 1,
    "podcast_author": 1,
    "episode_id": 1,
    "episode_title": 1,
    "episode_url": 1,
    "source_kind": 1,
    "source_id": 1,
    "source_title": 1,
    "source_author": 1,
    "item_id": 1,
    "item_title": 1,
    "item_url": 1,
    "media_kind": 1,
    "youtube_video_id": 1,
    "spotify_episode_id": 1,
    "apple_track_id": 1,
    "itunes_id": 1,
}


def _asset_title(doc: dict[str, Any]) -> str:
    asset = doc.get("asset") or {}
    return asset.get("title") or doc.get("episode_title") or doc.get("item_title") or ""


def regex_fallback(
    collection: Collection, query: str, limit: int
) -> list[dict[str, Any]]:
    """Last resort when Atlas Search / mongot is unreachable."""
    pattern = re.compile(re.escape(query), re.I)
    docs = list(
        collection.find(
            {
                "$or": [
                    {"asset.title": pattern},
                    {"parent.title": pattern},
                    {"episode_title": pattern},
                    {"text": pattern},
                ]
            },
            _FALLBACK_PROJECTION,
        ).limit(limit)
    )
    for doc in docs:
        doc.update(coerce(doc))
        doc["score"] = 1.0
        doc["match_types"] = ["keyword"]
        text = doc.get("text") or ""
        marked = pattern.sub(lambda m: f"<mark>{_escape(m.group(0))}</mark>", text)
        if "<mark>" not in marked:
            marked = _escape(text[:420])
        elif len(marked) > 800:
            idx = marked.find("<mark>")
            start = max(0, idx - 120)
            marked = ("…" if start else "") + marked[start : start + 600]
        doc["highlights"] = None
        doc["snippet_html"] = marked
    if docs:
        return docs

    token = query.strip().lower()
    if not (token.isalpha() and 4 <= len(token) <= 16):
        return []
    # Title-only, 1 edit: "gsming"→"gaming" without matching "giving".
    scanned = collection.find({}, _FALLBACK_PROJECTION)
    fuzzy_docs: list[dict[str, Any]] = []
    seen_assets: set[str] = set()
    for doc in scanned:
        title = _asset_title(doc).lower()
        words = re.findall(r"[a-z0-9']+", title)
        if any(
            abs(len(token) - len(w)) <= 1 and levenshtein(token, w) == 1
            for w in words
        ):
            doc.update(coerce(doc))
            eid = (doc.get("asset") or {}).get("id")
            if eid in seen_assets:
                continue
            seen_assets.add(eid)
            doc["score"] = 0.5
            doc["match_types"] = ["keyword"]
            doc["highlights"] = None
            doc["snippet_html"] = _escape((doc.get("text") or "")[:420])
            fuzzy_docs.append(doc)
            if len(fuzzy_docs) >= limit:
                break
    return fuzzy_docs


def _display_text(doc: dict[str, Any]) -> str:
    text = doc.get("text") or ""
    code = doc.get("code") or ""
    if code and len(text) < 180:
        return code
    return text


def search_topic(
    collection: Collection,
    query: str,
    *,
    search_index: str,
    vector_index: str,
    model: str,
    limit: int,
    snippets_per_episode: int,
    code_index: str | None = None,
    code_model: str | None = None,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"query": query, "mode": None, "groups": [], "total_snippets": 0}

    lexical_docs: list[dict[str, Any]] = []
    vector_docs: list[dict[str, Any]] = []
    warnings: list[str] = []
    ranked: list[dict[str, Any]] = []
    mode: str | None = None

    try:
        fused = _run_aggregate(
            collection,
            rank_fusion_pipeline(
                query,
                search_index=search_index,
                vector_index=vector_index,
                model=model,
                limit=limit,
            ),
            RANK_FUSION_MAX_TIME_MS,
        )
        if fused:
            for doc in fused:
                doc["match_types"] = ["keyword", "semantic"]
            ranked = fused
            mode = "rankFusion"
    except (PyMongoError, OperationFailure) as exc:
        warnings.append(f"$rankFusion unavailable ({exc}); combining pipelines.")

    if not ranked:
        try:
            lexical_docs = _run_aggregate(
                collection,
                lexical_pipeline(query, search_index, limit),
                LEXICAL_MAX_TIME_MS,
            )
        except (PyMongoError, OperationFailure) as exc:
            warnings.append(f"Atlas Search unavailable: {exc}")
        try:
            vector_docs = _run_aggregate(
                collection,
                vector_pipeline(query, vector_index, model, limit),
                VECTOR_MAX_TIME_MS,
            )
        except (PyMongoError, OperationFailure) as exc:
            warnings.append(f"Vector search unavailable: {exc}")
        used_fallback = False
        if not lexical_docs:
            fallback_docs = regex_fallback(collection, query, limit)
            if fallback_docs:
                lexical_docs = fallback_docs
                used_fallback = True
                warnings.append("No Atlas Search hits; used title/text match.")
        ranked = reciprocal_rank_fusion(lexical_docs, vector_docs)
        if lexical_docs and vector_docs:
            mode = "rrf"
        elif used_fallback:
            mode = "fallback"
        elif lexical_docs:
            mode = "keyword"
        elif vector_docs:
            mode = "semantic"
        else:
            mode = None

    if code_index and code_model:
        try:
            code_docs = _run_aggregate(
                collection,
                vector_pipeline(query, code_index, code_model, limit, path="code"),
                VECTOR_MAX_TIME_MS,
            )
        except (PyMongoError, OperationFailure) as exc:
            code_docs = []
            warnings.append(f"Code vector search unavailable: {exc}")
        if code_docs:
            if ranked:
                ranked = reciprocal_rank_fusion(
                    ranked,
                    [],
                    code=code_docs,
                    lexical_weight=1.0,
                    code_weight=0.8,
                    preserve_lexical_types=True,
                )
            else:
                ranked = reciprocal_rank_fusion([], [], code=code_docs)
            mode = f"{mode}+code" if mode else "code"
        ranked = ranked[:limit]

    serialized = []
    for doc in ranked:
        item = _serialize(doc)
        if doc.get("snippet_html") and not (doc.get("highlights")):
            item["snippet_html"] = doc["snippet_html"]
        serialized.append(item)
    groups = group_passages(serialized, snippets_per_episode)
    return {
        "query": query,
        "mode": mode,
        "groups": groups,
        "total_snippets": len(serialized),
        "warnings": warnings,
    }

"""Atlas Search + Voyage auto-embed index definitions.

Create documents first, then these indexes. Automated Embedding is faster on a
prepopulated collection because the initial sync uses a higher-throughput path.
"""

from __future__ import annotations

import time
from typing import Any

from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

VECTOR_INDEX_NAME = "passage_vector_index"
CODE_VECTOR_INDEX_NAME = "passage_code_index"
SEARCH_INDEX_NAME = "passage_search_index"
EMBEDDING_MODEL = "voyage-4"
CODE_EMBEDDING_MODEL = "voyage-code-4"

LEGACY_INDEXES = (
    "episode_chunk",
    "item_chunk",
    "podcast_published",
    "source_published",
    "kind_published",
)

SNIPPET_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["schema_version", "asset", "chunk_index", "text"],
        "properties": {
            "schema_version": {"bsonType": ["int", "long"]},
            "asset": {
                "bsonType": "object",
                "required": ["type", "id", "title"],
                "properties": {
                    "type": {"enum": ["episode", "video", "post", "file", "repo", "snippet"]},
                    "id": {"bsonType": "string", "minLength": 1},
                    "title": {"bsonType": "string", "minLength": 1},
                    "url": {"bsonType": "string"},
                    "author": {"bsonType": "string"},
                },
            },
            "parent": {
                "bsonType": "object",
                "required": ["type", "id", "title"],
                "properties": {
                    "type": {"enum": ["podcast", "channel", "blog", "repository"]},
                    "id": {"bsonType": "string", "minLength": 1},
                    "title": {"bsonType": "string", "minLength": 1},
                    "author": {"bsonType": "string"},
                    "url": {"bsonType": "string"},
                },
            },
            "published_at": {"bsonType": "date"},
            "chunk_index": {"bsonType": ["int", "long"], "minimum": 0},
            "start_ms": {"bsonType": ["int", "long", "null"]},
            "end_ms": {"bsonType": ["int", "long", "null"]},
            "audio_url": {"bsonType": "string"},
            "attrs": {"bsonType": "object"},
            "ingest_source": {"bsonType": "string"},
            "ingested_at": {"bsonType": "date"},
            "ingest_complete": {"bsonType": "bool"},
            "text": {
                "bsonType": "string",
                "minLength": 1,
                "maxLength": 8000,
                "description": "Bounded passage for search and Voyage auto-embed.",
            },
            "code": {
                "bsonType": "string",
                "minLength": 1,
                "maxLength": 8000,
                "description": "Source chunk embedded with voyage-code-4, not voyage-4.",
            },
        },
    }
}

VECTOR_INDEX_DEFINITION: dict[str, Any] = {
    "fields": [
        {
            "type": "autoEmbed",
            "path": "text",
            "model": EMBEDDING_MODEL,
            "modality": "text",
        },
        {"type": "filter", "path": "asset.type"},
        {"type": "filter", "path": "asset.id"},
        {"type": "filter", "path": "parent.id"},
    ]
}

# Source stays off the voyage-4 index. A missing `code` field is not embedded.
CODE_VECTOR_INDEX_DEFINITION: dict[str, Any] = {
    "fields": [
        {
            "type": "autoEmbed",
            "path": "code",
            "model": CODE_EMBEDDING_MODEL,
            "modality": "text",
        },
        {"type": "filter", "path": "asset.type"},
        {"type": "filter", "path": "asset.id"},
        {"type": "filter", "path": "parent.id"},
    ]
}


def _string_with_fuzzy(analyzer: str = "lucene.english") -> dict[str, Any]:
    # lucene.english stems ("gaming" → "game"), which blocks typos like
    # "gsming". The `fuzzy` multi uses lucene.standard so $search fuzzy
    # (maxEdits 1–2) can match the raw token.
    return {
        "type": "string",
        "analyzer": analyzer,
        "multi": {
            "fuzzy": {
                "type": "string",
                "analyzer": "lucene.standard",
            }
        },
    }


SEARCH_INDEX_DEFINITION: dict[str, Any] = {
    "analyzer": "lucene.english",
    "searchAnalyzer": "lucene.english",
    "mappings": {
        "dynamic": False,
        "fields": {
            "text": _string_with_fuzzy(),
            "asset": {
                "type": "document",
                "fields": {
                    "title": _string_with_fuzzy(),
                    "type": {"type": "token"},
                    "id": {"type": "token"},
                },
            },
            "parent": {
                "type": "document",
                "fields": {
                    "title": _string_with_fuzzy(),
                    "type": {"type": "token"},
                    "id": {"type": "token"},
                },
            },
            # lucene.standard keeps updateZoneKeyRange as one token.
            "code": {"type": "string", "analyzer": "lucene.standard"},
            "attrs": {
                "type": "document",
                "fields": {
                    "path": {"type": "string", "analyzer": "lucene.standard"},
                },
            },
        },
    },
}


def ensure_collection(db, name: str) -> Collection:
    if name not in db.list_collection_names():
        db.create_collection(
            name,
            validator=SNIPPET_VALIDATOR,
            validationLevel="strict",
            validationAction="error",
        )
    return db[name]


def drop_legacy_indexes(collection: Collection) -> None:
    existing = {idx["name"] for idx in collection.list_indexes()}
    for name in LEGACY_INDEXES:
        if name in existing:
            collection.drop_index(name)


def apply_validator(collection: Collection) -> None:
    collection.database.command(
        {
            "collMod": collection.name,
            "validator": SNIPPET_VALIDATOR,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )


def ensure_classic_indexes(collection: Collection) -> None:
    drop_legacy_indexes(collection)
    collection.create_index(
        [("asset.type", 1), ("asset.id", 1), ("chunk_index", 1)],
        unique=True,
        name="asset_chunk",
    )
    collection.create_index(
        [("asset.id", 1)],
        name="asset_id",
    )
    collection.create_index(
        [("parent.id", 1), ("published_at", -1)],
        name="parent_published",
        partialFilterExpression={"parent.id": {"$exists": True}},
    )
    collection.create_index(
        [("asset.type", 1), ("published_at", -1)],
        name="asset_type_published",
    )


def _existing_search_names(collection: Collection) -> set[str] | None:
    try:
        return {idx["name"] for idx in collection.list_search_indexes()}
    except Exception:
        return None


def ensure_search_indexes(
    collection: Collection,
    *,
    vector_name: str = VECTOR_INDEX_NAME,
    search_name: str = SEARCH_INDEX_NAME,
    attempts: int = 6,
) -> None:
    models = {
        vector_name: SearchIndexModel(
            definition=VECTOR_INDEX_DEFINITION,
            name=vector_name,
            type="vectorSearch",
        ),
        search_name: SearchIndexModel(
            definition=SEARCH_INDEX_DEFINITION,
            name=search_name,
            type="search",
        ),
        CODE_VECTOR_INDEX_NAME: SearchIndexModel(
            definition=CODE_VECTOR_INDEX_DEFINITION,
            name=CODE_VECTOR_INDEX_NAME,
            type="vectorSearch",
        ),
    }
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            existing = _existing_search_names(collection) or set()
            pending = [model for name, model in models.items() if name not in existing]
            if pending:
                collection.create_search_indexes(pending)
            if search_name in existing:
                collection.update_search_index(search_name, SEARCH_INDEX_DEFINITION)
            if vector_name in existing:
                collection.update_search_index(vector_name, VECTOR_INDEX_DEFINITION)
            if CODE_VECTOR_INDEX_NAME in existing:
                collection.update_search_index(
                    CODE_VECTOR_INDEX_NAME, CODE_VECTOR_INDEX_DEFINITION
                )
            return
        except Exception as exc:
            last_error = exc
            time.sleep(min(10 * attempt, 30))
    if last_error:
        raise last_error


def wait_for_indexes(
    collection: Collection,
    names: list[str],
    timeout_seconds: int = 300,
) -> None:
    deadline = time.time() + timeout_seconds
    pending = set(names)
    while pending and time.time() < deadline:
        status = {idx["name"]: idx for idx in collection.list_search_indexes()}
        ready = set()
        for name in pending:
            idx = status.get(name)
            if not idx:
                continue
            if idx.get("queryable") or idx.get("status") == "READY":
                ready.add(name)
        pending -= ready
        if pending:
            time.sleep(5)
    if pending:
        raise TimeoutError(f"Search indexes not queryable: {sorted(pending)}")

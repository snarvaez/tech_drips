"""Move snippets onto the passages collection and schema version 3.

Renames ``podcasts`` to ``passages`` when the new collection is absent,
rewrites each document to ``asset`` / ``parent``, then rebuilds classic
indexes and the Atlas Search + vector indexes.

Run once after deploy:

    python -m search_app.migrate_schema
"""

from __future__ import annotations

import sys

from pymongo import MongoClient, ReplaceOne
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.server_api import ServerApi

from .config import Config
from .indexes import (
    SEARCH_INDEX_NAME,
    VECTOR_INDEX_NAME,
    apply_validator,
    ensure_classic_indexes,
    ensure_collection,
    ensure_search_indexes,
    wait_for_indexes,
)
from .schema import LEGACY_FIELDS, SCHEMA_VERSION, passage_from_legacy

LEGACY_COLLECTION = "podcasts"
BATCH = 500


def _resolve(db: Database) -> Collection:
    target_name = Config.MONGODB_COLLECTION or "passages"
    names = set(db.list_collection_names())
    if target_name not in names and LEGACY_COLLECTION in names and target_name != LEGACY_COLLECTION:
        print(f"Renaming {db.name}.{LEGACY_COLLECTION} -> {target_name}")
        db[LEGACY_COLLECTION].rename(target_name)
        return db[target_name]
    if (
        target_name in names
        and LEGACY_COLLECTION in names
        and target_name != LEGACY_COLLECTION
        and db[target_name].estimated_document_count() == 0
        and db[LEGACY_COLLECTION].estimated_document_count() > 0
    ):
        print(f"Dropping empty {target_name} and renaming {LEGACY_COLLECTION}")
        db[target_name].drop()
        db[LEGACY_COLLECTION].rename(target_name)
        return db[target_name]
    if target_name not in names:
        print(f"Creating {db.name}.{target_name}")
        return ensure_collection(db, target_name)
    return db[target_name]


def _rewrite(coll: Collection) -> tuple[int, int]:
    matched = 0
    modified = 0
    batch: list[ReplaceOne] = []

    def flush() -> None:
        nonlocal modified
        if not batch:
            return
        result = coll.bulk_write(batch, ordered=False)
        modified += result.modified_count + result.upserted_count
        batch.clear()

    for doc in coll.find({}, batch_size=BATCH):
        matched += 1
        rewritten = passage_from_legacy(doc)
        if "asset" not in rewritten or not rewritten.get("text"):
            print(f"skip {doc.get('_id')}: missing asset or text", file=sys.stderr)
            continue
        stale = rewritten != doc or any(name in doc for name in LEGACY_FIELDS)
        if not stale and doc.get("schema_version") == SCHEMA_VERSION:
            continue
        batch.append(ReplaceOne({"_id": doc["_id"]}, rewritten))
        if len(batch) >= BATCH:
            flush()
            print(f"  rewritten {modified} / scanned {matched}", flush=True)
    flush()
    return matched, modified


def _drop_old_search_indexes(coll: Collection, keep: set[str]) -> None:
    old = {"podcast_search_index", "podcast_vector_index"} - keep
    try:
        existing = {idx.get("name") for idx in coll.list_search_indexes()}
    except Exception as exc:
        print(f"Could not list search indexes: {exc}")
        return
    for name in sorted(old & existing):
        print(f"Dropping search index {name}")
        coll.drop_search_index(name)


def main() -> int:
    if not Config.MONGODB_URI:
        print("Set MONGODB_URI", file=sys.stderr)
        return 1
    client = MongoClient(
        Config.MONGODB_URI,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=30_000,
        socketTimeoutMS=120_000,
        appname="schema-migrate-v3",
    )
    try:
        db = client[Config.MONGODB_DB]
        coll = _resolve(db)
        total = coll.estimated_document_count()
        print(f"Migrating {total} documents in {coll.full_name} to schema_version={SCHEMA_VERSION}")
        print("Dropping legacy unique indexes before the field rewrite…")
        from .indexes import drop_legacy_indexes

        drop_legacy_indexes(coll)
        matched, modified = _rewrite(coll)
        print(f"scanned={matched} replaced={modified}")
        sample = coll.find_one(
            {"schema_version": SCHEMA_VERSION},
            {"asset": 1, "parent": 1, "chunk_index": 1, "attrs": 1},
        )
        print("sample", sample)
        kinds = list(
            coll.aggregate(
                [{"$group": {"_id": "$asset.type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]
            )
        )
        print("asset.type counts", kinds)

        print("Applying validator and classic indexes…")
        apply_validator(coll)
        ensure_classic_indexes(coll)
        search_name = Config.SEARCH_INDEX or SEARCH_INDEX_NAME
        vector_name = Config.VECTOR_INDEX or VECTOR_INDEX_NAME
        print("Updating Atlas Search + vector index definitions…")
        ensure_search_indexes(
            coll,
            vector_name=vector_name,
            search_name=search_name,
        )
        print("Waiting until search indexes are queryable…")
        wait_for_indexes(coll, [search_name, vector_name], timeout_seconds=900)
        _drop_old_search_indexes(coll, {search_name, vector_name})
        print("Classic indexes:", [idx["name"] for idx in coll.list_indexes()])
        for idx in coll.list_search_indexes():
            print(
                " search",
                idx.get("name"),
                idx.get("status"),
                "queryable",
                idx.get("queryable"),
            )
        print("Done.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

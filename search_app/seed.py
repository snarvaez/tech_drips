"""Populate Atlas with test transcripts, then create Search + autoEmbed indexes."""

from __future__ import annotations

import sys

from pymongo import MongoClient
from pymongo.server_api import ServerApi

from .config import Config
from .indexes import (
    SEARCH_INDEX_NAME,
    VECTOR_INDEX_NAME,
    ensure_classic_indexes,
    ensure_collection,
    ensure_search_indexes,
    wait_for_indexes,
)
from .schema import passage_filter
from .transcripts import SNIPPETS


def seed(uri: str | None = None) -> int:
    uri = uri or Config.MONGODB_URI
    if not uri:
        print("Set MONGODB_URI in the environment or .env", file=sys.stderr)
        return 1

    client = MongoClient(
        uri,
        server_api=ServerApi("1"),
        maxPoolSize=5,
        minPoolSize=0,
        serverSelectionTimeoutMS=8_000,
        appname="podcast-transcript-seed",
    )
    try:
        client.admin.command("ping")
        db = client[Config.MONGODB_DB]
        collection = ensure_collection(db, Config.MONGODB_COLLECTION)
        ensure_classic_indexes(collection)

        for snippet in SNIPPETS:
            asset = snippet["asset"]
            collection.replace_one(
                passage_filter(asset["type"], asset["id"], snippet["chunk_index"]),
                snippet,
                upsert=True,
            )
        print(f"Upserted {len(SNIPPETS)} snippets into {db.name}.{collection.name}")

        ensure_search_indexes(
            collection,
            vector_name=Config.VECTOR_INDEX,
            search_name=Config.SEARCH_INDEX,
        )
        print(
            "Ensured indexes "
            f"{Config.SEARCH_INDEX} (Atlas Search) and "
            f"{Config.VECTOR_INDEX} (Voyage {Config.EMBEDDING_MODEL} autoEmbed)."
        )
        print("Waiting until indexes are queryable (Voyage embeddings run on Atlas)...")
        wait_for_indexes(
            collection,
            [Config.SEARCH_INDEX, Config.VECTOR_INDEX],
            timeout_seconds=300,
        )
        print("Indexes are queryable.")
        return 0
    finally:
        client.close()


def main() -> None:
    raise SystemExit(seed())


if __name__ == "__main__":
    main()

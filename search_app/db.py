"""MongoDB client lifecycle for a long-running Gunicorn/Flask process.

Each Gunicorn worker is its own process, so each worker owns one MongoClient.
Do not create a client per request — handshake + TLS is 50–500ms.
"""

from __future__ import annotations

import certifi
from flask import current_app, g
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.server_api import ServerApi

_client: MongoClient | None = None


def _build_client(uri: str) -> MongoClient:
    # macOS system Python 3.9 ships LibreSSL 2.8.3, which can stall TLS
    # handshakes to Atlas. Pin certifi's CA bundle and bound every wait so a
    # hung SSL socket cannot freeze the search UI.
    return MongoClient(
        uri,
        server_api=ServerApi("1"),
        tls=True,
        tlsCAFile=certifi.where(),
        # Peak concurrent ops per worker for this search UI is low.
        maxPoolSize=10,
        # Do not keep idle TLS sockets; LibreSSL + Atlas had handshake timeouts
        # on recycled connections.
        minPoolSize=0,
        maxIdleTimeMS=30_000,
        connectTimeoutMS=8_000,
        serverSelectionTimeoutMS=8_000,
        socketTimeoutMS=40_000,
        timeoutMS=45_000,
        retryWrites=True,
        retryReads=True,
        appname="podcast-transcript-search",
    )


def init_db(app) -> None:
    global _client
    uri = app.config.get("MONGODB_URI")
    if not uri:
        app.logger.warning("MONGODB_URI is not set; search endpoints will fail.")
        return
    if _client is None:
        _client = _build_client(uri)

    @app.teardown_appcontext
    def _teardown(_exc):
        g.pop("mongo_db", None)


def get_client() -> MongoClient:
    if _client is None:
        raise RuntimeError("MongoDB client is not initialized. Set MONGODB_URI.")
    return _client


def get_db() -> Database:
    if "mongo_db" not in g:
        g.mongo_db = get_client()[current_app.config["MONGODB_DB"]]
    return g.mongo_db


def get_snippets() -> Collection:
    return get_db()[current_app.config["MONGODB_COLLECTION"]]


def ping() -> bool:
    get_client().admin.command("ping")
    return True


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None

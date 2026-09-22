from flask import Blueprint, current_app, jsonify, render_template, request

from .db import get_snippets, ping
from .schema import coerce
from .search import search_topic

bp = Blueprint("main", __name__)

SAMPLE_QUERIES = [
    "ransomware double extortion",
    "passkeys and password managers",
    "CRISPR base editing",
    "James Webb exoplanet atmospheres",
    "sticky inflation",
    "AI replacing call center jobs",
]


@bp.get("/")
def index():
    return render_template("index.html", sample_queries=SAMPLE_QUERIES)


@bp.get("/health")
def health():
    try:
        ping()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.get("/listen")
def listen():
    return render_template("listen.html")


@bp.get("/api/clip")
def api_clip():
    asset_id = (request.args.get("asset") or request.args.get("episode") or "").strip()
    if not asset_id:
        return jsonify({"error": "asset is required"}), 400
    try:
        doc = get_snippets().find_one(
            {"$or": [{"asset.id": asset_id}, {"episode_id": asset_id}, {"item_id": asset_id}]}
        )
    except Exception as exc:
        current_app.logger.exception("clip lookup failed")
        return jsonify({"error": str(exc)}), 503
    if not doc:
        return jsonify({"error": "asset not found"}), 404
    doc = coerce(doc)
    return jsonify(
        {
            "asset": doc.get("asset"),
            "parent": doc.get("parent"),
            "audio_url": doc.get("audio_url") or "",
            "attrs": doc.get("attrs") or {},
        }
    )


@bp.get("/api/search")
def api_search():
    query = request.args.get("q", "")
    try:
        collection = get_snippets()
        payload = search_topic(
            collection,
            query,
            search_index=current_app.config["SEARCH_INDEX"],
            vector_index=current_app.config["VECTOR_INDEX"],
            model=current_app.config["EMBEDDING_MODEL"],
            limit=current_app.config["SEARCH_LIMIT"],
            snippets_per_episode=current_app.config["SNIPPETS_PER_EPISODE"],
        )
        return jsonify(payload)
    except Exception as exc:
        current_app.logger.exception("search failed")
        return jsonify({"error": str(exc), "query": query, "groups": []}), 503

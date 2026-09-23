"""Ingest one GitHub repository as file passages.

Markdown is stored as prose on ``text`` and embedded with voyage-4.
Source is stored on ``code`` and embedded with voyage-code-4. A short
grounded paragraph on the first chunk of each source file is what lets a
use-case query meet the implementation.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from pymongo import UpdateOne
from pymongo.collection import Collection

from .chunking import chunk_sections, chunk_source, chunk_transcript
from .config import Config
from .describe import describe_source
from .indexes import apply_validator, ensure_collection, ensure_search_indexes
from .ingest import _client, retry_mongo
from .schema import legacy_unset, passage_document, passage_filter

ORG = "mongodb-developer"
DEFAULT_REPO = "typescript-multiplayer-gaming-example"
SOURCE = "github"
MAX_BYTES = 200_000

SKIP_DIRS = {
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}
SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
SOURCE_EXT = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".ts",
    ".tsx",
}
LANG = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".h": "c",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".mjs": "javascript",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def _run(args: list[str]) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def clone_repo(org: str, repo: str, dest: str) -> str:
    url = f"https://github.com/{org}/{repo}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", url, dest],
        check=True,
        capture_output=True,
        text=True,
    )
    return _run(["git", "-C", dest, "rev-parse", "HEAD"])


def blob_sha(root: str, path: str) -> str:
    return _run(["git", "-C", root, "hash-object", path])


def repo_title(readme: str, repo: str) -> str:
    for line in readme.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
    return repo


def markdown_chunks(text: str) -> list[str]:
    sections: list[dict] = []
    heading = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal heading, buf
        body = "\n".join(buf).strip()
        if body:
            sections.append({"heading": heading, "blocks": [{"kind": "prose", "text": body}]})
        buf = []

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or None
        else:
            buf.append(line)
    flush()
    chunks = chunk_sections(sections) if sections else []
    return chunks or chunk_transcript(text)


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        for name in filenames:
            if name in SKIP_FILES or name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            if ext == ".md" or ext in SOURCE_EXT:
                yield path, rel, ext


def read_text(path: str) -> str | None:
    if os.path.getsize(path) > MAX_BYTES:
        return None
    with open(path, "rb") as handle:
        raw = handle.read()
    if b"\0" in raw[:1024]:
        return None
    return raw.decode("utf-8", "replace")


def root_readme(root: str) -> str:
    direct = os.path.join(root, "README.md")
    if os.path.isfile(direct):
        return read_text(direct) or ""
    for path, rel, ext in iter_files(root):
        if ext == ".md" and os.path.basename(rel).lower().startswith("readme"):
            return read_text(path) or ""
    return ""


def file_passages(rel: str, ext: str, text: str, readme: str) -> list[dict]:
    if ext == ".md":
        return [
            {"text": chunk, "code": None, "start_line": None}
            for chunk in markdown_chunks(text)
        ]
    paragraph = describe_source(readme, rel, text)
    pieces = chunk_source(text)
    passages = []
    for index, piece in enumerate(pieces):
        if index == 0:
            label = paragraph
        else:
            label = f"{rel} {piece['symbol']}".strip() if piece.get("symbol") else rel
        passages.append(
            {
                "text": label,
                "code": piece["code"],
                "start_line": piece["start_line"],
            }
        )
    return passages


def upsert_file(
    collection: Collection,
    *,
    full_name: str,
    title: str,
    commit: str,
    rel: str,
    ext: str,
    blob: str,
    passages: list[dict],
) -> int:
    if not passages:
        return 0
    asset_id = f"{full_name}:{rel}"
    url = f"https://github.com/{full_name}/blob/{commit}/{rel}"
    parent_url = f"https://github.com/{full_name}"
    now = datetime.now(timezone.utc)
    ops = []
    for index, passage in enumerate(passages):
        code = passage.get("code")
        if code and len(code) > 8000:
            code = code[:8000]
        text = passage["text"]
        if len(text) > 8000:
            text = text[:8000]
        doc = passage_document(
            asset_type="file",
            asset_id=asset_id,
            title=os.path.basename(rel),
            url=url,
            parent={
                "type": "repository",
                "id": full_name,
                "title": title,
                "author": full_name.split("/")[0],
                "url": parent_url,
            },
            chunk_index=index,
            text=text,
            code=code,
            attrs={
                "path": rel,
                "language": LANG.get(ext),
                "ref": blob,
                "start_line": passage.get("start_line"),
            },
            ingest_source=SOURCE,
            ingested_at=now,
            ingest_complete=True,
        )
        ops.append(
            UpdateOne(
                passage_filter("file", asset_id, index),
                {"$set": doc, "$unset": legacy_unset()},
                upsert=True,
            )
        )
    collection.bulk_write(ops, ordered=False)
    collection.delete_many(
        {**passage_filter("file", asset_id), "chunk_index": {"$gte": len(passages)}}
    )
    return len(passages)


def stored_ref(collection: Collection, full_name: str, rel: str) -> str | None:
    doc = collection.find_one(
        passage_filter("file", f"{full_name}:{rel}", 0),
        {"attrs.ref": 1},
    )
    if not doc:
        return None
    return ((doc.get("attrs") or {}).get("ref")) or None


def _prepare(client):
    database = client[Config.MONGODB_DB]
    name = Config.MONGODB_COLLECTION
    if name not in database.list_collection_names():
        collection = ensure_collection(database, name)
    else:
        collection = database[name]
        apply_validator(collection)
    try:
        ensure_search_indexes(collection)
    except Exception as exc:
        print(f"search index update failed: {exc}", file=sys.stderr)
    return collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=ORG)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    full_name = f"{args.org}/{args.repo}"

    client = _client()
    try:
        collection = _prepare(client)
        with tempfile.TemporaryDirectory(prefix="code-ingest-") as dest:
            print(f"Cloning {full_name}")
            commit = clone_repo(args.org, args.repo, dest)
            readme = root_readme(dest)
            title = repo_title(readme, args.repo)
            print(f"Commit {commit[:12]}  {title}")
            ingested = skipped = failed = chunks = 0
            files = list(iter_files(dest))
            for index, (path, rel, ext) in enumerate(files, start=1):
                blob = blob_sha(dest, path)
                if not args.force and stored_ref(collection, full_name, rel) == blob:
                    skipped += 1
                    continue
                text = read_text(path)
                if text is None:
                    print(f"[{index}/{len(files)}] skip {rel}", flush=True)
                    skipped += 1
                    continue
                try:
                    passages = file_passages(rel, ext, text, readme)
                    written = retry_mongo(
                        lambda passages=passages, rel=rel, ext=ext, blob=blob: upsert_file(
                            collection,
                            full_name=full_name,
                            title=title,
                            commit=commit,
                            rel=rel,
                            ext=ext,
                            blob=blob,
                            passages=passages,
                        ),
                        attempts=5,
                        label="upsert",
                    )
                    if not written:
                        raise ValueError("empty file")
                    ingested += 1
                    chunks += written
                    print(f"[{index}/{len(files)}] {written} chunks  {rel}", flush=True)
                except Exception as exc:
                    failed += 1
                    print(f"[{index}/{len(files)}] FAIL {rel}: {exc}", file=sys.stderr)
        total = collection.count_documents(
            {"asset.type": "file", "parent.id": full_name}
        )
        print(
            f"Code done ingested={ingested} skipped={skipped} "
            f"failed={failed} chunks_written={chunks}"
        )
        print(f"Atlas {Config.MONGODB_DB}.{collection.name}: {total} file chunks in {full_name}")
        return 1 if failed and not ingested else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

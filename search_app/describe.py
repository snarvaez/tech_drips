"""Grounded prose for a source file.

The paragraph may only join facts that are already in the file or the
repository README. It exists so a use-case query and the implementation
can meet in the voyage-4 index.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
_STOP = {
    "async",
    "await",
    "class",
    "const",
    "export",
    "false",
    "from",
    "function",
    "import",
    "interface",
    "null",
    "private",
    "public",
    "return",
    "string",
    "this",
    "true",
    "type",
    "undefined",
    "void",
}


def identifiers(source: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for name in _IDENT.findall(source or ""):
        if name.lower() in _STOP or name in seen:
            continue
        seen.append(name)
        if len(seen) >= limit:
            break
    return seen


def readme_lead(readme: str, limit: int = 320) -> str:
    paragraphs = []
    current: list[str] = []
    for line in (readme or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    for paragraph in paragraphs:
        if len(paragraph) >= 40:
            return paragraph[:limit].rsplit(" ", 1)[0] if len(paragraph) > limit else paragraph
    return paragraphs[0][:limit] if paragraphs else ""


def grounded_fallback(readme: str, path: str, source: str) -> str:
    """Join the path, identifiers that occur in the file, and the README lead."""
    names = identifiers(source, limit=6)
    lead = readme_lead(readme)
    parts = [path]
    if names:
        parts.append("defines " + ", ".join(names))
    if lead:
        parts.append(lead)
    return ". ".join(parts)


def _shares_identifier(paragraph: str, source: str) -> bool:
    names = identifiers(source, limit=24)
    if not names:
        return True
    return any(name in paragraph for name in names)


def _llm_paragraph(readme: str, path: str, source: str) -> str | None:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        return None
    prompt = (
        "Write one paragraph, at most 80 words, for search. "
        "Use only facts in the README excerpt and the source. "
        "Name concrete identifiers that appear in the source. "
        "State a user-facing role only when the README states it. "
        "Do not invent APIs, products, or behavior. "
        "Return the paragraph only.\n\n"
        f"Path: {path}\n\nREADME:\n{readme[:2000]}\n\nSource:\n{source[:6000]}"
    )
    body = json.dumps(
        {
            "model": os.environ.get("DESCRIBE_MODEL", "grok-4.5"),
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.x.ai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None
    choices = payload.get("choices") or []
    if not choices:
        return None
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    return content or None


def describe_source(readme: str, path: str, source: str) -> str:
    fallback = grounded_fallback(readme, path, source)
    paragraph = _llm_paragraph(readme, path, source)
    if not paragraph or not _shares_identifier(paragraph, source):
        return fallback[:1500]
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    return paragraph[:1500]

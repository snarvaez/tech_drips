"""Parse MongoDB documentation Markdown into prose and code passages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .chunking import chunk_sections, chunk_source

SITEMAP_INDEX = "https://www.mongodb.com/docs/sitemap-index-full.xml"
ORIGIN = "https://www.mongodb.com"
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_VERSION_SEGMENT = re.compile(r"^v\d", re.I)
_FENCE = re.compile(r"^```([^\n`]*)\s*$")
_TOKEN = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]{2,}")
_BANNER = "for the complete mongodb documentation index"

MANUAL_TITLES = {
    "manual": "Database Manual",
    "atlas": "Atlas",
    "atlas/architecture": "Atlas Architecture Center",
    "atlas/cli": "Atlas CLI",
    "atlas/government": "Atlas for Government",
    "atlas/operator": "Atlas Kubernetes Operator",
    "search": "MongoDB Search",
    "vector-search": "Vector Search",
    "voyageai": "Voyage AI",
    "compass": "Compass",
    "charts": "Atlas Charts",
}


@dataclass
class DocPageRef:
    url: str
    lastmod: str | None
    product: str


def is_current_docs_url(url: str) -> bool:
    """Current English docs. Versioned books (/v7.0/, /mongosync/v1.10/) are out."""
    path = urlparse(url).path
    if not path.startswith("/docs/"):
        return False
    parts = [part for part in path.split("/") if part and part != "sitemap-0.xml"]
    if parts and parts[0] == "docs":
        parts = parts[1:]
    return not any(_VERSION_SEGMENT.match(part) for part in parts)


def product_id(sitemap_or_page_url: str) -> str:
    parts = [part for part in urlparse(sitemap_or_page_url).path.split("/") if part]
    if parts and parts[0] == "docs":
        parts = parts[1:]
    parts = [part for part in parts if part not in {"current", "sitemap-0.xml"}]
    if parts and parts[-1].endswith(".xml"):
        parts = parts[:-1]
    return "/".join(parts)


def manual_title(product: str) -> str:
    if product in MANUAL_TITLES:
        return MANUAL_TITLES[product]
    leaf = product.split("/")[-1].replace("-", " ")
    return leaf.title() if leaf else "MongoDB Docs"


def heading_anchor(heading: str) -> str:
    text = heading.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text


def parse_sitemap_index(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    found = []
    for node in root.findall("sm:sitemap", _SITEMAP_NS):
        loc = (node.findtext("sm:loc", default="", namespaces=_SITEMAP_NS) or "").strip()
        if loc and is_current_docs_url(loc):
            found.append(loc)
    return found


def parse_page_sitemap(xml_text: str, product: str) -> list[DocPageRef]:
    root = ET.fromstring(xml_text)
    pages = []
    prefix = f"/docs/{product}/" if product else "/docs/"
    for node in root.findall("sm:url", _SITEMAP_NS):
        loc = (node.findtext("sm:loc", default="", namespaces=_SITEMAP_NS) or "").strip()
        parsed = urlparse(loc)
        path = parsed.path
        # Tab and distro variants are the same Markdown page. Query strings
        # would rewrite one passage over and over.
        if parsed.query:
            continue
        if not loc or not is_current_docs_url(loc):
            continue
        if product and not (path.rstrip("/") == f"/docs/{product}" or path.startswith(prefix)):
            continue
        lastmod = (node.findtext("sm:lastmod", default="", namespaces=_SITEMAP_NS) or "").strip()
        pages.append(DocPageRef(url=loc, lastmod=lastmod[:10] or None, product=product or product_id(loc)))
    return pages


def markdown_url(page_url: str) -> str:
    path = urlparse(page_url).path
    if path.endswith(".md"):
        return page_url
    return ORIGIN + path.rstrip("/") + ".md"


def _first_token(code: str) -> str | None:
    match = _TOKEN.search(code or "")
    return match.group(0) if match else None


def passages_from_markdown(markdown: str) -> tuple[str, list[dict]]:
    """Return the page title and ordered passages.

    Prose chunks carry ``text`` only. Fenced samples carry ``code`` plus a
    short ``text`` label of the heading and the first identifier.
    """
    lines = _body_lines(markdown)
    title = ""
    heading = ""
    prose: list[str] = []
    passages: list[dict] = []
    fence_lang = None
    fence: list[str] = []

    def flush_prose() -> None:
        nonlocal prose
        body = "\n".join(prose).strip()
        prose = []
        if not body:
            return
        sections = [{"heading": heading or None, "blocks": [{"kind": "prose", "text": body}]}]
        for chunk in chunk_sections(sections):
            passages.append(_prose_passage(chunk, heading))

    def flush_fence() -> None:
        nonlocal fence, fence_lang
        code = "\n".join(fence).strip("\n")
        language = (fence_lang or "").strip() or None
        fence = []
        fence_lang = None
        if not code.strip():
            return
        for piece in chunk_source(code, max_chars=3500):
            passages.append(_code_passage(piece["code"], heading, language))

    for line in lines:
        fence_match = _FENCE.match(line)
        if fence_lang is not None:
            if fence_match and fence_match.group(1) == "":
                flush_fence()
            else:
                fence.append(line)
            continue
        if fence_match:
            flush_prose()
            fence_lang = fence_match.group(1)
            continue
        if line.startswith("#"):
            flush_prose()
            text = line.lstrip("#").strip()
            if line.startswith("# ") and not title:
                title = text
            heading = text or heading
            continue
        prose.append(line)
    if fence_lang is not None:
        flush_fence()
    flush_prose()
    if not title:
        title = heading
    return title, passages


def _body_lines(markdown: str) -> list[str]:
    lines = (markdown or "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().lower().startswith(">") and _BANNER in lines[0].lower():
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return lines


def _prose_passage(text: str, heading: str) -> dict:
    return {
        "text": text,
        "code": None,
        "language": None,
        "heading": heading or None,
        "anchor": heading_anchor(heading) if heading else None,
    }


def _code_passage(code: str, heading: str, language: str | None) -> dict:
    token = _first_token(code)
    label = ". ".join(part for part in (heading, token) if part) or "example"
    return {
        "text": label,
        "code": code,
        "language": language,
        "heading": heading or None,
        "anchor": heading_anchor(heading) if heading else None,
    }

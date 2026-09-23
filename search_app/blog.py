"""List MongoDB blog posts and turn Contentstack rich text into sections."""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

SITEMAP_URL = "https://www.mongodb.com/sitemap-blog-pages.xml"
BLOG_URL = "https://www.mongodb.com/company/blog"
BLOG_ID = "mongodb-blog"
BLOG_TITLE = "MongoDB Blog"
BLOG_AUTHOR = "MongoDB"
SITE_ORIGIN = "https://www.mongodb.com"

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_WHITESPACE = re.compile(r"\s+")
_USER_AGENT = "Mozilla/5.0 (compatible; TechDripBlogIngest/1.0)"


@dataclass
class SitemapEntry:
    url: str
    lastmod: str | None


@dataclass
class BlogArticle:
    uid: str
    title: str
    url: str
    slug: str
    channel: str | None
    author: str | None
    published_at: datetime | None
    locale: str | None
    sections: list[dict[str, Any]]


def fetch_bytes(url: str, timeout: float = 45) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xml"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: float = 45) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", "replace")


def is_blog_post_url(url: str) -> bool:
    """English article URLs under /company/blog/, not channel listings."""
    path = urlparse(url).path
    prefix = "/company/blog/"
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix) :].strip("/")
    if not rest or rest.split("/")[0] == "channel":
        return False
    return True


def slug_from_url(url: str) -> str:
    path = urlparse(url).path or url
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def channel_of(slug: str) -> str | None:
    parts = urlparse(slug).path.strip("/").split("/")
    # /company/blog/<channel>/<slug> carries a section. A bare slug does not.
    if len(parts) >= 4 and parts[0] == "company" and parts[1] == "blog":
        return parts[2]
    return None


def parse_sitemap(xml_text: str) -> list[SitemapEntry]:
    root = ET.fromstring(xml_text)
    entries: list[SitemapEntry] = []
    for node in root.findall("sm:url", _SITEMAP_NS):
        loc = (node.findtext("sm:loc", default="", namespaces=_SITEMAP_NS) or "").strip()
        if not loc or not is_blog_post_url(loc):
            continue
        lastmod = (node.findtext("sm:lastmod", default="", namespaces=_SITEMAP_NS) or "").strip()
        entries.append(SitemapEntry(url=loc, lastmod=lastmod[:10] or None))
    return entries


def _absolute(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return SITE_ORIGIN + path_or_url


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_page(html: str) -> dict[str, Any] | None:
    marker = 'id="__NEXT_DATA__"'
    start_attr = html.find(marker)
    if start_attr < 0:
        return None
    start = html.find(">", start_attr)
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        return None
    try:
        payload = json.loads(html[start + 1 : end])
    except json.JSONDecodeError:
        return None
    page = ((payload.get("props") or {}).get("pageProps") or {}).get("page")
    return page if isinstance(page, dict) else None


def _jsonld_article(html: str) -> dict[str, Any]:
    lowered = html.lower()
    cursor = 0
    while True:
        found = lowered.find("application/ld+json", cursor)
        if found < 0:
            return {}
        start = html.find(">", found)
        end = lowered.find("</script>", start)
        cursor = end if end > 0 else found + 1
        if start < 0 or end < 0:
            continue
        try:
            data = json.loads(html[start + 1 : end])
        except json.JSONDecodeError:
            continue
        nodes: list[Any]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes = data["@graph"]
        elif isinstance(data, list):
            nodes = data
        elif isinstance(data, dict):
            nodes = [data]
        else:
            continue
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") in ("Article", "BlogPosting"):
                return node
    return {}


def _names(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        return _names(value.get("name") or value.get("title"))
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_names(item))
        return names
    return []


def _embedded_by_uid(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    embedded = page.get("_embedded_items") or {}
    if not isinstance(embedded, dict):
        return found

    def take(item: Any) -> None:
        if isinstance(item, dict) and item.get("uid"):
            found[str(item["uid"])] = item

    for value in embedded.values():
        if isinstance(value, list):
            for item in value:
                take(item)
        else:
            take(value)
    return found


def _inline_text(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_inline_text(child) for child in node)
    if not isinstance(node, dict) or node.get("type") == "reference":
        return ""
    parts: list[str] = []
    if isinstance(node.get("text"), str):
        parts.append(node["text"])
    parts.append(_inline_text(node.get("children") or []))
    return "".join(parts)


def _prose(node: Any) -> str:
    return _WHITESPACE.sub(" ", _inline_text(node)).strip()


def _code_text(entry: dict[str, Any]) -> list[str]:
    snippets = ((entry.get("entry_content") or {}).get("code_snippets")) or []
    blocks: list[str] = []
    if not isinstance(snippets, list):
        return blocks
    for snippet in snippets:
        if not isinstance(snippet, dict):
            continue
        code = snippet.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        language = snippet.get("language")
        if isinstance(language, str) and language.strip():
            blocks.append(f"{language.strip()}\n{code.strip()}")
        else:
            blocks.append(code.strip())
    return blocks


class _SectionBuilder:
    def __init__(self) -> None:
        self.sections: list[dict[str, Any]] = [{"heading": None, "blocks": []}]

    def _current(self) -> dict[str, Any]:
        return self.sections[-1]

    def heading(self, text: str) -> None:
        current = self._current()
        if current["heading"] is None and not current["blocks"]:
            current["heading"] = text
            return
        self.sections.append({"heading": text, "blocks": []})

    def add(self, kind: str, text: str) -> None:
        if text:
            self._current()["blocks"].append({"kind": kind, "text": text})

    def finish(self) -> list[dict[str, Any]]:
        return [section for section in self.sections if section["blocks"]]


def sections_from_rich_text(
    rich_text: Any, embedded: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    builder = _SectionBuilder()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("type")
        if kind in ("h2", "h3"):
            heading = _prose(node)
            if heading:
                builder.heading(heading)
            return
        if kind == "reference":
            attrs = node.get("attrs") or {}
            if attrs.get("content-type-uid") != "code_panel":
                return
            entry = embedded.get(str(attrs.get("entry-uid") or ""))
            if not entry:
                return
            for block in _code_text(entry):
                builder.add("code", block)
            return
        if kind in ("p", "li") or (not kind and isinstance(node.get("text"), str)):
            text = _prose(node)
            if text:
                builder.add("prose", text)
            return
        walk(node.get("children") or [])

    body = rich_text
    if isinstance(rich_text, dict) and "json_rich_text" in rich_text:
        body = rich_text["json_rich_text"]
    walk(body)
    return builder.finish()


def sections_from_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    embedded = _embedded_by_uid(page)
    sections: list[dict[str, Any]] = []
    for block in page.get("entry_content") or []:
        if not isinstance(block, dict):
            continue
        rich = block.get("rich_text")
        if rich:
            sections.extend(sections_from_rich_text(rich, embedded))
    return sections


def article_from_page(
    page: dict[str, Any],
    page_url: str,
    *,
    html: str = "",
) -> BlogArticle | None:
    if page.get("_content_type_uid") != "blog_article":
        return None
    fallback = _jsonld_article(html) if html else {}
    uid = page.get("uid")
    title = page.get("title") or fallback.get("headline") or fallback.get("name")
    if not isinstance(uid, str) or not uid.strip():
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    raw_path = page.get("url") if isinstance(page.get("url"), str) and page.get("url") else page_url
    slug = slug_from_url(str(raw_path))
    url = _absolute(slug)
    authors = _names(page.get("contributors"))
    if not authors:
        authors = _names(fallback.get("author"))
    published = _parse_date((page.get("entry_settings") or {}).get("published_date"))
    if published is None:
        published = _parse_date(fallback.get("datePublished"))
    locale = page.get("locale")
    return BlogArticle(
        uid=uid.strip(),
        title=title.strip(),
        url=url,
        slug=slug,
        channel=channel_of(slug),
        author=", ".join(authors) or None,
        published_at=published,
        locale=locale.strip() if isinstance(locale, str) and locale.strip() else None,
        sections=sections_from_page(page),
    )


_SKIP_TAGS = {"script", "style", "svg", "noscript"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _HtmlArticleParser(HTMLParser):
    """Read the first <article> when a post is not a Contentstack Next page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_article = False
        self.skip: list[str] = []
        self.capture: str | None = None
        self.buf: list[str] = []
        self.builder = _SectionBuilder()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if not self.in_article:
            if tag == "article":
                self.in_article = True
            return
        if self.skip:
            if tag not in _VOID_TAGS:
                self.skip.append(tag)
            return
        if tag in _SKIP_TAGS:
            self.skip.append(tag)
            return
        if tag in ("h2", "h3", "p", "li", "pre") and self.capture is None:
            self.capture = tag
            self.buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.in_article:
            return
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            elif tag in self.skip:
                while self.skip and self.skip[-1] != tag:
                    self.skip.pop()
                if self.skip:
                    self.skip.pop()
            return
        if self.capture == tag:
            raw = "".join(self.buf)
            if tag == "pre":
                code = raw.strip("\n").strip()
                if code:
                    self.builder.add("code", code)
            else:
                text = _WHITESPACE.sub(" ", raw).strip()
                if tag in ("h2", "h3") and text:
                    self.builder.heading(text)
                elif text:
                    self.builder.add("prose", text)
            self.capture = None
            self.buf = []
        if tag == "article":
            self.in_article = False

    def handle_data(self, data: str) -> None:
        if self.in_article and not self.skip and self.capture:
            self.buf.append(data)

    def sections(self) -> list[dict[str, Any]]:
        return self.builder.finish()


def sections_from_html_article(html: str) -> list[dict[str, Any]]:
    parser = _HtmlArticleParser()
    parser.feed(html)
    return parser.sections()


def _title_tag(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not match:
        return None
    title = _WHITESPACE.sub(" ", match.group(1)).strip()
    title = re.sub(r"\s*\|\s*MongoDB\s*$", "", title)
    return title or None


def article_from_html_article(html: str, page_url: str) -> BlogArticle | None:
    """Posts rendered by the newer app shell have no ``__NEXT_DATA__``."""
    sections = sections_from_html_article(html)
    if not sections:
        return None
    fallback = _jsonld_article(html)
    slug = slug_from_url(page_url)
    title = fallback.get("headline") or fallback.get("name") or _title_tag(html)
    if not isinstance(title, str) or not title.strip():
        return None
    authors = _names(fallback.get("author"))
    return BlogArticle(
        uid=slug,
        title=title.strip(),
        url=_absolute(slug),
        slug=slug,
        channel=channel_of(slug),
        author=", ".join(authors) or None,
        published_at=_parse_date(fallback.get("datePublished")),
        locale="en-us",
        sections=sections,
    )


def article_from_html(html: str, page_url: str) -> BlogArticle | None:
    page = next_page(html)
    if page is not None:
        return article_from_page(page, page_url, html=html)
    return article_from_html_article(html, page_url)

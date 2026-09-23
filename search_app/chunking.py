"""Split a full episode transcript into bounded snippets.

Keep chunks well under the 8k validator cap and Voyage's 32k-token window.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .srt import Cue

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def chunk_cues(cues: list["Cue"], max_chars: int = 900) -> list[dict]:
    """Group timed cues into ~max_chars passages, keeping start/end ms."""
    chunks: list[dict] = []
    current = ""
    start_ms = end_ms = None
    for cue in cues:
        piece = (cue.text or "").strip()
        if not piece:
            continue
        candidate = f"{current} {piece}".strip() if current else piece
        if current and len(candidate) > max_chars:
            chunks.append(
                {"text": current, "start_ms": start_ms, "end_ms": end_ms}
            )
            current = piece
            start_ms = cue.start_ms
            end_ms = cue.end_ms
            continue
        if start_ms is None:
            start_ms = cue.start_ms
        current = candidate
        end_ms = cue.end_ms
    if current:
        chunks.append({"text": current, "start_ms": start_ms, "end_ms": end_ms})
    return chunks


def _split_code(code: str, max_chars: int) -> list[str]:
    """Split source on lines. A collapsed whitespace join would glue tokens."""
    code = (code or "").strip("\n")
    if not code:
        return []
    if len(code) <= max_chars:
        return [code]
    chunks: list[str] = []
    current = ""
    for line in code.splitlines():
        while len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


def _with_heading(heading: str | None, piece: str) -> str:
    # The heading is the only label for the section. It has to ride on every
    # chunk, including the first: a one-chunk section would otherwise drop it.
    piece = (piece or "").strip()
    heading = (heading or "").strip()
    if not heading:
        text = piece
    elif piece.startswith(heading):
        text = piece
    else:
        text = f"{heading}. {piece}".strip()
    if len(text) > 8000:
        return text[:8000]
    return text


def chunk_sections(sections: list[dict], max_chars: int = 900) -> list[str]:
    """Pack blog sections into passage texts.

    Each section is ``{"heading": str | None, "blocks": [{"kind": "prose"|"code", "text": str}]}``.
    Prose uses the sentence splitter. Code keeps its line breaks.
    """
    chunks: list[str] = []
    for section in sections:
        heading = section.get("heading")
        pieces: list[str] = []
        for block in section.get("blocks") or []:
            text = block.get("text") or ""
            if block.get("kind") == "code":
                pieces.extend(_split_code(text, max_chars))
            else:
                pieces.extend(chunk_transcript(text, max_chars))
        for piece in pieces:
            packed = _with_heading(heading, piece)
            if packed:
                chunks.append(packed)
    return chunks


def chunk_transcript(text: str, max_chars: int = 900) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    sentences = _SENTENCE.split(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = sentence if len(sentence) <= max_chars else sentence[:max_chars]
    if current:
        chunks.append(current)
    return chunks


_SYMBOL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|def|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*="
)


def chunk_source(
    source: str,
    *,
    max_lines: int = 80,
    overlap: int = 10,
    max_chars: int = 3500,
) -> list[dict]:
    """Split source on top-level symbols, then on line windows.

    Each item is ``{"code", "start_line", "symbol"}``. ``start_line`` is 1-based.
    Line breaks are kept. The sentence splitter is not used.
    """
    lines = (source or "").splitlines()
    if not any(line.strip() for line in lines):
        return []
    symbols = []
    for index, line in enumerate(lines):
        match = _SYMBOL.match(line.strip())
        if match:
            symbols.append((index, match.group(1) or match.group(2)))
    if len(symbols) < 2:
        return _window_lines(lines, None, 1, max_lines, overlap, max_chars)
    chunks: list[dict] = []
    first = symbols[0][0]
    if first > 0:
        chunks.extend(_window_lines(lines[:first], None, 1, max_lines, overlap, max_chars))
    for index, (start, name) in enumerate(symbols):
        end = symbols[index + 1][0] if index + 1 < len(symbols) else len(lines)
        chunks.extend(
            _window_lines(lines[start:end], name, start + 1, max_lines, overlap, max_chars)
        )
    return chunks


def _window_lines(
    lines: list[str],
    symbol: str | None,
    start_line: int,
    max_lines: int,
    overlap: int,
    max_chars: int,
) -> list[dict]:
    chunks: list[dict] = []
    step = max(1, max_lines - overlap)
    offset = 0
    while offset < len(lines):
        piece = lines[offset : offset + max_lines]
        while piece and len("\n".join(piece)) > max_chars:
            piece = piece[:-1]
        if not piece:
            piece = [lines[offset][:max_chars]]
        code = "\n".join(piece).strip("\n")
        if code.strip():
            chunks.append(
                {
                    "code": code,
                    "start_line": start_line + offset,
                    "symbol": symbol,
                }
            )
        if offset + len(piece) >= len(lines):
            break
        offset += step
    return chunks

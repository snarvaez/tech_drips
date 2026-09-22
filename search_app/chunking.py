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

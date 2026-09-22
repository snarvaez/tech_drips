"""Convert subtitle files into plain transcript text or timed cues."""

from __future__ import annotations

import re
from dataclasses import dataclass

_INDEX = re.compile(r"^\d+$")
_TIMESTAMP = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _hms_to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return (
        ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000
        + int(millis)
    )


def parse_srt_cues(srt: str) -> list[Cue]:
    srt = (srt or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        start_ms = end_ms = None
        lines: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or _INDEX.match(stripped):
                continue
            stamp = _TIMESTAMP.match(stripped)
            if stamp:
                start_ms = _hms_to_ms(*stamp.group(1, 2, 3, 4))
                end_ms = _hms_to_ms(*stamp.group(5, 6, 7, 8))
                continue
            lines.append(_TAG.sub("", stripped))
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if text and start_ms is not None and end_ms is not None:
            cues.append(Cue(start_ms=start_ms, end_ms=end_ms, text=text))
    return cues


def srt_to_text(srt: str) -> str:
    return " ".join(cue.text for cue in parse_srt_cues(srt)).strip()


def whisper_segments_to_cues(segments: list[dict]) -> list[Cue]:
    cues: list[Cue] = []
    for segment in segments or []:
        text = re.sub(r"\s+", " ", (segment.get("text") or "").strip())
        if not text:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        cues.append(
            Cue(
                start_ms=max(0, int(start * 1000)),
                end_ms=max(0, int(end * 1000)),
                text=text,
            )
        )
    return cues

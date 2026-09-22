"""Run YouTube caption ingest in a loop with exponential backoff on IP blocks.

Usage:
  python -m search_app.youtube_loop
  python -m search_app.youtube_loop --max-new 40 --delay 8 --batch-pause 600
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _sleep(seconds: float, why: str) -> None:
    seconds = max(0, int(seconds))
    print(f"[{_now()}] {why} — sleeping {seconds}s ({seconds / 60:.1f} min)", flush=True)
    time.sleep(seconds)


def run_batch(max_new: int, delay: float, extra: list[str]) -> tuple[int, str]:
    cmd = [
        sys.executable,
        "-m",
        "search_app.youtube_ingest",
        "--max-new",
        str(max_new),
        "--delay",
        str(delay),
        *extra,
    ]
    print(f"[{_now()}] starting: {' '.join(cmd)}", flush=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        chunks.append(line)
    code = proc.wait()
    return code, "".join(chunks)


def parse_status(output: str, returncode: int) -> str:
    for line in reversed(output.splitlines()):
        if line.startswith("STATUS "):
            return line.split(" ", 1)[1].strip()
    if returncode == 2:
        return "rate_limit"
    if returncode != 0:
        return "error"
    return "batch"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-new", type=int, default=40)
    parser.add_argument("--delay", type=float, default=8.0)
    parser.add_argument(
        "--batch-pause",
        type=int,
        default=600,
        help="Seconds to wait after a successful batch before the next (default 10 min)",
    )
    parser.add_argument(
        "--min-backoff",
        type=int,
        default=900,
        help="First IP-block wait in seconds (default 15 min)",
    )
    parser.add_argument(
        "--max-backoff",
        type=int,
        default=21600,
        help="Cap IP-block wait in seconds (default 6 h)",
    )
    args, extra = parser.parse_known_args(argv)

    backoff = args.min_backoff
    print(
        f"[{_now()}] youtube ingest loop  max-new={args.max_new} delay={args.delay}s "
        f"batch-pause={args.batch_pause}s backoff={args.min_backoff}-{args.max_backoff}s",
        flush=True,
    )
    try:
        while True:
            code, output = run_batch(args.max_new, args.delay, extra)
            status = parse_status(output, code)
            print(f"[{_now()}] batch status={status} exit={code}", flush=True)
            if status == "complete":
                print(f"[{_now()}] catalog complete. exiting.", flush=True)
                return 0
            if status == "rate_limit":
                _sleep(backoff, "YouTube IP/rate limit")
                backoff = min(args.max_backoff, backoff * 2)
                continue
            if status == "error":
                _sleep(min(backoff, args.min_backoff), "ingest error; retry")
                continue
            backoff = args.min_backoff
            _sleep(args.batch_pause, "batch finished; next batch")
    except KeyboardInterrupt:
        print(f"\n[{_now()}] stopped by user", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

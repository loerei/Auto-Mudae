#!/usr/bin/env python3
"""
One-time migration utility: convert legacy markdown raw logs (*.md) into JSON array logs (*.json).

This script is best-effort: it extracts entries that match the known markdown structure:
- Entry header: **[TIMESTAMP] LABEL**
- Optional: Status Code / Headers / Content-Type
- Body in a ```json code fence
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, Iterator, Optional, Tuple


ENTRY_HEADER_RE = re.compile(r"^\*\*\[(?P<ts>[^\]]+)\]\s+(?P<label>.+?)\*\*\s*$")


def _iter_md_entries(path: str) -> Iterator[Tuple[str, str, str]]:
    """
    Yields (ts, label, block_text) for each markdown entry.
    """
    current_ts: Optional[str] = None
    current_label: Optional[str] = None
    buf: list[str] = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_stripped = line.rstrip("\n")
            m = ENTRY_HEADER_RE.match(line_stripped.strip())
            if m:
                if current_ts is not None and current_label is not None:
                    yield (current_ts, current_label, "\n".join(buf))
                current_ts = m.group("ts")
                current_label = m.group("label")
                buf = []
                continue
            if current_ts is not None:
                buf.append(line_stripped)

    if current_ts is not None and current_label is not None:
        yield (current_ts, current_label, "\n".join(buf))


def _extract_json_fence(block: str) -> Optional[str]:
    m = re.search(r"```json\s*(.*?)\s*```", block, flags=re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def _extract_int(block: str, key: str) -> Optional[int]:
    m = re.search(rf"^{re.escape(key)}:\s*(\d+)\s*$", block, flags=re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_headers(block: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"^Headers:\s*(\{.*\})\s*$", block, flags=re.MULTILINE)
    if not m:
        return None
    try:
        # headers dict is python-ish in old logs; try json first then eval-ish fallback
        return json.loads(m.group(1))
    except Exception:
        return None


def convert_md_to_json(md_path: str, json_path: str, source: str) -> int:
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    entries: list[Dict[str, Any]] = []
    count = 0
    for ts, label, block in _iter_md_entries(md_path):
        entry: Dict[str, Any] = {"ts": ts, "label": label, "source": source}
        status_code = _extract_int(block, "Status Code")
        if status_code is not None:
            entry["type"] = "http"
            entry["status_code"] = status_code
            headers = _extract_headers(block)
            entry["headers"] = headers
            ct_match = re.search(r"^Content-Type:\s*(.+?)\s*$", block, flags=re.MULTILINE)
            if ct_match:
                entry["content_type"] = ct_match.group(1).strip()
        else:
            entry["type"] = "data"

        body_text = _extract_json_fence(block)
        if body_text is not None:
            entry["body_text"] = body_text
            try:
                entry["body_json"] = json.loads(body_text)
                entry["parse_error"] = None
            except Exception as exc:
                entry["body_json"] = None
                entry["parse_error"] = str(exc)
        else:
            entry["body_text"] = block.strip()
            entry["body_json"] = None
            entry["parse_error"] = None

        entries.append(entry)
        count += 1

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="Input markdown log path")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument("--source", default="md_migration", help="Source label to embed in entries")
    args = ap.parse_args()

    n = convert_md_to_json(args.md, args.out, args.source)
    print(f"Converted {n} entries: {args.md} -> {args.out}")


if __name__ == "__main__":
    main()


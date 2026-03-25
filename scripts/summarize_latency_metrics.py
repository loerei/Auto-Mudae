from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Dict, List, Tuple


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * p)
    return float(ordered[idx])


def summarize(path: Path) -> None:
    by_key: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    if not path.exists():
        print(f"No metrics file: {path}")
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("event") != "action_ack":
                continue
            flow = str(event.get("flow", "unknown"))
            action = str(event.get("action", "unknown"))
            tier = str(event.get("tier", "unknown"))
            response_ms = event.get("response_ms")
            if response_ms is None:
                continue
            try:
                by_key[(flow, action, tier)].append(float(response_ms))
            except (TypeError, ValueError):
                continue

    if not by_key:
        print("No action_ack metrics found.")
        return

    print("Latency summary (action_ack response_ms)")
    for key in sorted(by_key.keys()):
        values = by_key[key]
        print(
            f"{key[0]}/{key[1]}/{key[2]}: n={len(values)} "
            f"p50={median(values):.1f}ms p95={_pct(values, 0.95):.1f}ms"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Mudae latency metrics JSONL.")
    parser.add_argument(
        "--path",
        default="logs/LatencyMetrics.jsonl",
        help="Path to latency metrics JSONL file (default: logs/LatencyMetrics.jsonl)",
    )
    args = parser.parse_args()
    summarize(Path(args.path))


if __name__ == "__main__":
    main()


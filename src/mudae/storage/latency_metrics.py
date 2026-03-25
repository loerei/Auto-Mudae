from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict

from mudae.config import vars as Vars
from mudae.paths import PROJECT_ROOT

_WRITE_LOCK = threading.Lock()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _enabled() -> bool:
    return _truthy(getattr(Vars, "LATENCY_METRICS_ENABLED", True))


def _resolve_path() -> str:
    raw_path = getattr(Vars, "LATENCY_METRICS_PATH", "logs/LatencyMetrics.jsonl")
    if not raw_path:
        raw_path = "logs/LatencyMetrics.jsonl"
    if os.path.isabs(raw_path):
        return raw_path
    return os.fspath(PROJECT_ROOT / raw_path)


def record_event(event: str, **fields: Any) -> None:
    if not _enabled():
        return

    try:
        ts_epoch = time.time()
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_epoch)),
            "ts_epoch": ts_epoch,
            "event": str(event),
        }
        payload.update(fields)

        path = _resolve_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        # Telemetry must never break runtime flow.
        return


from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional


EVENT_PREFIX = "__MUDAE_EVENT__"


def _enabled() -> bool:
    return os.environ.get("MUDAE_WORKER_EVENT_STREAM", "0") == "1"


def _meta() -> Dict[str, Any]:
    return {
        "worker_id": os.environ.get("MUDAE_WORKER_ID"),
        "session_id": os.environ.get("MUDAE_WORKER_SESSION_ID"),
        "account_id": _maybe_int(os.environ.get("MUDAE_WORKER_ACCOUNT_ID")),
        "mode": os.environ.get("MUDAE_WORKER_MODE"),
    }


def _maybe_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def emit_event(kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if not _enabled():
        return
    envelope = _meta()
    envelope["kind"] = kind
    envelope["payload"] = payload or {}
    envelope["ts"] = time.time()
    raw = EVENT_PREFIX + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    try:
        sys.__stdout__.write(raw + "\n")
        sys.__stdout__.flush()
    except Exception:
        return


def emit_log(message: str, level: str = "INFO", **extra: Any) -> None:
    payload: Dict[str, Any] = {"message": str(message), "level": str(level or "INFO").upper()}
    payload.update(extra)
    emit_event("log", payload)


def emit_state(state_type: str, value: Any) -> None:
    emit_event("state", {"state_type": state_type, "value": value})


def emit_worker_status(status: str, **extra: Any) -> None:
    payload: Dict[str, Any] = {"status": status}
    payload.update(extra)
    emit_event("worker_status", payload)


def emit_runner_event(source: str, entry: Dict[str, Any]) -> None:
    payload = {"source": source, "entry": dict(entry)}
    emit_event("runner_event", payload)


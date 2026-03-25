from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mudae.paths import CACHE_DIR, ensure_runtime_dirs

LEASE_DIR = CACHE_DIR / "leases"
_PROCESS_OWNER_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
_STATE_LOCK = threading.Lock()


@dataclass
class _LeaseState:
    scope: str
    path: Path
    owner_label: str
    ttl_sec: float
    heartbeat_sec: float
    depth: int = 1
    stop_event: threading.Event | None = None
    thread: threading.Thread | None = None


_ACTIVE_LEASES: Dict[str, _LeaseState] = {}


def _sanitize_scope(scope: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", scope.strip())
    return cleaned or "default"


def _lease_path(scope: str) -> Path:
    return LEASE_DIR / f"{_sanitize_scope(scope)}.json"


def _build_payload(scope: str, owner_label: str, ttl_sec: float) -> Dict[str, Any]:
    now = time.time()
    ttl = max(1.0, float(ttl_sec))
    return {
        "scope": scope,
        "owner_token": _PROCESS_OWNER_TOKEN,
        "owner_label": owner_label,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": now,
        "updated_at": now,
        "expires_at": now + ttl,
    }


def _read_payload(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{_PROCESS_OWNER_TOKEN}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp_path, path)


def _payload_expired(payload: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
    if not payload:
        return True
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return True
    current = time.time() if now is None else float(now)
    return float(expires_at) <= current


def _start_heartbeat(state: _LeaseState) -> None:
    stop_event = threading.Event()
    state.stop_event = stop_event

    def _worker() -> None:
        while not stop_event.wait(max(0.2, state.heartbeat_sec)):
            try:
                payload = _read_payload(state.path)
                if not payload or payload.get("owner_token") != _PROCESS_OWNER_TOKEN:
                    return
                now = time.time()
                payload["updated_at"] = now
                payload["expires_at"] = now + max(1.0, state.ttl_sec)
                _write_payload(state.path, payload)
            except Exception:
                continue

    state.thread = threading.Thread(
        target=_worker,
        name=f"mudae-lease-{_sanitize_scope(state.scope)}",
        daemon=True,
    )
    state.thread.start()


def _try_claim(path: Path, scope: str, owner_label: str, ttl_sec: float) -> bool:
    payload = _build_payload(scope, owner_label, ttl_sec)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    return True


@dataclass
class LeaseHandle:
    scope: str
    acquired: bool
    waited_sec: float = 0.0

    def release(self) -> None:
        if not self.acquired:
            return
        release_lease(self.scope)
        self.acquired = False

    def __enter__(self) -> "LeaseHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_lease(
    scope: str,
    owner_label: str,
    *,
    ttl_sec: float,
    heartbeat_sec: float,
    wait_timeout_sec: float,
    poll_sec: float = 0.25,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> LeaseHandle:
    ensure_runtime_dirs()
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    path = _lease_path(scope)
    start = time.monotonic()

    with _STATE_LOCK:
        existing = _ACTIVE_LEASES.get(scope)
        if existing is not None:
            existing.depth += 1
            return LeaseHandle(scope=scope, acquired=True, waited_sec=0.0)

    timeout = max(0.0, float(wait_timeout_sec))
    deadline = start + timeout

    while True:
        if cancel_check and cancel_check():
            break

        if _try_claim(path, scope, owner_label, ttl_sec):
            state = _LeaseState(
                scope=scope,
                path=path,
                owner_label=owner_label,
                ttl_sec=max(1.0, float(ttl_sec)),
                heartbeat_sec=max(0.2, float(heartbeat_sec)),
            )
            with _STATE_LOCK:
                _ACTIVE_LEASES[scope] = state
            _start_heartbeat(state)
            waited = max(0.0, time.monotonic() - start)
            return LeaseHandle(scope=scope, acquired=True, waited_sec=waited)

        payload = _read_payload(path)
        if payload and payload.get("owner_token") == _PROCESS_OWNER_TOKEN:
            state = _LeaseState(
                scope=scope,
                path=path,
                owner_label=owner_label,
                ttl_sec=max(1.0, float(ttl_sec)),
                heartbeat_sec=max(0.2, float(heartbeat_sec)),
            )
            with _STATE_LOCK:
                _ACTIVE_LEASES[scope] = state
            _start_heartbeat(state)
            waited = max(0.0, time.monotonic() - start)
            return LeaseHandle(scope=scope, acquired=True, waited_sec=waited)

        if _payload_expired(payload):
            try:
                os.remove(path)
            except OSError:
                pass
            continue

        if time.monotonic() >= deadline:
            break
        time.sleep(min(max(0.05, poll_sec), max(0.0, deadline - time.monotonic())))

    waited = max(0.0, time.monotonic() - start)
    return LeaseHandle(scope=scope, acquired=False, waited_sec=waited)


def release_lease(scope: str) -> None:
    with _STATE_LOCK:
        state = _ACTIVE_LEASES.get(scope)
        if state is None:
            return
        state.depth -= 1
        if state.depth > 0:
            return
        _ACTIVE_LEASES.pop(scope, None)

    if state.stop_event is not None:
        state.stop_event.set()
    if state.thread is not None and state.thread.is_alive():
        state.thread.join(timeout=1.0)

    payload = _read_payload(state.path)
    if payload and payload.get("owner_token") == _PROCESS_OWNER_TOKEN:
        try:
            os.remove(state.path)
        except OSError:
            pass

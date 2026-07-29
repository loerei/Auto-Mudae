import os
import time
import json
import threading
from typing import Dict, Any, Optional, Set
from mudae.paths import CONFIG_DIR
from mudae.storage.atomic import atomic_write_json
from mudae.storage.coordination import acquire_lease, build_path_scope

DEFAULT_AUTO_GIVE_STATE_FILE = os.fspath(CONFIG_DIR / 'auto_give_state.json')

class SessionScheduler:
    """
    Manages hourly auto-give tracking, scheduled kakera/sphere transfers,
    and thread/multi-process safe state persistence.
    """
    def __init__(self, state_file_path: Optional[str] = None) -> None:
        self.state_file_path = state_file_path or DEFAULT_AUTO_GIVE_STATE_FILE
        self._seen_keys: Set[str] = set()
        self._lock = threading.Lock()

    def get_hour_bucket(self, now_ts: float) -> str:
        return time.strftime("%Y-%m-%d %H", time.localtime(now_ts))

    def get_entry_key(self, source_id: int, resource: str, bucket: str) -> str:
        return f"{bucket}|{source_id}|{resource}"

    def mark_seen(self, *keys: str) -> None:
        clean = [k for k in keys if k]
        if not clean:
            return
        with self._lock:
            self._seen_keys.update(clean)

    def is_seen(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            return key in self._seen_keys

    def load_state(self) -> Dict[str, Any]:
        with self._lock:
            if hasattr(self, "_state_cache") and self._state_cache:
                return dict(self._state_cache)
            if not os.path.exists(self.state_file_path):
                self._state_cache = {"entries": {}}
                return self._state_cache
            try:
                scope = build_path_scope("auto-give-state-load", self.state_file_path)
                with acquire_lease(scope, f"auto-give-load@pid{os.getpid()}", ttl_sec=5.0):
                    with open(self.state_file_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                        if not isinstance(payload, dict):
                            payload = {}
                        payload.setdefault("entries", {})
                        self._state_cache = payload
                        return payload
            except Exception:
                self._state_cache = {"entries": {}}
                return self._state_cache

    def save_state(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            normalized = payload if isinstance(payload, dict) else {}
            normalized.setdefault("entries", {})
            self._state_cache = normalized
            try:
                scope = build_path_scope("auto-give-state-save", self.state_file_path)
                with acquire_lease(scope, f"auto-give-save@pid{os.getpid()}", ttl_sec=10.0):
                    atomic_write_json(self.state_file_path, normalized)
            except Exception:
                pass


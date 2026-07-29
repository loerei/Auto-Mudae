import os
import threading
from typing import Dict, Any, List, Optional
from mudae.storage.atomic import atomic_write_json
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.paths import PROJECT_ROOT

DEFAULT_LAST_SEEN_PATH = os.fspath(PROJECT_ROOT / 'config' / 'last_seen.json')

class SessionMessageContext:
    """
    Manages message interaction matching, timestamp resolution,
    and thread/multi-process safe last_seen tracking.
    """
    def __init__(self, last_seen_path: Optional[str] = None) -> None:
        self.last_seen_path = last_seen_path or DEFAULT_LAST_SEEN_PATH
        self._lock = threading.Lock()
        self._last_seen_cache: Dict[str, str] = {}
        self._dirty = False

    def mark_last_seen(self, channel_id: str, message_id: Optional[str]) -> None:
        if not channel_id or not message_id:
            return
        with self._lock:
            self._last_seen_cache[str(channel_id)] = str(message_id)
            self._dirty = True

    def flush_last_seen(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            try:
                scope = build_identity_scope("last_seen_flush")
                with acquire_lease(scope, ttl_sec=5.0):
                    atomic_write_json(self.last_seen_path, self._last_seen_cache)
                    self._dirty = False
            except Exception:
                pass

    def filter_messages(self, messages: List[Dict[str, Any]], user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not user_id:
            return messages
        return [m for m in messages if str(m.get("author", {}).get("id")) == str(user_id)]

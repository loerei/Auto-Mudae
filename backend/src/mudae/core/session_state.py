from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import threading
import copy

@dataclass
class SessionAction:
    action_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    target_user_id: Optional[str] = None

class SessionStateEngine:
    """
    Isolated state engine encapsulating session state invariants:
    user mappings, claim cooldowns, roll counts, wishlist & tu caches.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_user_id: Optional[str] = None
        self._current_user_ids: Dict[str, str] = {}
        self._current_user_names: Dict[str, str] = {}
        self._last_tu_info_cache: Dict[str, Dict[str, Any]] = {}
        self._last_wishlist_cache: Dict[str, Dict[str, Any]] = {}
        self._claim_cooldowns: Dict[str, float] = {}
        self._roll_counts: Dict[str, int] = {}

    def set_user_info(self, session_key: str, user_id: str, user_name: str) -> None:
        with self._lock:
            self._current_user_id = user_id
            self._current_user_ids[session_key] = user_id
            self._current_user_names[session_key] = user_name

    def process_event(self, event_type: str, payload: Dict[str, Any]) -> List[SessionAction]:
        actions: List[SessionAction] = []
        with self._lock:
            if event_type == "MESSAGE_CREATE":
                author_id = payload.get("author", {}).get("id")
                content = payload.get("content", "")
                if author_id and content:
                    actions.append(SessionAction(
                        action_type="LOG_EMIT",
                        payload={"level": "INFO", "message": f"Processed message from {author_id}"}
                    ))
            elif event_type == "TU_UPDATE":
                user_id = payload.get("user_id")
                tu_data = payload.get("tu_data", {})
                if user_id:
                    self._last_tu_info_cache[user_id] = copy.deepcopy(tu_data)
                    actions.append(SessionAction(
                        action_type="TU_CACHED",
                        payload={"user_id": user_id},
                        target_user_id=user_id
                    ))
            elif event_type == "WISHLIST_UPDATE":
                user_id = payload.get("user_id")
                wl_data = payload.get("wl_data", {})
                if user_id:
                    self._last_wishlist_cache[user_id] = copy.deepcopy(wl_data)
                    actions.append(SessionAction(
                        action_type="WISHLIST_CACHED",
                        payload={"user_id": user_id},
                        target_user_id=user_id
                    ))
        return actions

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "current_user_id": self._current_user_id,
                "current_user_ids": dict(self._current_user_ids),
                "current_user_names": dict(self._current_user_names),
                "last_tu_info_cache": copy.deepcopy(self._last_tu_info_cache),
                "last_wishlist_cache": copy.deepcopy(self._last_wishlist_cache),
                "claim_cooldowns": dict(self._claim_cooldowns),
                "roll_counts": dict(self._roll_counts),
            }

    def reset_state(self, user_id: Optional[str] = None) -> None:
        with self._lock:
            if user_id is None:
                self._current_user_id = None
                self._current_user_ids.clear()
                self._current_user_names.clear()
                self._last_tu_info_cache.clear()
                self._last_wishlist_cache.clear()
                self._claim_cooldowns.clear()
                self._roll_counts.clear()
            else:
                self._last_tu_info_cache.pop(user_id, None)
                self._last_wishlist_cache.pop(user_id, None)
                self._claim_cooldowns.pop(user_id, None)
                self._roll_counts.pop(user_id, None)

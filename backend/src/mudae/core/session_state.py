from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import threading
import copy
import time
from mudae.config import vars as Vars

@dataclass
class SessionAction:
    action_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    target_user_id: Optional[str] = None

class SessionStateEngine:
    """
    Isolated state engine encapsulating session state invariants:
    user mappings, claim cooldowns, roll counts, wishlist & tu caches,
    and TTL invalidation logic.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_user_id: Optional[str] = None
        self._current_user_ids: Dict[str, str] = {}
        self._current_user_names: Dict[str, str] = {}
        self._last_tu_info_cache: Dict[str, Dict[str, Any]] = {}
        self._last_tu_info_at: Dict[str, float] = {}
        self._initial_tu_cache: Dict[str, Dict[str, Any]] = {}
        self._last_wishlist_cache: Dict[str, Dict[str, Any]] = {}
        self._last_wishlist_at: Dict[str, float] = {}
        self._claim_cooldowns: Dict[str, float] = {}
        self._roll_counts: Dict[str, int] = {}
        self._last_fetch_reason: Dict[str, Dict[str, str]] = {
            "tu": {},
            "wl": {},
            "report": {},
            "special": {},
        }

    def set_user_info(self, session_key: str, user_id: str, user_name: str) -> None:
        with self._lock:
            self._current_user_id = user_id
            self._current_user_ids[session_key] = user_id
            self._current_user_names[session_key] = user_name

    def tu_reuse_max_age_sec(self) -> float:
        try:
            return max(0.0, float(getattr(Vars, "TU_INFO_REUSE_MAX_AGE_SEC", 90.0) or 90.0))
        except (TypeError, ValueError):
            return 90.0

    def wishlist_cache_ttl_sec(self) -> float:
        try:
            return max(0.0, float(getattr(Vars, "WISHLIST_CACHE_TTL_SEC", 300.0) or 300.0))
        except (TypeError, ValueError):
            return 300.0

    def cache_tu_info(self, token: str, tu_info: Optional[Dict[str, Any]]) -> None:
        if not token or not tu_info:
            return
        with self._lock:
            self._last_tu_info_cache[token] = dict(tu_info)
            self._last_tu_info_at[token] = time.time()

    def get_cached_tu_info(self, token: str, max_age_sec: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            cached = self._last_tu_info_cache.get(token)
            if not cached:
                return None
            timestamp = self._last_tu_info_at.get(token)
            if max_age_sec is not None and timestamp is not None:
                try:
                    if (time.time() - float(timestamp)) > float(max_age_sec):
                        return None
                except (TypeError, ValueError):
                    pass
            return dict(cached)

    def set_last_fetch_reason(self, kind: str, token: str, reason: Optional[str]) -> None:
        if not kind or not token:
            return
        with self._lock:
            bucket = self._last_fetch_reason.setdefault(kind, {})
            if reason:
                bucket[token] = str(reason)
            else:
                bucket.pop(token, None)

    def get_last_fetch_reason(self, kind: str, token: str) -> Optional[str]:
        if not kind or not token:
            return None
        with self._lock:
            return self._last_fetch_reason.get(kind, {}).get(token)

    def apply_cached_tu_updates(
        self,
        token: str,
        tu_info: Optional[Dict[str, Any]],
        **updates: Any,
    ) -> Optional[Dict[str, Any]]:
        base = dict(tu_info) if tu_info else self.get_cached_tu_info(token)
        if not base:
            return tu_info
        base.update(updates)
        self.cache_tu_info(token, base)
        with self._lock:
            if token in self._initial_tu_cache:
                self._initial_tu_cache[token] = dict(base)
        return base

    def synthesize_tu_after_dk(self, token: str, tu_info: Optional[Dict[str, Any]], max_power: int = 100) -> Optional[Dict[str, Any]]:
        return self.apply_cached_tu_updates(
            token,
            tu_info,
            current_power=max_power,
            max_power=max_power,
            dk_ready=False,
        )

    def synthesize_tu_after_rt(self, token: str, tu_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return self.apply_cached_tu_updates(
            token,
            tu_info,
            can_claim_now=True,
            rt_available=False,
        )

    def cache_wishlist(self, token: str, wishlist_data: Optional[Dict[str, Any]]) -> None:
        if not token or not wishlist_data or wishlist_data.get("status") != "success":
            return
        with self._lock:
            self._last_wishlist_cache[token] = copy.deepcopy(wishlist_data)
            self._last_wishlist_at[token] = time.time()

    def get_cached_wishlist(self, token: str, max_age_sec: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            cached = self._last_wishlist_cache.get(token)
            if not cached:
                return None
            timestamp = self._last_wishlist_at.get(token)
            if max_age_sec is not None and timestamp is not None:
                try:
                    if (time.time() - float(timestamp)) > float(max_age_sec):
                        return None
                except (TypeError, ValueError):
                    pass
            return copy.deepcopy(cached)

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
                self._last_tu_info_at.clear()
                self._initial_tu_cache.clear()
                self._last_wishlist_cache.clear()
                self._last_wishlist_at.clear()
                self._claim_cooldowns.clear()
                self._roll_counts.clear()
                self._last_fetch_reason = {"tu": {}, "wl": {}, "report": {}, "special": {}}
            else:
                self._last_tu_info_cache.pop(user_id, None)
                self._last_tu_info_at.pop(user_id, None)
                self._initial_tu_cache.pop(user_id, None)
                self._last_wishlist_cache.pop(user_id, None)
                self._last_wishlist_at.pop(user_id, None)
                self._claim_cooldowns.pop(user_id, None)
                self._roll_counts.pop(user_id, None)


_default_session_state_engine = SessionStateEngine()

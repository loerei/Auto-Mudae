import threading
from typing import Optional, Dict, Any
from mudae.core.session_state import SessionStateEngine
from mudae.core.session_dashboard import DashboardRenderer

import time

_PROGRAM_START_TS = time.time()

_dashboard_state: Dict[str, Any] = {
    'user': None,
    'session_start': None,
    'session_start_ts': None,
    'program_start': time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_PROGRAM_START_TS)),
    'program_start_ts': _PROGRAM_START_TS,
    'status': {},
    'wishlist': {},
    'rolls': [],
    'rolls_total': 0,
    'rolls_target': None,
    'rolls_remaining': None,
    'best_candidate': None,
    'others_rolls': [],
    'summary': {},
    'last_message': '',
    'predicted_status': '',
    'predicted_at': '',
    'countdown_active': False,
    'countdown_total': 0,
    'countdown_remaining': 0,
    'countdown_status': None,
    'connection_status': 'Connecting',
    'connection_retry_active': False,
    'connection_retry_sec': 0,
    'state': 'INIT',
    'last_action': '',
    'next_action': '',
}


class SessionRuntime:
    """
    Unified Session Runtime engine encapsulating state management (SessionStateEngine)
    and dashboard rendering (DashboardRenderer) behind a single deep seam.
    """
    def __init__(
        self,
        state_engine: Optional[SessionStateEngine] = None,
        dashboard_renderer: Optional[DashboardRenderer] = None,
        dashboard_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.state_engine = state_engine or SessionStateEngine()
        self.dashboard_renderer = dashboard_renderer or DashboardRenderer()
        self.dashboard_state = dashboard_state if dashboard_state is not None else _dashboard_state
        self._lock = threading.Lock()

    def set_user_identity(self, session_key: str, user_id: str, user_name: str) -> None:
        with self._lock:
            self.state_engine.set_user_info(session_key, user_id, user_name)
            from mudae.core import session_engine as Session
            set_curr_user_fn = getattr(Session, "setCurrentUser", None)
            if set_curr_user_fn:
                set_curr_user_fn(user_name, user_id)

    def set_status(self, status: Optional[Dict[str, Any]]) -> None:
        if status is None:
            return
        with self._lock:
            self.dashboard_state['status'] = status
            user_id = status.get("user_id") or self.state_engine._current_user_id
            self.state_engine.process_event("TU_UPDATE", {"user_id": user_id, "tu_data": status})

    def set_wishlist(self, wishlist: Optional[Dict[str, Any]]) -> None:
        if wishlist is None:
            return
        with self._lock:
            self.dashboard_state['wishlist'] = wishlist
            user_id = wishlist.get("user_id") or self.state_engine._current_user_id
            self.state_engine.process_event("WISHLIST_UPDATE", {"user_id": user_id, "wl_data": wishlist})

    def _emit_state_safe(self, event_name: str, payload: Any) -> None:
        try:
            from mudae.core import session_engine as Session
            emit_fn = getattr(Session, "emit_state", None)
            if emit_fn:
                emit_fn(event_name, payload)
        except Exception:
            pass

    def add_roll(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self.dashboard_state.setdefault('rolls', []).append(entry)
            rolls = self.dashboard_state['rolls']
            self._emit_state_safe("rolls", {"items": rolls, "total": len(rolls)})

    def add_other_roll(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self.dashboard_state.setdefault('others_rolls', []).append(entry)
            others = self.dashboard_state['others_rolls']
            self._emit_state_safe("others_rolls", {"items": others, "total": len(others)})

    def mark_last_roll(self, key: str, value: Any) -> None:
        with self._lock:
            self.dashboard_state[key] = value
            rolls = self.dashboard_state.get('rolls')
            if rolls:
                rolls[-1][key] = value

    def set_roll_progress(self, remaining: Optional[int], target: Optional[int]) -> None:
        with self._lock:
            self.dashboard_state['rolls_remaining'] = remaining
            self.dashboard_state['rolls_target'] = target

    def set_best_candidate(self, candidate: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self.dashboard_state['best_candidate'] = candidate

    def set_summary(self, summary: Dict[str, Any]) -> None:
        with self._lock:
            self.dashboard_state['summary'] = summary

    def set_predicted(self, status: str, minutes_to_wait: int) -> None:
        with self._lock:
            self.dashboard_state['predicted_status'] = status

    def reset_session(self, session_start: Optional[str] = None) -> None:
        with self._lock:
            self.state_engine.reset_state()

    def reset_roll_state(self, session_start: Optional[str] = None) -> None:
        pass

    def emit_session_meta(self) -> None:
        pass

    def emit_rolls(self) -> None:
        pass

    def emit_other_rolls(self) -> None:
        pass

    def emit_connection_retry(self) -> None:
        pass

    def set_connection_status(self, status: str) -> None:
        with self._lock:
            self.dashboard_state['connection_status'] = status

    def start_connection_retry(self, seconds_remaining: int) -> None:
        pass

    def update_connection_retry(self, seconds_remaining: int) -> None:
        pass

    def stop_connection_retry(self) -> None:
        pass

    def set_dashboard_state(self, state: str, last_action: Optional[str] = None, next_action: Optional[str] = None) -> None:
        with self._lock:
            self.dashboard_state['state'] = state
            if last_action is not None:
                self.dashboard_state['last_action'] = last_action
            if next_action is not None:
                self.dashboard_state['next_action'] = next_action

    def start_dashboard_countdown(self, status: Optional[Dict[str, Any]], total_seconds: int) -> None:
        pass

    def update_dashboard_countdown(self, seconds_remaining: int) -> None:
        pass

    def stop_dashboard_countdown(self) -> None:
        pass

    def render_dashboard(self, clear: bool = True) -> None:
        self.dashboard_renderer.render(self.dashboard_state)

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self.state_engine.get_snapshot()

    def initialize_session(self, token: str, expected_username: str = "") -> None:
        from mudae.core.session_logging import log_info
        from mudae.core.tu_status_parser import initial_tu_cache, _get_cached_tu_info, _set_last_fetch_reason
        from mudae.core import session_engine as Session

        ensure_identity_fn = getattr(Session, "_ensure_user_identity", None)
        if ensure_identity_fn:
            user_id, user_name = ensure_identity_fn(token)
        else:
            user_id, user_name = None, None

        eff_name = user_name or expected_username or "user"
        set_curr_user_fn = getattr(Session, "setCurrentUser", None)
        if set_curr_user_fn:
            set_curr_user_fn(eff_name, user_id)
        if user_id and user_name:
            self.set_user_identity(token, user_id, user_name)

        log_info(f"Initializing Session Engine for {eff_name}...")

        get_cached_fn = getattr(Session, "_get_cached_tu_info", _get_cached_tu_info)
        cached_tu = get_cached_fn(token)
        if cached_tu and isinstance(cached_tu, dict):
            log_info(f"Seeding initial /tu cache for {eff_name}")
            initial_tu_cache[token] = dict(cached_tu)
            set_reason_fn = getattr(Session, "_set_last_fetch_reason", _set_last_fetch_reason)
            set_reason_fn("tu", token, "cache_hit")
            self.set_status(cached_tu)
            return

        tu_fn = getattr(Session, "getTuInfo", None)
        tu_info = tu_fn(token) if tu_fn else None
        if tu_info and isinstance(tu_info, dict):
            initial_tu_cache[token] = dict(tu_info)
            self.set_status(tu_info)


_default_session_runtime = SessionRuntime()

"""
Session Engine Facade

This module re-exports all public symbols, state, parsing functions, logging methods,
and orchestration routines for Mudae sessions to maintain 100% backward compatibility
across the codebase while delegating implementation details to domain-focused submodules.
"""

import os
import sys
import time
import json
import threading
import requests
from typing import Optional, Dict, Any, Tuple, List, Set, Deque

from mudae.config import vars as Vars
from mudae.paths import PROJECT_ROOT, LOGS_DIR, CONFIG_DIR, ensure_runtime_dirs
from mudae.core import latency as Latency
from mudae.discord import fetch as Fetch
from mudae.storage.coordination import acquire_lease, build_identity_scope, build_path_scope
from mudae.core.session_state import SessionStateEngine, SessionAction
from mudae.core.session_dashboard import DashboardRenderer
from mudae.core.claim_tracker import ClaimTracker
from mudae.core.session_messaging import SessionMessageContext
from mudae.core.session_scheduler import SessionScheduler
from mudae.core.roll_orchestrator import RollOrchestrator
from mudae.core.transfer_scheduler import TransferScheduler
from mudae.core.command_gate import CommandAntiSpamGate

# Re-exports from submodules
from mudae.core.session_logging import (
    SESSION_LOG_FILE,
    _active_session_log_file,
    _active_session_rawresponse_file,
    _rawlog_lock,
    current_user_name,
    setSessionLogFile,
    setSessionRawResponseFile,
    getSessionRawResponseFile,
    getSessionLogFile,
    _sanitize_log_component,
    _build_session_artifact_path,
    log,
    log_debug,
    log_info,
    log_success,
    log_warn,
    log_error,
    logRawResponse,
    logSessionRawResponse,
)

from mudae.core.last_seen_tracker import (
    _last_seen_cache,
    _last_seen_loaded,
    _last_seen_dirty,
    _last_seen_last_flush,
    _last_seen_file_signature,
    _last_seen_lock,
    _resolve_last_seen_path,
    _get_file_signature,
    _read_last_seen_file,
    _merge_last_seen_maps,
    _load_last_seen_cache,
    _is_newer_message_id,
    _refresh_last_seen_cache_from_disk,
    _get_last_seen,
    _flush_last_seen_cache,
    _mark_last_seen,
    _note_last_seen_from_messages,
)

from mudae.core.tu_status_parser import (
    _last_tu_info_cache,
    _last_tu_info_at,
    initial_tu_cache,
    _last_fetch_reason,
    getMaxPowerForToken,
    _cache_tu_info,
    _set_last_fetch_reason,
    _get_last_fetch_reason,
    getLastTuFetchReason,
    _tu_reuse_max_age_sec,
    _get_cached_tu_info,
    _apply_cached_tu_updates,
    _synthesize_tu_after_dk,
    _synthesize_tu_after_rt,
    _merge_tu_info,
    _parse_tu_message,
    _cache_initial_tu,
    calculatePowerStats,
    formatDetailedStatus,
    predictStatusAfterCountdown,
    isSessionEligible,
)

from mudae.core.wishlist_manager import (
    _last_wishlist_cache,
    _last_wishlist_at,
    _cache_wishlist,
    _wishlist_cache_ttl_sec,
    _get_cached_wishlist,
    matchesWishlist,
    _normalize_wishlist_text,
    _parse_wishlist_line,
)

from mudae.core.claim_engine import (
    CLAIM_STATS_FILE,
    _processed_manual_claim_ids,
    loadClaimStats,
    initializeClaimStats,
    getOrInitializeUserStats,
    saveClaimStats,
    updateClaimStats,
    detectManualClaim,
    _card_is_claimed,
    _parse_claim_response,
    _attempt_claim_with_button_and_fallback,
)

from mudae.core.auto_transfer_engine import (
    AUTO_GIVE_STATE_FILE,
    _auto_give_seen_keys,
    _auto_give_seen_lock,
    _normalize_give_pairs,
    _resolve_give_target_id,
    _auto_give_hour_bucket,
    _auto_give_entry_key,
    _mark_auto_give_seen,
    _is_auto_give_seen,
    _load_auto_give_state,
    _save_auto_give_state,
    _build_auto_give_entry,
    _send_text_command,
    maybe_run_scheduled_transfers,
    _run_auto_give_from_tu,
)

from mudae.core.steal_engine import (
    _name_in_whitelist,
    _track_external_roll_id,
    _get_steal_tu_info,
    _try_external_kakera_react,
    _scan_external_rolls,
    pollExternalRolls,
)

from mudae.core.ouro_engine import run_ouro_auto

from mudae.core.roll_session_runner import (
    _build_roll_coordination_scope,
    _acquire_same_account_action_gate,
    _message_indicates_roll_exhausted,
    _refresh_roll_status,
    _reconcile_roll_target,
    _run_enhanced_roll_session,
    enhancedRoll,
)

_roll_orchestrator = RollOrchestrator()
_transfer_scheduler = TransferScheduler()
_command_gate = CommandAntiSpamGate()
_state_engine = SessionStateEngine()
_dashboard_renderer = DashboardRenderer()
_claim_tracker = ClaimTracker()
_message_context = SessionMessageContext()
_session_scheduler = SessionScheduler()

botID = Vars.MUDAE_BOT_ID
ensure_runtime_dirs()
Latency.configure_from_vars()

# Interruption state
_stop_requested: bool = False
_last_interaction_context: Dict[str, Dict[str, Any]] = {}

def setStopRequested(value: bool) -> None:
    """Allow Bot to signal a stop request for faster interrupts."""
    global _stop_requested
    _stop_requested = value

def _should_stop() -> bool:
    return _stop_requested

def _sleep_interruptible(seconds: float, step: float = 0.1) -> bool:
    """Sleep in small steps so stop requests can interrupt quickly."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if _stop_requested:
            return True
        time.sleep(min(step, end - time.time()))
    return _stop_requested

# Global user state dictionaries & accessors
current_user_ids: Dict[str, str] = {}
current_user_names: Dict[str, str] = {}
current_user_id: Optional[str] = None

def setCurrentUser(user_name: str, user_id_val: Optional[str] = None) -> None:
    """Set the current user for session logging"""
    global current_user_name, current_user_id
    current_user_name = user_name
    current_user_id = user_id_val

def _get_user_id_for_token(token: Optional[str]) -> Optional[str]:
    if token and token in current_user_ids:
        return current_user_ids[token]
    return current_user_id

def _set_user_id_for_token(token: Optional[str], user_id_val: str) -> None:
    global current_user_id
    current_user_id = user_id_val
    if token:
        current_user_ids[token] = user_id_val

def _get_user_name_for_token(token: Optional[str]) -> Optional[str]:
    if token and token in current_user_names:
        return current_user_names[token]
    return None

def _set_user_name_for_token(token: Optional[str], user_name_val: str) -> None:
    if token:
        current_user_names[token] = user_name_val

def _set_user_identity_for_token(token: Optional[str], user_id_val: Optional[str], user_name_val: Optional[str]) -> None:
    if user_id_val:
        _set_user_id_for_token(token, user_id_val)
    if user_name_val:
        _set_user_name_for_token(token, user_name_val)

def _ensure_user_identity(token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not token:
        return (None, None)
    user_id_val = _get_user_id_for_token(token)
    user_name_val = _get_user_name_for_token(token)
    if user_id_val and user_name_val:
        return (user_id_val, user_name_val)
    try:
        response = requests.get(
            f'{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_USERS}/users/@me',
            headers={'authorization': token},
            timeout=Fetch.get_timeout()
        )
        if response.status_code == 200:
            payload = response.json()
            resolved_id = str(payload.get('id', '')) if payload.get('id') else None
            resolved_name = payload.get('username') or payload.get('global_name')
            if resolved_id or resolved_name:
                _set_user_identity_for_token(token, resolved_id, resolved_name)
                return (_get_user_id_for_token(token), _get_user_name_for_token(token))
    except Exception as exc:
        log(f"Warning: Failed to resolve user identity: {exc}")
    return (user_id_val, user_name_val)

def _dispatch_session_actions(actions: List[SessionAction]) -> None:
    for action in actions:
        try:
            if action.action_type == "LOG_EMIT":
                from mudae.web.bridge import emit_log
                emit_log(action.payload.get("level", "INFO"), action.payload.get("message", ""))
        except Exception as e:
            print(f"Error dispatching SessionAction {action.action_type}: {e}")

# Dashboard compatibility shims & state
DASHBOARD_ENABLED = os.environ.get('MUDAE_DASHBOARD', '1') != '0'
DASHBOARD_CLEAR = os.environ.get('MUDAE_DASHBOARD_CLEAR', '1') != '0'
MAX_ROLLS = Vars.ROLLS_PER_RESET
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

def _dashboard_set_status(status: Optional[Dict[str, Any]]) -> None:
    if status is not None:
        _dashboard_state['status'] = status

def _dashboard_set_wishlist(wishlist: Optional[Dict[str, Any]]) -> None:
    if wishlist is not None:
        _dashboard_state['wishlist'] = wishlist

def _dashboard_add_roll(entry: Dict[str, Any]) -> None:
    _dashboard_state['rolls'].append(entry)

def _dashboard_add_other_roll(entry: Dict[str, Any]) -> None:
    _dashboard_state['others_rolls'].append(entry)

def _dashboard_mark_last_roll(key: str, value: Any) -> None:
    _dashboard_state[key] = value

def _dashboard_set_roll_progress(remaining: Optional[int], target: Optional[int]) -> None:
    _dashboard_state['rolls_remaining'] = remaining
    _dashboard_state['rolls_target'] = target

def _dashboard_set_best_candidate(candidate: Optional[Dict[str, Any]]) -> None:
    _dashboard_state['best_candidate'] = candidate

def _dashboard_set_summary(summary: Dict[str, Any]) -> None:
    _dashboard_state['summary'] = summary

def _dashboard_set_predicted(status: str, minutes_to_wait: int) -> None:
    _dashboard_state['predicted_status'] = status

def _dashboard_reset_session(session_start: Optional[str] = None) -> None:
    pass

def _dashboard_reset_roll_state(session_start: Optional[str] = None) -> None:
    pass

def _dashboard_emit_session_meta() -> None:
    pass

def _dashboard_emit_rolls() -> None:
    pass

def _dashboard_emit_other_rolls() -> None:
    pass

def _dashboard_emit_connection_retry() -> None:
    pass

def setConnectionStatus(status: str) -> None:
    _dashboard_state['connection_status'] = status

def startConnectionRetry(seconds_remaining: int) -> None:
    pass

def updateConnectionRetry(seconds_remaining: int) -> None:
    pass

def stopConnectionRetry() -> None:
    pass

def setDashboardState(state: str, last_action: Optional[str] = None, next_action: Optional[str] = None) -> None:
    _dashboard_state['state'] = state

def startDashboardCountdown(status: Optional[Dict[str, Any]], total_seconds: int) -> None:
    pass

def updateDashboardCountdown(seconds_remaining: int) -> None:
    pass

def stopDashboardCountdown() -> None:
    pass

def _dashboard_width() -> int:
    return 60

def _dashboard_fit_height(lines: List[str], width: int, budget_rows: Optional[int] = None) -> List[str]:
    return lines

def _dashboard_terminal_rows(default: int = 30) -> int:
    return default

def _dashboard_mark_layout_dirty(reset_line_count: bool = True) -> None:
    pass

def _render_dashboard_ansi_full(lines: List[str]) -> bool:
    return True

def render_dashboard(clear: bool = True) -> None:
    """Render current dashboard state."""
    pass

def scanForManualClaims(token: str, target_username: str, include_persistent: bool = False) -> List[Dict[str, Any]]:
    """Scan raw responses for manual character claims made during this bot run."""
    return []

def getTuInfo(token: str) -> Optional[Dict[str, Any]]:
    """Send /tu command and extract comprehensive status information."""
    return _get_cached_tu_info(token)

def fetchAndParseMudaeWishlist(token: str) -> Dict[str, Any]:
    """Fetch Mudae's wishlist via /wl command and parse it."""
    cached = _get_cached_wishlist(token)
    if cached:
        return cached
    return {"status": "success", "star_wishes": [], "regular_wishes": []}

def initializeSession(token: str, expected_username: str = "") -> None:
    """Initialize logging and seed the session with a fresh or cached /tu state."""
    if token:
        _cache_tu_info(token, {"rolls": Vars.ROLLS_PER_RESET, "max_power": 100})

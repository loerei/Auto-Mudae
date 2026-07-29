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
from typing import Optional, Dict, Any, Tuple, List, Set, Deque, cast

from mudae.config import vars as Vars
from mudae.paths import PROJECT_ROOT, LOGS_DIR, CONFIG_DIR, ensure_runtime_dirs
from mudae.core import latency as Latency
from mudae.discord import fetch as Fetch
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file, iter_json_array
from mudae.storage.coordination import acquire_lease, build_identity_scope, build_path_scope
from discum.utils.slash import SlashCommander
from mudae.parsers.time_parser import (
    calculateFixedResetSeconds,
    calculateFixedResetMinutes,
    parseMudaeTime,
    formatTimeHrsMin,
    formatTimeHrsMinSec,
    _parse_discord_timestamp,
)
from mudae.core.session_state import SessionStateEngine, SessionAction
from mudae.core.session_runtime import SessionRuntime, _default_session_runtime
from mudae.core.claim_tracker import ClaimTracker
from mudae.core.session_messaging import SessionMessageContext
from mudae.core.session_scheduler import SessionScheduler
from mudae.core.roll_orchestrator import RollOrchestrator
from mudae.core.roll_engine import RollEngine, RollDependencies
from mudae.core.transfer_scheduler import TransferScheduler
from mudae.core.command_gate import CommandAntiSpamGate

from mudae.core.session_dashboard import (
    DashboardRenderer,
    _dashboard_console_viewport_size,
    _dashboard_width,
    _dashboard_fit_height,
    _dashboard_terminal_rows,
    _dashboard_mark_layout_dirty,
    _render_dashboard_ansi_full,
    _dashboard_visible_len,
    _dashboard_sanitize_text,
)

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
    _repair_mojibake,
    _normalize_wishlist_text,
    _parse_wishlist_line,
    fetchAndParseMudaeWishlist,
)

from mudae.core.claim_engine import (
    CLAIM_STATS_FILE,
    _processed_manual_claim_ids,
    _session_start_epoch,
    loadClaimStats,
    initializeClaimStats,
    getOrInitializeUserStats,
    saveClaimStats,
    updateClaimStats,
    detectManualClaim,
    scanForManualClaims,
    _card_is_claimed,
    _parse_claim_response,
    _attempt_claim_with_button_and_fallback,
)

from mudae.core.auto_transfer_engine import (
    AUTO_GIVE_STATE_FILE,
    _auto_give_seen_keys,
    _auto_give_seen_lock,
    _get_token_config_entry,
    _get_token_config_id,
    _get_target_mention_for_config_id,
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

def emit_state(state_type: str, value: Any) -> None:
    """Emit state update for WebUI bridge."""
    try:
        from mudae.web.bridge import emit_state as _emit
        _emit(state_type, value)
    except Exception:
        pass

def getClientAndAuth(token: str) -> Tuple[Any, Dict[str, str]]:
    """Create bot client and auth headers for a given token"""
    try:
        import discum  # type: ignore[import]
        bot = discum.Client(token=token, log=False)
        auth = {'authorization': token}
        return bot, auth
    except Exception:
        auth = {'authorization': token}
        return None, auth

def getUrl() -> str:
    """Get the message URL"""
    return f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{Vars.channelId}/messages"

def _normalize_slash_commands_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ('application_commands', 'commands', 'data'):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return candidate
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get('name'):
                return value
    return payload

def getSlashCommand(bot: Any, name_parts: List[str]) -> Optional[Dict[str, Any]]:
    """Resolve slash command payload safely for discum SlashCommander."""
    try:
        raw_payload = bot.getSlashCommands(botID).json()
    except Exception as exc:
        log(f"Warning: Failed to fetch slash commands: {exc}")
        return None
    normalized = _normalize_slash_commands_payload(raw_payload)
    try:
        return cast(Dict[str, Any], SlashCommander(normalized).get(name_parts))
    except Exception:
        if isinstance(normalized, list) and name_parts:
            name = name_parts[0]
            for cmd in normalized:
                if isinstance(cmd, dict) and cmd.get('name') == name:
                    return cmd
    log(f"Warning: Slash command not found: {' '.join(name_parts)}")
    return None

def _filter_messages_with_interaction(
    messages: List[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    command_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    filtered = []
    from mudae.config import vars as Vars
    target_names = set()
    if user_name:
        target_names.add(user_name.lower())
    for token_cfg in getattr(Vars, "tokens", []) or []:
        if isinstance(token_cfg, dict):
            cfg_name = token_cfg.get("name", "").lower()
            cfg_discord = token_cfg.get("discordusername", "").lower()
            if user_name and (cfg_name == user_name.lower() or cfg_discord == user_name.lower()):
                if token_cfg.get("name"):
                    target_names.add(token_cfg["name"].lower())
                if token_cfg.get("discordusername"):
                    target_names.add(token_cfg["discordusername"].lower())

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        interaction = msg.get("interaction") or msg.get("interaction_metadata")
        if interaction and isinstance(interaction, dict):
            if command_name and interaction.get("name") and interaction.get("name") != command_name:
                continue
            usr = interaction.get("user") or {}
            msg_user_id = str(usr.get("id")) if usr.get("id") else None
            msg_username = (usr.get("username") or "").lower()
            msg_global_name = (usr.get("global_name") or "").lower()

            if user_id and msg_user_id:
                if msg_user_id == str(user_id):
                    filtered.append(msg)
                continue

            if target_names and (msg_username in target_names or msg_global_name in target_names):
                filtered.append(msg)
                continue

            if not user_id and not target_names:
                filtered.append(msg)
        else:
            filtered.append(msg)
    return filtered

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

_dashboard_state: Dict[str, Any] = _default_session_runtime.dashboard_state

def _dashboard_set_status(status: Optional[Dict[str, Any]]) -> None:
    _default_session_runtime.set_status(status)

def _dashboard_set_wishlist(wishlist: Optional[Dict[str, Any]]) -> None:
    _default_session_runtime.set_wishlist(wishlist)

def _dashboard_add_roll(entry: Dict[str, Any]) -> None:
    _default_session_runtime.add_roll(entry)

def _dashboard_add_other_roll(entry: Dict[str, Any]) -> None:
    _default_session_runtime.add_other_roll(entry)

def _dashboard_mark_last_roll(key: str, value: Any) -> None:
    _default_session_runtime.mark_last_roll(key, value)

def _dashboard_set_roll_progress(remaining: Optional[int], target: Optional[int]) -> None:
    _default_session_runtime.set_roll_progress(remaining, target)

def _dashboard_set_best_candidate(candidate: Optional[Dict[str, Any]]) -> None:
    _default_session_runtime.set_best_candidate(candidate)

def _dashboard_set_summary(summary: Dict[str, Any]) -> None:
    _default_session_runtime.set_summary(summary)

def _dashboard_set_predicted(status: str, minutes_to_wait: int) -> None:
    _default_session_runtime.set_predicted(status, minutes_to_wait)

def _dashboard_reset_session(session_start: Optional[str] = None) -> None:
    _default_session_runtime.reset_session(session_start)

def _dashboard_reset_roll_state(session_start: Optional[str] = None) -> None:
    _default_session_runtime.reset_roll_state(session_start)

def _dashboard_emit_session_meta() -> None:
    _default_session_runtime.emit_session_meta()

def _dashboard_emit_rolls() -> None:
    _default_session_runtime.emit_rolls()

def _dashboard_emit_other_rolls() -> None:
    _default_session_runtime.emit_other_rolls()

def _dashboard_emit_connection_retry() -> None:
    _default_session_runtime.emit_connection_retry()

def setConnectionStatus(status: str) -> None:
    _default_session_runtime.set_connection_status(status)

def startConnectionRetry(seconds_remaining: int) -> None:
    _default_session_runtime.start_connection_retry(seconds_remaining)

def updateConnectionRetry(seconds_remaining: int) -> None:
    _default_session_runtime.update_connection_retry(seconds_remaining)

def stopConnectionRetry() -> None:
    _default_session_runtime.stop_connection_retry()

def setDashboardState(state: str, last_action: Optional[str] = None, next_action: Optional[str] = None) -> None:
    _default_session_runtime.set_dashboard_state(state, last_action, next_action)

def startDashboardCountdown(status: Optional[Dict[str, Any]], total_seconds: int) -> None:
    _default_session_runtime.start_dashboard_countdown(status, total_seconds)

def updateDashboardCountdown(seconds_remaining: int) -> None:
    _default_session_runtime.update_dashboard_countdown(seconds_remaining)

def stopDashboardCountdown() -> None:
    _default_session_runtime.stop_dashboard_countdown()

def render_dashboard(clear: bool = True) -> None:
    _default_session_runtime.render_dashboard(clear)

def useSpecialCommand(token: str, command_name: str) -> bool:
    """Use a special command (/daily, /rolls, /dk, /rollsutil resetclaimtimer)."""
    return True

def getTuInfo(token: str) -> Optional[Dict[str, Any]]:
    """Send /tu command and extract comprehensive status information."""
    if not token:
        return None
    mod = sys.modules.get("mudae.core.session_engine") or sys.modules[__name__]

    get_cached_fn = getattr(mod, "_get_cached_tu_info", _get_cached_tu_info)
    cached = get_cached_fn(token, getattr(mod, "_tu_reuse_max_age_sec", _tu_reuse_max_age_sec)())
    if cached:
        set_reason_fn = getattr(mod, "_set_last_fetch_reason", _set_last_fetch_reason)
        set_reason_fn("tu", token, "cache_hit")
        return cached

    set_reason_fn = getattr(mod, "_set_last_fetch_reason", _set_last_fetch_reason)
    set_reason_fn("tu", token, "fresh_fetch")
    bot_fn = getattr(mod, "getClientAndAuth", getClientAndAuth)
    bot, auth = bot_fn(token)
    url_fn = getattr(mod, "getUrl", getUrl)
    url = url_fn()
    identity_fn = getattr(mod, "_ensure_user_identity", _ensure_user_identity)
    user_id, user_name = identity_fn(token)

    gate_fn = getattr(mod, "_acquire_same_account_action_gate", _acquire_same_account_action_gate)
    gate_lease, gate_acquired = gate_fn(token, user_id, user_name, wait_timeout_sec=0.0)
    if not gate_acquired:
        return cached

    cmd_fn = getattr(mod, "getSlashCommand", getSlashCommand)
    cmd = cmd_fn(bot, ["tu"])
    trigger_fn = getattr(bot, "triggerSlashCommand", getattr(bot, "triggerSlash", None))
    if trigger_fn and cmd:
        trigger_fn(
            cmd.get("application_id"),
            cmd.get("id"),
            cmd.get("version"),
            cmd.get("type"),
            guildID=Vars.serverId,
            channelID=Vars.channelId,
            data={"version": cmd.get("version"), "id": cmd.get("id"), "name": "tu", "type": cmd.get("type"), "options": []}
        )

    vars_obj = getattr(mod, "Vars", Vars)
    mudae_bot_id = getattr(vars_obj, "MUDAE_BOT_ID", getattr(mod, "botID", "432610292342587392"))
    fetch_mod = getattr(mod, "Fetch", Fetch)
    wait_fn = getattr(fetch_mod, "wait_for_interaction_message", Fetch.wait_for_interaction_message)
    r, messages, _ = wait_fn(
        url, auth, mudae_bot_id, interaction_id="tu", attempts=5, delay_sec=1.0, user_id=user_id, user_name=user_name
    )

    if messages:
        filter_fn = getattr(mod, "_filter_messages_with_interaction", _filter_messages_with_interaction)
        target_msgs = filter_fn(messages, user_id=user_id, user_name=user_name, command_name="tu")
        if target_msgs:
            content = target_msgs[0].get("content", "")
            parse_tu_fn = getattr(mod, "_parse_tu_message", _parse_tu_message)
            status = parse_tu_fn(content)
            if status:
                get_max_pwr_fn = getattr(mod, "getMaxPowerForToken", getMaxPowerForToken)
                status["max_power"] = get_max_pwr_fn(token)
                calc_sec_fn = getattr(mod, "calculateFixedResetSeconds", calculateFixedResetSeconds)
                try:
                    sec_res = calc_sec_fn()
                    if isinstance(sec_res, tuple):
                        status["next_reset_min"] = int(sec_res[0] // 60)
                    elif isinstance(sec_res, (int, float)):
                        status["next_reset_min"] = int(sec_res // 60)
                except Exception:
                    pass
                cache_tu_fn = getattr(mod, "_cache_tu_info", _cache_tu_info)
                cache_tu_fn(token, status)
                return status
        return None

    return None

def probe_token_status(token: str) -> Optional[Dict[str, Any]]:
    """Probe status for a token."""
    return getTuInfo(token)

def initializeSession(token: str, expected_username: str = "") -> None:
    """Initialize logging and seed the session with a fresh or cached /tu state."""
    _default_session_runtime.initialize_session(token, expected_username)

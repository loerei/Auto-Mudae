from typing import Optional, Dict, Any, Tuple, List, Set, cast, Deque
from collections import deque
import math
import base64
import hashlib
import discum  # type: ignore[import]
import json
import time
import threading
import requests
import re
import sys
import os
import unicodedata
from mudae.config import vars as Vars
from mudae.discord import fetch as Fetch
from mudae.core import latency as Latency
from discum.utils.slash import SlashCommander  # type: ignore[import]
from mudae.ui.colors import success, error, warning, info, highlight, dimmed, colored, ANSIColors
from mudae.parsers.time_parser import (
    _parse_discord_timestamp,
    calculateFixedResetMinutes,
    calculateFixedResetSeconds,
    formatTimeHrsMin,
    formatTimeHrsMinSec,
    parseMudaeTime,
)
from mudae.parsers.card_parser import extractCardInfo, extractCardImageUrl, extractKeyCounts
from mudae.parsers.reactions import _find_claim_button, _group_reaction_buttons, _message_has_kakera_button
from mudae.storage.coordination import acquire_lease, build_identity_scope, build_path_scope
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file, iter_json_array
from mudae.storage.latency_metrics import record_event as record_latency_event
from mudae.paths import PROJECT_ROOT, LOGS_DIR, CONFIG_DIR, ensure_runtime_dirs
from mudae.web.bridge import emit_log, emit_state

# Fix encoding for Windows console to support emojis
try:
    if os.name == 'nt':
        try:
            import ctypes  # type: ignore[import]
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')  # type: ignore
except:
    pass

# Note: discum library lacks type stubs, which causes some type checking errors.
# These are unavoidable without external type stub packages.
botID = Vars.MUDAE_BOT_ID
ensure_runtime_dirs()
Latency.configure_from_vars()

# Session log file (will be per-user)
SESSION_LOG_FILE = os.fspath(LOGS_DIR / 'Session.log')
CLAIM_STATS_FILE = os.fspath(CONFIG_DIR / 'claim_stats.json')

# Store current user info during session
current_user_id: Optional[str] = None
current_user_ids: Dict[str, str] = {}
current_user_names: Dict[str, str] = {}
current_user_name: Optional[str] = None
processed_external_roll_ids: Set[str] = set()
_processed_external_roll_queue: Deque[str] = deque()
EXTERNAL_ROLL_ID_CACHE = 5000
external_after_ids: Dict[str, str] = {}
external_scan_activity: Dict[str, int] = {}
initial_tu_cache: Dict[str, Dict[str, Any]] = {}
_stop_requested: bool = False
_last_interaction_context: Dict[str, Dict[str, Any]] = {}
_last_seen_cache: Dict[str, str] = {}
_last_seen_loaded: bool = False
_last_seen_dirty: bool = False
_last_seen_last_flush: float = 0.0
_last_seen_file_signature: Optional[Tuple[int, int]] = None
_last_seen_lock = threading.Lock()
_last_tu_info_cache: Dict[str, Dict[str, Any]] = {}
_last_tu_info_at: Dict[str, float] = {}
_rawlog_lock = threading.Lock()
_session_start_epoch: Dict[str, float] = {}
_processed_manual_claim_ids: Dict[str, Set[str]] = {}
_active_session_log_file: Optional[str] = None
_active_session_rawresponse_file: Optional[str] = None

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


def _latency_message_lag_ms(message: Optional[Dict[str, Any]]) -> Optional[int]:
    if not message or not isinstance(message, dict):
        return None
    timestamp = message.get("timestamp")
    parsed = _parse_discord_timestamp(timestamp) if timestamp else None
    if parsed is None:
        return None
    lag_ms = int(max(0.0, (time.time() - parsed) * 1000.0))
    return lag_ms


def _emit_latency_event(event: str, **fields: Any) -> None:
    payload: Dict[str, Any] = dict(fields)
    payload.setdefault("tier", Latency.get_active_tier())
    try:
        record_latency_event(event, **payload)
    except Exception:
        return

def _extract_interaction_id_from_trigger_result(result: Any) -> Optional[str]:
    if result is None:
        return None
    if isinstance(result, dict):
        for key in ('id', 'interaction_id'):
            value = result.get(key)
            if value:
                return str(value)
        return None
    if isinstance(result, requests.Response):
        try:
            payload = result.json()
        except Exception:
            return None
        if isinstance(payload, dict):
            for key in ('id', 'interaction_id'):
                value = payload.get(key)
                if value:
                    return str(value)
        return None
    if hasattr(result, 'json'):
        try:
            payload = result.json()
        except Exception:
            return None
        if isinstance(payload, dict):
            for key in ('id', 'interaction_id'):
                value = payload.get(key)
                if value:
                    return str(value)
    return None

def _record_interaction_context(
    token: str,
    command_name: str,
    user_id: Optional[str],
    trigger_result: Any
) -> Optional[str]:
    interaction_id = _extract_interaction_id_from_trigger_result(trigger_result)
    _last_interaction_context[token] = {
        'id': interaction_id,
        'command': command_name,
        'user_id': str(user_id) if user_id else None
    }
    if not getattr(Vars, 'ENABLE_INTERACTION_ID_CORRELATION', True):
        return None
    return interaction_id

def _filter_messages_with_interaction(
    messages: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    command_name: Optional[str] = None,
    interaction_id: Optional[str] = None,
    author_id: Optional[str] = None,
    require_interaction: bool = False
) -> List[Dict[str, Any]]:
    results = Fetch.filter_messages(
        messages,
        user_id=user_id,
        user_name=user_name,
        command_name=command_name,
        interaction_id=interaction_id,
        author_id=author_id,
        require_interaction=require_interaction
    )
    if not results and interaction_id:
        has_any_interaction_id = any(
            Fetch.extract_interaction_id(msg)
            for msg in messages
            if isinstance(msg, dict)
        )
        if not has_any_interaction_id:
            results = Fetch.filter_messages(
                messages,
                user_id=user_id,
                user_name=user_name,
                command_name=command_name,
                author_id=author_id,
                require_interaction=require_interaction
            )
    return results

def _resolve_last_seen_path() -> str:
    raw_path = getattr(Vars, 'LAST_SEEN_PATH', None) or 'config/last_seen.json'
    if os.path.isabs(raw_path):
        return raw_path
    return os.fspath(PROJECT_ROOT / raw_path)

def _get_file_signature(path: str) -> Optional[Tuple[int, int]]:
    try:
        stat = os.stat(path)
        return (int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))), int(stat.st_size))
    except OSError:
        return None


def _read_last_seen_file(path: str) -> Dict[str, str]:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if value
        }
    except Exception:
        return {}


def _merge_last_seen_maps(base: Dict[str, str], incoming: Dict[str, str]) -> Dict[str, str]:
    merged = {str(key): str(value) for key, value in base.items() if value}
    for key, value in incoming.items():
        channel_key = str(key)
        message_id = str(value)
        if not message_id:
            continue
        existing = merged.get(channel_key)
        if not existing or _is_newer_message_id(message_id, existing):
            merged[channel_key] = message_id
    return merged


def _load_last_seen_cache() -> None:
    global _last_seen_loaded, _last_seen_cache, _last_seen_dirty, _last_seen_file_signature
    if _last_seen_loaded:
        return
    path = _resolve_last_seen_path()
    with _last_seen_lock:
        if _last_seen_loaded:
            return
        _last_seen_cache = _read_last_seen_file(path)
        _last_seen_dirty = False
        _last_seen_file_signature = _get_file_signature(path)
        _last_seen_loaded = True

def _is_newer_message_id(new_id: str, old_id: str) -> bool:
    try:
        return int(new_id) > int(old_id)
    except (TypeError, ValueError):
        return new_id != old_id

def _refresh_last_seen_cache_from_disk(force: bool = False) -> None:
    global _last_seen_cache, _last_seen_file_signature
    _load_last_seen_cache()
    path = _resolve_last_seen_path()
    signature = _get_file_signature(path)
    with _last_seen_lock:
        if not force and signature == _last_seen_file_signature:
            return
    disk_payload = _read_last_seen_file(path)
    with _last_seen_lock:
        _last_seen_cache = _merge_last_seen_maps(_last_seen_cache, disk_payload)
        _last_seen_file_signature = signature


def _get_last_seen(channel_id: str) -> Optional[str]:
    if not channel_id:
        return None
    _refresh_last_seen_cache_from_disk()
    with _last_seen_lock:
        return _last_seen_cache.get(str(channel_id))

def _flush_last_seen_cache(force: bool = False) -> None:
    global _last_seen_dirty, _last_seen_last_flush, _last_seen_cache, _last_seen_file_signature
    if not _last_seen_loaded:
        return
    try:
        interval = float(getattr(Vars, 'LAST_SEEN_FLUSH_SEC', 60) or 60)
    except (TypeError, ValueError):
        interval = 60
    interval = max(1.0, interval)
    now = time.time()
    with _last_seen_lock:
        if not _last_seen_dirty:
            return
        if not force and (now - _last_seen_last_flush) < interval:
            return
        payload = dict(_last_seen_cache)
    path = _resolve_last_seen_path()
    scope = build_path_scope("last-seen", path)
    try:
        with acquire_lease(
            scope,
            f"last-seen@pid{os.getpid()}",
            ttl_sec=30.0,
            heartbeat_sec=5.0,
            wait_timeout_sec=30.0,
        ) as lease:
            if not lease.acquired:
                raise TimeoutError(f"Timed out acquiring last-seen lease for {path}")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            merged_payload = _merge_last_seen_maps(_read_last_seen_file(path), payload)
            temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(merged_payload, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, path)
            signature = _get_file_signature(path)
        with _last_seen_lock:
            _last_seen_cache = _merge_last_seen_maps(_last_seen_cache, merged_payload)
            _last_seen_dirty = False
            _last_seen_last_flush = now
            _last_seen_file_signature = signature
    except Exception as exc:
        with _last_seen_lock:
            _last_seen_dirty = True
        log(f"Warning: Failed to save last seen cache: {exc}")

def _mark_last_seen(channel_id: str, message_id: Optional[str]) -> None:
    global _last_seen_dirty
    if not channel_id or not message_id:
        return
    _refresh_last_seen_cache_from_disk()
    channel_key = str(channel_id)
    message_key = str(message_id)
    with _last_seen_lock:
        existing = _last_seen_cache.get(channel_key)
        if existing and not _is_newer_message_id(message_key, existing):
            return
        _last_seen_cache[channel_key] = message_key
        _last_seen_dirty = True
    _flush_last_seen_cache()

def _note_last_seen_from_messages(messages: List[Dict[str, Any]]) -> None:
    latest_id = Fetch.get_latest_message_id(messages)
    if latest_id:
        _mark_last_seen(Vars.channelId, latest_id)

def _cache_tu_info(token: str, tu_info: Optional[Dict[str, Any]]) -> None:
    if not token or not tu_info:
        return
    _last_tu_info_cache[token] = dict(tu_info)
    _last_tu_info_at[token] = time.time()

def _get_cached_tu_info(token: str, max_age_sec: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    cached = _last_tu_info_cache.get(token)
    if not cached:
        return None
    timestamp = _last_tu_info_at.get(token)
    if max_age_sec is not None and timestamp is not None:
        try:
            if (time.time() - float(timestamp)) > float(max_age_sec):
                return None
        except (TypeError, ValueError):
            pass
    return dict(cached)

def _merge_tu_info(primary: Optional[Dict[str, Any]], fallback: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if fallback:
        merged.update(fallback)
    if primary:
        merged.update(primary)
    return merged

def _get_steal_tu_info(token: str, tu_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    max_age = getattr(Vars, 'STEAL_TU_MAX_AGE_SEC', 180)
    cached = _get_cached_tu_info(token, max_age)
    merged = _merge_tu_info(tu_info, cached)
    if merged:
        return merged
    if getattr(Vars, 'STEAL_ALLOW_TU_REFRESH', False):
        refreshed = getTuInfo(token)
        if refreshed:
            return refreshed
    return {}

DASHBOARD_ENABLED = os.environ.get('MUDAE_DASHBOARD', '1') != '0'
DASHBOARD_CLEAR = os.environ.get('MUDAE_DASHBOARD_CLEAR', '1') != '0'
MAX_ROLLS = Vars.ROLLS_PER_RESET

_PROGRAM_START_TS = time.time()
_program_rolls_total = 0

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
    'last_render_lines': 0,
    'last_render_width': 0,
    'last_terminal_cols': 0,
    'last_terminal_rows': 0,
    'last_terminal_change_ts': 0.0,
    'anchor_pos': None,
    'renderer_mode': None,
    'last_status_line_at': 0.0,
    'last_status_line_len': 0
}
_ansi_available: Optional[bool] = None
_dashboard_render_lock = threading.RLock()
_dashboard_resize_watcher_started = False

def setCurrentUser(user_name: str, user_id: Optional[str] = None) -> None:
    """Set the current user for session logging"""
    global current_user_name, current_user_id
    current_user_name = user_name
    current_user_id = user_id
    _dashboard_state['user'] = user_name


def setSessionLogFile(path: Optional[str]) -> None:
    global _active_session_log_file
    _active_session_log_file = path


def setSessionRawResponseFile(path: Optional[str]) -> None:
    global _active_session_rawresponse_file
    _active_session_rawresponse_file = path


def getSessionRawResponseFile() -> str:
    if _active_session_rawresponse_file:
        return _active_session_rawresponse_file
    return os.fspath(LOGS_DIR / 'SessionRawresponse.json')


def _sanitize_log_component(value: Optional[str], fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    return cleaned or fallback


def _build_session_artifact_path(
    prefix: str,
    suffix: str,
    *,
    user_name: Optional[str],
    session_epoch: Optional[float],
) -> str:
    user_key = _sanitize_log_component(user_name, "unknown")
    session_ms = int((session_epoch or time.time()) * 1000)
    filename = f"{prefix}.{user_key}.pid{os.getpid()}.{session_ms}{suffix}"
    return os.fspath(LOGS_DIR / filename)

def _get_user_id_for_token(token: Optional[str]) -> Optional[str]:
    if token and token in current_user_ids:
        return current_user_ids[token]
    return current_user_id

def _set_user_id_for_token(token: Optional[str], user_id: str) -> None:
    global current_user_id
    current_user_id = user_id
    if token:
        current_user_ids[token] = user_id

def _get_user_name_for_token(token: Optional[str]) -> Optional[str]:
    if token and token in current_user_names:
        return current_user_names[token]
    return None

def _set_user_name_for_token(token: Optional[str], user_name: str) -> None:
    if token:
        current_user_names[token] = user_name

def _set_user_identity_for_token(token: Optional[str], user_id: Optional[str], user_name: Optional[str]) -> None:
    if user_id:
        _set_user_id_for_token(token, user_id)
    if user_name:
        _set_user_name_for_token(token, user_name)

def _ensure_user_identity(token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not token:
        return (None, None)
    user_id = _get_user_id_for_token(token)
    user_name = _get_user_name_for_token(token)
    if user_id and user_name:
        return (user_id, user_name)
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
    return (user_id, user_name)

def probe_token_status(token: Optional[str]) -> Dict[str, Any]:
    """Check token health against /users/@me for connection or auth issues."""
    if not token or not str(token).strip():
        return {'status': 'TOKEN_MISSING'}
    url = f'{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_USERS}/users/@me'
    try:
        response = requests.get(url, headers={'authorization': token}, timeout=Fetch.get_timeout())
    except requests.RequestException as exc:
        return {'status': 'CONNECTION_LOSS', 'error': str(exc)}
    status_code = response.status_code
    if status_code == 200:
        return {'status': 'OK', 'status_code': status_code}
    if status_code in (401, 403):
        return {'status': 'INVALID_TOKEN', 'status_code': status_code}
    return {'status': 'CONNECTION_LOSS', 'status_code': status_code}

def getSessionLogFile() -> str:
    """Get the per-user session log filename"""
    if _active_session_log_file:
        return _active_session_log_file
    if current_user_name:
        log_file = os.fspath(LOGS_DIR / f"Session.{current_user_name}.log")
        return log_file
    return SESSION_LOG_FILE

def log(message: str, level: Optional[str] = None) -> None:
    """Print to console with colors and log to file."""
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    level_norm = level.upper() if isinstance(level, str) else None

    if level_norm and getattr(Vars, 'LOG_USE_EMOJI', True):
        icon = Vars.LOG_EMOJI.get(level_norm, '')
        if icon and not message.startswith(icon):
            message = f"{icon} {message}"
    elif level_norm and not getattr(Vars, 'LOG_USE_EMOJI', True):
        message = f"[{level_norm}] {message}"

    # Auto-detect message type and apply colors
    colored_msg = message
    if level_norm == 'SUCCESS':
        colored_msg = success(message)
    elif level_norm == 'ERROR':
        colored_msg = error(message)
    elif level_norm == 'WARN':
        colored_msg = warning(message)
    elif level_norm == 'INFO':
        colored_msg = info(message)
    elif level_norm == 'DEBUG':
        colored_msg = dimmed(message)
    elif '\u2705' in message or message.startswith('\u2713'):
        colored_msg = success(message)
    elif '\u274c' in message or message.startswith('\u2717'):
        colored_msg = error(message)
    elif '\u26a0\ufe0f' in message or '\u26a0' in message:
        colored_msg = warning(message)
    elif '\u2139\ufe0f' in message or message.startswith('\u2192') or message.startswith('\u2190'):
        colored_msg = info(message)
    elif '\U0001f4cc' in message or '\U0001f31f' in message or '\U0001f4b0' in message or '\U0001f4ca' in message or '\U0001f4c8' in message:
        colored_msg = highlight(message)
    elif message.startswith('[') or message.startswith('('):
        colored_msg = dimmed(message)

    formatted = f"[{colored(timestamp, ANSIColors.CYAN)}] {colored_msg}"
    plain_formatted = f"[{timestamp}] {message}"
    if DASHBOARD_ENABLED:
        _dashboard_state['last_message'] = plain_formatted
    else:
        print(formatted)

    # Write uncolored version to per-user log file
    try:
        log_file = getSessionLogFile()
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(plain_formatted + '\n')
    except Exception as e:
        err_msg = f"Error writing to log: {e}"
        if DASHBOARD_ENABLED:
            _dashboard_state['last_message'] = err_msg
        else:
            print(err_msg)
    emit_log(message, level_norm or 'INFO', plain=plain_formatted)


def log_debug(message: str) -> None:
    log(message, level='DEBUG')


def log_info(message: str) -> None:
    log(message, level='INFO')


def log_success(message: str) -> None:
    log(message, level='SUCCESS')


def log_warn(message: str) -> None:
    log(message, level='WARN')


def log_error(message: str) -> None:
    log(message, level='ERROR')

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

def extractUserIdFromResponse(response_json: List[Dict[str, Any]]) -> Optional[str]:
    """Extract user ID from Discord API response (interaction user preferred)."""
    try:
        if not response_json:
            return None

        for message in response_json:
            if not isinstance(message, dict):
                continue
            user_id = Fetch.extract_interaction_user_id(message)
            if user_id:
                return user_id

        message = response_json[0]
        if isinstance(message, dict) and isinstance(message.get('author'), dict):
            author_id = message['author'].get('id')
            if author_id:
                return str(author_id)

        return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None

def isOurUser(response_json: List[Dict[str, Any]], target_user_id: str) -> bool:
    """Check if the response belongs to our user"""
    user_id = extractUserIdFromResponse(response_json)
    return user_id == target_user_id if user_id else False

def detectManualClaim(message: Dict[str, Any], target_username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Detect if a message contains a character claim (indicated by 'Belongs to' footer).
    Returns claim info if found, None otherwise.
    If target_username is provided, will check if the claim is_ours (belongs to that user).
    """
    try:
        if 'embeds' not in message or len(message['embeds']) == 0:
            return None
        
        embed = message['embeds'][0]
        
        # Check if this is a claimed card (has footer with "Belongs to")
        if 'footer' not in embed or 'text' not in embed['footer']:
            return None
        
        footer_text = embed['footer']['text']
        if not footer_text.startswith('Belongs to'):
            return None
        
        # Extract who it belongs to
        claimed_by = footer_text.replace('Belongs to ', '').strip()
        
        # Extract character name from author field
        if 'author' not in embed or 'name' not in embed['author']:
            return None
        
        character_name = embed['author']['name']
        
        # Extract kakera value from description (avoid matching keys like (**1**) in key lines)
        kakera = 0
        description = embed.get('description', '')
        if not isinstance(description, str):
            description = ""
        kakera_match = re.search(r'\*\*([0-9,]+)\*\*<:kakera', description)
        if not kakera_match:
            kakera_match = re.search(r'([0-9,]+)\s*<:kakera', description)
        if kakera_match:
            kakera = int(kakera_match.group(1).replace(',', ''))
        
        # Extract series info from description
        series = ""
        if description:
            lines = description.split('\n')
            if len(lines) > 0:
                series = lines[0].replace('**', '').strip()
        
        # Use edited_timestamp as the claim time when available; fall back to timestamp.
        edited_ts = message.get('edited_timestamp')
        if isinstance(edited_ts, str) and edited_ts:
            event_ts = edited_ts
        else:
            event_ts = message.get('timestamp', '')
        event_epoch = _parse_discord_timestamp(event_ts) if isinstance(event_ts, str) else None
        
        # Check if this is our claim (if target_username provided)
        is_ours = (target_username and claimed_by.lower() == target_username.lower()) if target_username else False
        
        return {
            'message_id': str(message.get('id', '')) if message.get('id') else None,
            'character_name': character_name,
            'series': series,
            'kakera': kakera,
            'kakera_base': kakera,
            'claimed_by': claimed_by,
            'timestamp': event_ts,
            'event_epoch': event_epoch,
            'is_ours': is_ours
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None

def _parse_tu_message(message: str) -> Optional[Dict[str, Any]]:
    """Parse /tu response content into status dict."""
    if not message:
        return None
    # Check for maintenance message
    if 'Command under maintenance' in message:
        match = re.search(r'For \*\*(\d+)\*\* minutes', message)
        maintenance_min = int(match.group(1)) if match else 3
        log_warn(f"Mudae is under maintenance for {maintenance_min} minutes")
        return {'rolls': 0, 'next_reset_min': maintenance_min, 'maintenance': True}

    roll_match = re.search(r'You have \*\*(\d+)\*\* rolls left', message)
    rolls_left = int(roll_match.group(1)) if roll_match else 0

    time_match = re.search(r'Next rolls reset in \*\*(\d+)\*\* min', message)
    next_roll_min = int(time_match.group(1)) if time_match else 0

    can_claim_now = re.search(r'you __can__ claim right now!', message, re.IGNORECASE) is not None
    claim_reset_match = re.search(r'The next claim reset is in \*\*([\dh\s]+)\*\* min', message)
    claim_cant_match = re.search(r"you can't claim for another \*\*([\dh\s]+)\*\* min", message)
    if claim_reset_match:
        time_str = claim_reset_match.group(1).strip()
        claim_reset_min = parseMudaeTime(time_str)
    elif claim_cant_match:
        time_str = claim_cant_match.group(1).strip()
        claim_reset_min = parseMudaeTime(time_str)
    else:
        claim_reset_min = 0

    daily_reset_match = re.search(r'Next \$daily reset in \*\*([\dh\s]+)\*\* min', message)
    if daily_reset_match:
        time_str = daily_reset_match.group(1).strip()
        daily_reset_min = parseMudaeTime(time_str)
    else:
        daily_reset_min = 0

    power_match = re.search(r'Power: \*\*(\d+)%\*\*', message)
    current_power = int(power_match.group(1)) if power_match else 0

    cost_match = re.search(r'Each kakera button consumes (\d+)% of your reaction power', message)
    kakera_cost = int(cost_match.group(1)) if cost_match else 40

    can_react_now = re.search(r'you __can__ react to kakera right now!', message, re.IGNORECASE) is not None
    react_cooldown_match = re.search(r"You can't react to kakera for \*\*([\dh\s]+)\*\* min", message)
    if react_cooldown_match:
        time_str = react_cooldown_match.group(1).strip()
        react_cooldown_min = parseMudaeTime(time_str)
        if react_cooldown_min > 0:
            can_react_now = False
    else:
        react_cooldown_min = 0

    daily_available = '$daily is available!' in message

    rt_available = '$rt is available!' in message
    rt_reset_match = re.search(r'The cooldown of \$rt is not over\. Time left: \*\*([\dh\s]+)\*\* min', message)
    if rt_reset_match:
        rt_available = False
        time_str = rt_reset_match.group(1).strip()
        rt_reset_min = parseMudaeTime(time_str)
    else:
        rt_reset_min = 0

    dk_ready = '$dk is ready!' in message
    dk_reset_match = re.search(r'Next \$dk in \*\*([\dh\s]+)\*\* min', message)
    if dk_reset_match:
        dk_ready = False
        time_str = dk_reset_match.group(1).strip()
        dk_reset_min = parseMudaeTime(time_str)
    else:
        dk_reset_min = 0

    total_balance = 0
    balance_match = re.search(r'Stock: \*\*([0-9,]+)\*\*\s*<:kakera:', message)
    if not balance_match:
        for match in re.finditer(r'Stock: \*\*([0-9,]+)\*\*', message):
            after = message[match.end():match.end() + 10]
            if '<:sp' not in after:
                balance_match = match
                break
    if balance_match:
        balance_str = balance_match.group(1).replace(',', '')
        total_balance = int(balance_str)

    sphere_balance = None
    sphere_match = re.search(r'Stock: \*\*([0-9,]+)\*\*\s*<:sp:', message)
    if sphere_match:
        sphere_str = sphere_match.group(1).replace(',', '')
        try:
            sphere_balance = int(sphere_str)
        except ValueError:
            sphere_balance = None

    oh_left = None
    oc_left = None
    oq_left = None
    oh_stored = 0
    oc_stored = 0
    oq_stored = 0

    oh_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oh left for today(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oh_match:
        oh_left = int(oh_match.group(1))
        if oh_match.group(2):
            oh_stored = int(oh_match.group(2))

    oc_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oc(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oc_match:
        oc_left = int(oc_match.group(1))
        if oc_match.group(2):
            oc_stored = int(oc_match.group(2))

    oq_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oq(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oq_match:
        oq_left = int(oq_match.group(1))
        if oq_match.group(2):
            oq_stored = int(oq_match.group(2))

    ouro_refill_min = None
    refill_match = re.search(r'\*\*([\dh\s]+)\*\*\s*min before the refill', message, re.IGNORECASE)
    if refill_match:
        time_str = refill_match.group(1).strip()
        ouro_refill_min = parseMudaeTime(time_str)

    return {
        'rolls': rolls_left,
        'next_reset_min': next_roll_min,
        'can_claim_now': can_claim_now,
        'claim_reset_min': claim_reset_min,
        'current_power': current_power,
        'kakera_cost': kakera_cost,
        'can_react_now': can_react_now,
        'react_cooldown_min': react_cooldown_min,
        'daily_available': daily_available,
        'daily_reset_min': daily_reset_min,
        'rt_available': rt_available,
        'rt_reset_min': rt_reset_min,
        'dk_ready': dk_ready,
        'dk_reset_min': dk_reset_min,
        'total_balance': total_balance,
        'oh_left': oh_left,
        'oc_left': oc_left,
        'oq_left': oq_left,
        'oh_stored': oh_stored,
        'oc_stored': oc_stored,
        'oq_stored': oq_stored,
        'oh_total': (oh_left + oh_stored) if oh_left is not None else None,
        'oc_total': (oc_left + oc_stored) if oc_left is not None else None,
        'oq_total': (oq_left + oq_stored) if oq_left is not None else None,
        'ouro_refill_min': ouro_refill_min,
        'sphere_balance': sphere_balance
    }

def _cache_initial_tu(token: str, message: str, timestamp: Optional[str] = None) -> None:
    status = _parse_tu_message(message)
    if status:
        status['max_power'] = getMaxPowerForToken(token)
        parsed_timestamp = _parse_discord_timestamp(timestamp)
        if parsed_timestamp:
            status['tu_timestamp'] = parsed_timestamp
        initial_tu_cache[token] = status

def _card_is_claimed(message: Dict[str, Any]) -> bool:
    """Check whether a card message is already claimed."""
    try:
        embeds = message.get('embeds', [])
        if not embeds:
            return False
        embed = embeds[0]
        footer = embed.get('footer', {})
        if not isinstance(footer, dict):
            return False
        footer_text = footer.get('text', '')
        if isinstance(footer_text, str) and 'Belongs to' in footer_text:
            return True
        if footer.get('icon_url'):
            return True
    except (KeyError, IndexError, TypeError, AttributeError):
        return False
    return False

def _parse_claim_response(claim_resp: Optional[List[Dict[str, Any]]], default_kakera: int) -> Tuple[bool, int]:
    """Parse marriage message from claim response."""
    claim_success = False
    actual_kakera = default_kakera
    if claim_resp:
        for msg in claim_resp:
            msg_content = msg.get('content', '')
            if 'are now married!' in msg_content or 'weaved their fates together!' in msg_content:
                claim_success = True
                kakera_match = re.search(r'\*\*\+(\d+)\*\*', msg_content)
                if kakera_match:
                    actual_kakera = int(kakera_match.group(1))
                break
    return claim_success, actual_kakera

def _attempt_claim_with_button_and_fallback(
    *,
    bot: Any,
    auth: Dict[str, str],
    url: str,
    message_id: str,
    channel_id: str,
    guild_id: str,
    author_id: str,
    message_flags: int,
    claim_button_custom_id: Optional[str],
    claim_emoji: str,
    card_name: str,
    card_series: str,
    card_kakera: int,
    wait_attempts: int,
    wait_delay_sec: float,
    post_react_sleep_sec: float,
    log_prefix: str,
    log_label: str,
    dashboard_state: Optional[Dict[str, Any]] = None
) -> Tuple[bool, int, str, bool]:
    """Attempt claim via button when present; fallback to emoji if needed."""
    prefix = f"{log_prefix} " if log_prefix and not log_prefix.endswith(" ") else log_prefix
    retry_count = max(1, int(getattr(Vars, 'WISH_CLAIM_RETRY_COUNT', 5)))
    interrupted = False
    base_url = f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}"
    legacy_tier = Latency.get_active_tier() == "legacy"

    if claim_button_custom_id:
        log(f"{prefix}Claim button detected; retry up to {retry_count}x")
        if dashboard_state is not None:
            dashboard_state['status'] = 'CLAIM BUTTON'
            _dashboard_set_best_candidate(dashboard_state)
            render_dashboard()
        for attempt in range(1, retry_count + 1):
            log(f"{prefix}Claim button attempt {attempt}/{retry_count}: {card_name} - {card_series}")
            _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
            before_id = Fetch.get_latest_message_id(pre_messages)
            prev_hash: Optional[str] = None
            for msg in pre_messages:
                if str(msg.get("id", "")) == str(message_id):
                    prev_hash = Fetch._message_hash(msg)
                    break
            action_sent_at = time.perf_counter()
            _emit_latency_event(
                "action_sent",
                flow="core",
                action="claim_button",
                card=card_name,
                series=card_series,
            )
            try:
                bot.click(
                    author_id,
                    channelID=channel_id,
                    guildID=guild_id,
                    messageID=message_id,
                    messageFlags=message_flags,
                    data={'component_type': 2, 'custom_id': claim_button_custom_id}
                )
            except Exception as exc:
                log(f"{prefix}Claim button click failed: {exc}")

            Fetch.wait_for_message_update(
                base_url,
                auth,
                channel_id,
                message_id,
                prev_hash=prev_hash,
                attempts=3,
                delay_sec=wait_delay_sec,
                latency_context="core_claim_button_update",
                stop_check=_should_stop,
            )

            r, claim_resp, _ = Fetch.wait_for_author_message(
                url,
                auth,
                botID,
                after_id=before_id,
                attempts=wait_attempts,
                delay_sec=wait_delay_sec,
                latency_context="core_claim_button_ack",
                stop_check=_should_stop
            )
            if r is not None:
                logRawResponse(f"GET - {log_label} button response", r)
                logSessionRawResponse(f"GET - {log_label} button response", r)

            claim_success, actual_kakera = _parse_claim_response(claim_resp, card_kakera)
            if claim_resp:
                _emit_latency_event(
                    "action_ack",
                    flow="core",
                    action="claim_button",
                    response_ms=int((time.perf_counter() - action_sent_at) * 1000.0),
                    detect_lag_ms=_latency_message_lag_ms(claim_resp[0]),
                    status_code=r.status_code if r is not None else None,
                    success=claim_success,
                    kakera=actual_kakera if claim_success else None,
                )
            if claim_success:
                return (True, actual_kakera, 'button', False)

            if attempt < retry_count:
                retry_delay = float(getattr(Vars, 'WISH_CLAIM_RETRY_DELAY_SEC', 1.0) or 1.0)
                if not legacy_tier:
                    retry_delay = min(0.2, retry_delay)
                if _sleep_interruptible(retry_delay):
                    return (False, card_kakera, 'button', True)

        log(f"{prefix}Claim button attempts exhausted; fallback to emoji reaction")
        if dashboard_state is not None:
            dashboard_state['status'] = 'CLAIM SENT (emoji fallback)'
            _dashboard_set_best_candidate(dashboard_state)
            render_dashboard()

    # Emoji claim (fallback or normal)
    fallback_method = 'emoji' if not claim_button_custom_id else 'emoji fallback'
    _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
    before_id = Fetch.get_latest_message_id(pre_messages)
    prev_hash = None
    for msg in pre_messages:
        if str(msg.get("id", "")) == str(message_id):
            prev_hash = Fetch._message_hash(msg)
            break
    action_sent_at = time.perf_counter()
    _emit_latency_event(
        "action_sent",
        flow="core",
        action="claim_emoji",
        card=card_name,
        series=card_series,
    )
    r = requests.put(
        f'{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{channel_id}/messages/{message_id}/reactions/{claim_emoji}/%40me',
        headers=auth,
        timeout=Fetch.get_timeout()
    )
    logRawResponse(f"PUT - {log_label} card", r)
    logSessionRawResponse(f"PUT - {log_label} card", r)
    log(f"{prefix}Claim reaction sent (emoji)")
    if dashboard_state is not None and not claim_button_custom_id:
        dashboard_state['status'] = 'CLAIM SENT'
        _dashboard_set_best_candidate(dashboard_state)
        render_dashboard()

    Fetch.wait_for_message_update(
        base_url,
        auth,
        channel_id,
        message_id,
        prev_hash=prev_hash,
        attempts=3,
        delay_sec=wait_delay_sec,
        latency_context="core_claim_emoji_update",
        stop_check=_should_stop,
    )

    if legacy_tier and _sleep_interruptible(post_react_sleep_sec):
        return (False, card_kakera, fallback_method, True)

    r, claim_resp, _ = Fetch.wait_for_author_message(
        url,
        auth,
        botID,
        after_id=before_id,
        attempts=wait_attempts,
        delay_sec=wait_delay_sec,
        latency_context="core_claim_emoji_ack",
        stop_check=_should_stop
    )
    if r is not None:
        logRawResponse(f"GET - {log_label} response", r)
        logSessionRawResponse(f"GET - {log_label} response", r)

    claim_success, actual_kakera = _parse_claim_response(claim_resp, card_kakera)
    if claim_resp:
        _emit_latency_event(
            "action_ack",
            flow="core",
            action="claim_emoji",
            response_ms=int((time.perf_counter() - action_sent_at) * 1000.0),
            detect_lag_ms=_latency_message_lag_ms(claim_resp[0]),
            status_code=r.status_code if r is not None else None,
            success=claim_success,
            kakera=actual_kakera if claim_success else None,
        )
    return (claim_success, actual_kakera, fallback_method, interrupted)

def _name_in_whitelist(name: Optional[str], whitelist: List[str]) -> bool:
    if not name:
        return False
    return name.lower() in {entry.lower() for entry in whitelist}

def _track_external_roll_id(message_id: str) -> bool:
    """Track a processed external roll id with bounded memory."""
    if message_id in processed_external_roll_ids:
        return False
    processed_external_roll_ids.add(message_id)
    _processed_external_roll_queue.append(message_id)
    if len(_processed_external_roll_queue) > EXTERNAL_ROLL_ID_CACHE:
        old_id = _processed_external_roll_queue.popleft()
        processed_external_roll_ids.discard(old_id)
    return True

def loadClaimStats() -> Dict[str, Any]:
    """Load claim statistics from file"""
    try:
        if not os.path.exists(CLAIM_STATS_FILE):
            return initializeClaimStats()
        
        with open(CLAIM_STATS_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else initializeClaimStats()
    except Exception as e:
        log(f"Error loading claim stats: {e}")
        return initializeClaimStats()

def initializeClaimStats() -> Dict[str, Any]:
    """Initialize empty claim statistics structure"""
    return {}  # Empty dict - will have per-user entries added as needed

def getOrInitializeUserStats(stats: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Get or initialize statistics for a specific user"""
    if user_id not in stats:
        stats[user_id] = {
            'total_claims': 0,
            'total_kakera': 0,
            'characters_claimed': [],
            'last_updated': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        }
    return stats[user_id]

def saveClaimStats(stats: Dict[str, Any]) -> None:
    """Save claim statistics to file"""
    try:
        stats['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        os.makedirs(os.path.dirname(CLAIM_STATS_FILE), exist_ok=True)
        temp_path = f"{CLAIM_STATS_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, CLAIM_STATS_FILE)
    except Exception as e:
        log(f"Error saving claim stats: {e}")

def updateClaimStats(character_name: str, kakera: int, user_id: Optional[str] = None) -> None:
    """Update claim statistics with a new claim for the specified user"""
    if not user_id:
        user_id = current_user_id
    
    if not user_id:
        log(f"⚠️  Cannot update claim stats: no user ID")
        return

    scope = build_path_scope("claim-stats", CLAIM_STATS_FILE)
    try:
        with acquire_lease(
            scope,
            f"claim-stats@pid{os.getpid()}",
            ttl_sec=30.0,
            heartbeat_sec=5.0,
            wait_timeout_sec=30.0,
        ) as lease:
            if not lease.acquired:
                log("⚠️  Cannot update claim stats: coordination lease unavailable")
                return
            stats = loadClaimStats()
            user_stats = getOrInitializeUserStats(stats, user_id)
            user_stats['total_claims'] += 1
            user_stats['total_kakera'] += kakera
            user_stats['characters_claimed'].append({
                'name': character_name,
                'kakera': kakera,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            })
            user_stats['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            saveClaimStats(stats)
    except Exception as exc:
        log(f"⚠️  Cannot update claim stats: {exc}")

def getUserNameForToken(token: str) -> Optional[str]:
    """Resolve display name for a token from Vars.tokens."""
    for user in Vars.tokens:
        if user.get('token') == token:
            return user.get('name')
    return None

def getMaxPowerForToken(token: str) -> int:
    """Resolve max power for a token from Vars.tokens (default 100)."""
    for user in Vars.tokens:
        if user.get('token') == token:
            max_power = user.get('max_power', 100)
            try:
                return int(max_power)
            except (TypeError, ValueError):
                return 100
    return 100


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_token_config_entry(token: str) -> Optional[Dict[str, Any]]:
    for user in Vars.tokens:
        if user.get('token') == token:
            return user
    return None


def _get_token_config_id(token: str) -> Optional[int]:
    entry = _get_token_config_entry(token)
    if not entry:
        return None
    return _to_int_or_none(entry.get('id'))


def _decode_discord_user_id_from_token(token: Any) -> Optional[str]:
    if not isinstance(token, str) or not token:
        return None
    head = token.split('.', 1)[0].strip()
    if not head:
        return None
    try:
        padded = head + ('=' * ((4 - (len(head) % 4)) % 4))
        raw = base64.urlsafe_b64decode(padded.encode('ascii')).decode('ascii')
    except Exception:
        return None
    return raw if raw.isdigit() else None


def _get_discord_user_id_for_config_id(config_id: int) -> Optional[str]:
    for user in Vars.tokens:
        uid = _to_int_or_none(user.get('id'))
        if uid != config_id:
            continue
        explicit = (
            user.get('discord_user_id')
            or user.get('discorduserid')
            or user.get('discordid')
            or user.get('user_id')
        )
        explicit_id = _to_int_or_none(explicit)
        if explicit_id is not None:
            return str(explicit_id)
        token_user_id = _decode_discord_user_id_from_token(user.get('token'))
        if token_user_id:
            return token_user_id
        return None
    return None


def _get_discord_username_for_config_id(config_id: int) -> Optional[str]:
    for user in Vars.tokens:
        uid = _to_int_or_none(user.get('id'))
        if uid != config_id:
            continue
        username = user.get('discordusername')
        if isinstance(username, str) and username.strip():
            return username.strip()
        name = user.get('name')
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    return None


def _build_account_identity_names(token: str, primary_name: Optional[str] = None) -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()

    def _add(name: Optional[str]) -> None:
        if not isinstance(name, str):
            return
        clean = name.strip()
        if not clean:
            return
        lowered = clean.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        names.append(clean)

    _add(primary_name)
    _add(_get_user_name_for_token(token))
    entry = _get_token_config_entry(token) or {}
    _add(cast(Optional[str], entry.get('discordusername')))
    _add(cast(Optional[str], entry.get('name')))
    _add(current_user_name)
    return names


def _extract_tu_content_name(message: Dict[str, Any]) -> Optional[str]:
    content = message.get('content', '')
    if not isinstance(content, str):
        return None
    match = re.search(r'^\*\*(.+?)\*\*,\s+you', content, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _message_matches_identity_names(message: Dict[str, Any], allowed_names: List[str]) -> bool:
    if not allowed_names:
        return False
    normalized = {name.lower() for name in allowed_names if isinstance(name, str) and name.strip()}
    if not normalized:
        return False
    interaction_name = Fetch.extract_interaction_user_name(message)
    if interaction_name and interaction_name.lower() in normalized:
        return True
    content_name = _extract_tu_content_name(message)
    if content_name and content_name.lower() in normalized:
        return True
    return False


def _select_tu_response_message(
    messages: List[Dict[str, Any]],
    *,
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
    interaction_id: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], str]:
    if interaction_id and user_id:
        exact_candidates = _filter_messages_with_interaction(
            messages,
            user_id=user_id,
            command_name='tu',
            interaction_id=interaction_id,
            require_interaction=True,
        )
        if exact_candidates:
            return exact_candidates[0], "exact_interaction_user"

    if user_id:
        user_candidates = _filter_messages_with_interaction(
            messages,
            user_id=user_id,
            command_name='tu',
            require_interaction=True,
        )
        if user_candidates:
            return user_candidates[0], "user_command"

    allowed_names = _build_account_identity_names(token, user_name)
    if allowed_names:
        for message in messages:
            if not isinstance(message, dict):
                continue
            command = Fetch.extract_interaction_name(message)
            if command != 'tu':
                continue
            if _message_matches_identity_names(message, allowed_names):
                return message, "name_match"

    if allowed_names:
        tu_candidates: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            author = message.get('author', {})
            if not isinstance(author, dict) or str(author.get('id', '')) != str(botID):
                continue
            if not _message_matches_identity_names(message, allowed_names):
                continue
            content = message.get('content', '')
            if _parse_tu_message(content):
                tu_candidates.append(message)
        if len(tu_candidates) == 1:
            return tu_candidates[0], "single_tu_for_expected_user"

    if interaction_id and not user_id:
        interaction_candidates = _filter_messages_with_interaction(
            messages,
            command_name='tu',
            interaction_id=interaction_id,
            require_interaction=True,
        )
        if len(interaction_candidates) == 1:
            return interaction_candidates[0], "interaction_only"

    return None, "none"


def _get_target_mention_for_config_id(config_id: int) -> Tuple[Optional[str], Optional[str], str]:
    user_id = _get_discord_user_id_for_config_id(config_id)
    username = _get_discord_username_for_config_id(config_id)
    label = username or (f"id:{config_id}")
    if user_id:
        return (f"<@{user_id}>", label, "id")
    if username:
        return (f"@{username}", label, "username")
    return (None, None, "none")


def _normalize_give_pairs(raw_pairs: Any) -> List[Tuple[int, int]]:
    pairs: List[Tuple[int, int]] = []
    if not isinstance(raw_pairs, (list, tuple)):
        return pairs
    for item in raw_pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        src = _to_int_or_none(item[0])
        dst = _to_int_or_none(item[1])
        if src is None or dst is None:
            continue
        pairs.append((src, dst))
    return pairs


def _send_text_command(
    *,
    url: str,
    auth: Dict[str, str],
    content: str,
    raw_label: str,
) -> bool:
    try:
        response = requests.post(url=url, headers=auth, data={'content': content}, timeout=Fetch.get_timeout())
    except Exception as exc:
        log(f"[AUTO-GIVE] Failed to send '{content}': {exc}")
        return False
    logRawResponse(raw_label, response)
    logSessionRawResponse(raw_label, response)
    return response.status_code == 200


def _run_auto_give_from_tu(
    *,
    token: str,
    status: Dict[str, Any],
    url: str,
    auth: Dict[str, str],
) -> None:
    source_id = _get_token_config_id(token)
    if source_id is None:
        return

    kakera_pairs = _normalize_give_pairs(getattr(Vars, 'Kakera_Give', []))
    sphere_pairs = _normalize_give_pairs(getattr(Vars, 'Sphere_Give', []))
    kakera_balance = _to_int_or_none(status.get('total_balance')) or 0
    sphere_balance = _to_int_or_none(status.get('sphere_balance')) or 0

    def _dispatch(
        pairs: List[Tuple[int, int]],
        amount: int,
        give_cmd: str,
    ) -> None:
        if amount <= 0:
            return
        target_id: Optional[int] = None
        for src, dst in pairs:
            if src == source_id:
                target_id = dst
                break
        if target_id is None:
            return
        if target_id == source_id:
            log(f"[AUTO-GIVE] Skipped {give_cmd}: source id equals target id ({source_id}).")
            return
        target_mention, target_label, mention_mode = _get_target_mention_for_config_id(target_id)
        if not target_mention:
            log(f"[AUTO-GIVE] Skipped {give_cmd}: target id {target_id} has no resolvable mention/username.")
            return
        if mention_mode != "id":
            log(
                f"[AUTO-GIVE] Warning: using @{target_label} text fallback (no discord user id resolved). "
                "Use tokens[].discord_user_id (or valid token prefix) for stable mention."
            )

        command = f"{give_cmd} {target_mention} {amount}"
        ok = _send_text_command(
            url=url,
            auth=auth,
            content=command,
            raw_label=f"POST - {give_cmd} auto-give command",
        )
        if not ok:
            log(f"[AUTO-GIVE] Command failed: {command}")
            return
        log(f"[AUTO-GIVE] Sent: {command}")
        if _sleep_interruptible(2.0):
            return
        ok_confirm = _send_text_command(
            url=url,
            auth=auth,
            content='y',
            raw_label=f"POST - {give_cmd} auto-give confirm",
        )
        if ok_confirm:
            log(f"[AUTO-GIVE] Confirmed {give_cmd} transfer with 'y'.")
        else:
            log(f"[AUTO-GIVE] Confirm failed for {give_cmd} transfer.")

    _dispatch(kakera_pairs, kakera_balance, "$givek")
    _dispatch(sphere_pairs, sphere_balance, "$givesp")

def scanForManualClaims(token: str, target_username: str, include_persistent: bool = False) -> List[Dict[str, Any]]:
    """
    Scan raw responses for manual character claims made during this bot run.

    Important: Discord API message fetches can include old already-claimed cards. We only
    count claims whose claim-time is within this run (prefer edited_timestamp as claim time).
    """
    manual_claims: List[Dict[str, Any]] = []
    try:
        session_start_epoch = _session_start_epoch.get(token)
        if not isinstance(session_start_epoch, (int, float)) or session_start_epoch <= 0:
            session_start_epoch = float(time.time())
            _session_start_epoch[token] = session_start_epoch
        processed_ids = _processed_manual_claim_ids.setdefault(token, set())

        marriage_bonus: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def _iter_messages_from_entry(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
            """Extract Discord message objects from a log entry."""
            candidates: List[Any] = []
            body_json = entry.get('body_json')
            if isinstance(body_json, list):
                candidates = body_json
            elif isinstance(body_json, dict):
                candidates = [body_json]
            else:
                body_text = entry.get('body_text')
                if isinstance(body_text, str) and body_text.strip().startswith(('{', '[')):
                    try:
                        parsed = json.loads(body_text)
                        if isinstance(parsed, list):
                            candidates = parsed
                        elif isinstance(parsed, dict):
                            candidates = [parsed]
                    except json.JSONDecodeError:
                        candidates = []
            out: List[Dict[str, Any]] = []
            for item in candidates:
                if isinstance(item, dict):
                    out.append(item)
            return out

        def _iter_messages_from_json_log(path: str):
            for entry in iter_json_array(path):
                if not isinstance(entry, dict):
                    continue
                for msg in _iter_messages_from_entry(entry):
                    yield msg

        def _iter_messages_from_md_log(path: str):
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            json_pattern = r'\```json\n(.*?)\n\`\`\`'
            for json_str in re.findall(json_pattern, content, re.DOTALL):
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, list):
                    for msg in data:
                        if isinstance(msg, dict):
                            yield msg
                elif isinstance(data, dict):
                    yield data

        def _load_messages_from_log(path: str):
            if path.endswith('.json'):
                return _iter_messages_from_json_log(path)
            return _iter_messages_from_md_log(path)

        def _record_marriage_bonus(message: Dict[str, Any]) -> None:
            content = message.get('content', '')
            if not content or ('are now married!' not in content and 'weaved their fates together!' not in content):
                return
            timestamp = message.get('timestamp')
            parsed_ts = _parse_discord_timestamp(timestamp) if isinstance(timestamp, str) else None
            if parsed_ts is None or parsed_ts < session_start_epoch:
                return

            match = re.search(r'\*\*([^*]+)\*\* and \*\*([^*]+)\*\*', content)
            if not match:
                return
            user_name = match.group(1).strip()
            character_name = match.group(2).strip()
            kakera_match = re.search(r'\*\*\+([\d,]+)\*\*', content)
            if not kakera_match:
                return
            kakera = int(kakera_match.group(1).replace(',', ''))

            key = (user_name.lower(), character_name.lower())
            prev = marriage_bonus.get(key)
            if prev and isinstance(prev.get('timestamp'), (int, float)) and parsed_ts <= prev['timestamp']:
                return
            marriage_bonus[key] = {'kakera': kakera, 'timestamp': parsed_ts}

        # Prefer JSON logs; fall back to MD logs for backward compatibility.
        session_json = getSessionRawResponseFile()
        persistent_json = os.fspath(LOGS_DIR / 'Rawresponse.json')
        session_md = session_json[:-5] + '.md' if session_json.endswith('.json') else os.fspath(LOGS_DIR / 'SessionRawresponse.md')
        persistent_md = os.fspath(LOGS_DIR / 'Rawresponse.md')

        log_paths: List[str] = []
        if os.path.exists(session_json):
            log_paths.append(session_json)
        elif os.path.exists(session_md):
            log_paths.append(session_md)
        if include_persistent:
            if os.path.exists(persistent_json):
                log_paths.append(persistent_json)
            elif os.path.exists(persistent_md):
                log_paths.append(persistent_md)

        # Pass 1: collect marriage bonuses within this run.
        for path in log_paths:
            for msg in _load_messages_from_log(path):
                _record_marriage_bonus(msg)

        # Pass 2: detect claimed cards within this run.
        for path in log_paths:
            for msg in _load_messages_from_log(path):
                claim_info = detectManualClaim(msg, target_username)
                if not claim_info or not claim_info.get('is_ours'):
                    continue

                event_epoch = claim_info.get('event_epoch')
                if not isinstance(event_epoch, (int, float)) or event_epoch < session_start_epoch:
                    continue

                msg_id = claim_info.get('message_id') or (str(msg.get('id')) if msg.get('id') else None)
                if not msg_id:
                    msg_id = f"{claim_info.get('character_name', '')}|{claim_info.get('timestamp', '')}"
                msg_id_str = str(msg_id)
                if msg_id_str in processed_ids:
                    continue

                claimed_by = str(claim_info.get('claimed_by', target_username)).lower()
                bonus_key = (claimed_by, str(claim_info.get('character_name', '')).lower())
                bonus_info = marriage_bonus.get(bonus_key)
                if bonus_info:
                    claim_info['kakera'] = bonus_info['kakera']
                    if 'kakera_base' in claim_info:
                        claim_info['kakera_bonus'] = bonus_info['kakera'] - claim_info['kakera_base']

                manual_claims.append(claim_info)
                processed_ids.add(msg_id_str)

    except Exception as e:
        log(f"Error scanning for manual claims: {e}")

    return manual_claims

def _dashboard_runtime_str(start_ts: Optional[float]) -> str:
    if not start_ts:
        return "Unknown"
    elapsed = max(0, int(time.time() - start_ts))
    return formatTimeHrsMinSec(elapsed)


def _dashboard_emit_session_meta() -> None:
    emit_state(
        "session_meta",
        {
            "session_start": _dashboard_state.get("session_start"),
            "session_start_ts": _dashboard_state.get("session_start_ts"),
            "program_start": _dashboard_state.get("program_start"),
            "program_start_ts": _dashboard_state.get("program_start_ts"),
        },
    )


def _dashboard_emit_rolls() -> None:
    rolls = _dashboard_state.get("rolls", [])
    emit_state(
        "rolls",
        {
            "items": [dict(item) for item in rolls],
            "total": int(_dashboard_state.get("rolls_total") or len(rolls)),
            "target": _dashboard_state.get("rolls_target"),
            "remaining": _dashboard_state.get("rolls_remaining"),
        },
    )


def _dashboard_emit_other_rolls() -> None:
    others = _dashboard_state.get("others_rolls", [])
    emit_state("others_rolls", {"items": [dict(item) for item in others]})


def _dashboard_emit_connection_retry() -> None:
    emit_state(
        "connection_retry",
        {
            "active": bool(_dashboard_state.get("connection_retry_active")),
            "remaining": int(_dashboard_state.get("connection_retry_sec") or 0),
        },
    )

def _dashboard_reset_session(session_start: Optional[str] = None) -> None:
    _dashboard_state['session_start'] = session_start or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _dashboard_state['session_start_ts'] = time.time()
    if not _dashboard_state.get('program_start_ts'):
        _dashboard_state['program_start_ts'] = _PROGRAM_START_TS
        _dashboard_state['program_start'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_PROGRAM_START_TS))
    _dashboard_state['status'] = {}
    _dashboard_state['wishlist'] = {}
    _dashboard_state['rolls'] = []
    _dashboard_state['rolls_total'] = 0
    _dashboard_state['rolls_target'] = None
    _dashboard_state['rolls_remaining'] = None
    _dashboard_state['best_candidate'] = None
    _dashboard_state['others_rolls'] = []
    _dashboard_state['summary'] = {}
    _dashboard_state['predicted_status'] = ''
    _dashboard_state['predicted_at'] = ''
    _dashboard_state['countdown_active'] = False
    _dashboard_state['countdown_total'] = 0
    _dashboard_state['countdown_remaining'] = 0
    _dashboard_state['countdown_status'] = None
    if not _dashboard_state.get('connection_status'):
        _dashboard_state['connection_status'] = 'Connecting'
    _dashboard_state['connection_retry_active'] = False
    _dashboard_state['connection_retry_sec'] = 0
    _dashboard_state['state'] = 'INIT'
    _dashboard_state['last_action'] = ''
    _dashboard_state['next_action'] = ''
    _dashboard_state['last_render_lines'] = 0
    _dashboard_state['last_render_width'] = 0
    _dashboard_state['last_terminal_cols'] = 0
    _dashboard_state['last_terminal_rows'] = 0
    _dashboard_state['last_terminal_change_ts'] = 0.0
    _dashboard_state['anchor_pos'] = None
    _dashboard_state['renderer_mode'] = None
    _dashboard_state['last_status_line_at'] = 0.0
    _dashboard_state['last_status_line_len'] = 0
    _dashboard_emit_session_meta()
    emit_state("session_status", {})
    emit_state("wishlist", {})
    _dashboard_emit_rolls()
    _dashboard_emit_other_rolls()
    emit_state("best_candidate", None)
    emit_state("summary", {})
    emit_state("predicted", {"status": "", "minutes_to_wait": 0, "predicted_at": ""})
    _dashboard_emit_connection_retry()
    emit_state("dashboard_state", {"state": _dashboard_state["state"], "last_action": "", "next_action": ""})
    if DASHBOARD_ENABLED:
        render_dashboard()

def _dashboard_reset_roll_state(session_start: Optional[str] = None) -> None:
    if session_start:
        _dashboard_state['session_start'] = session_start
        _dashboard_state['session_start_ts'] = time.time()
    _dashboard_state['rolls'] = []
    _dashboard_state['rolls_total'] = 0
    _dashboard_state['rolls_target'] = None
    _dashboard_state['rolls_remaining'] = None
    _dashboard_state['best_candidate'] = None
    _dashboard_state['summary'] = {}
    _dashboard_state['countdown_active'] = False
    _dashboard_state['countdown_total'] = 0
    _dashboard_state['countdown_remaining'] = 0
    _dashboard_state['countdown_status'] = None
    _dashboard_state['connection_retry_active'] = False
    _dashboard_state['connection_retry_sec'] = 0
    _dashboard_state['last_render_lines'] = 0
    _dashboard_state['last_render_width'] = 0
    _dashboard_state['last_terminal_cols'] = 0
    _dashboard_state['last_terminal_rows'] = 0
    _dashboard_state['anchor_pos'] = None
    _dashboard_state['last_status_line_at'] = 0.0
    _dashboard_state['last_status_line_len'] = 0
    _dashboard_emit_session_meta()
    _dashboard_emit_rolls()
    emit_state("best_candidate", None)
    emit_state("summary", {})
    emit_state("predicted", {"status": "", "minutes_to_wait": 0, "predicted_at": ""})
    _dashboard_emit_connection_retry()
    if DASHBOARD_ENABLED:
        render_dashboard()

def _dashboard_set_status(status: Optional[Dict[str, Any]]) -> None:
    if not status:
        return
    _dashboard_state['status'] = status
    emit_state("session_status", status)

def _dashboard_set_wishlist(wishlist: Optional[Dict[str, Any]]) -> None:
    if not wishlist:
        return
    _dashboard_state['wishlist'] = wishlist
    emit_state("wishlist", wishlist)

def _dashboard_add_roll(entry: Dict[str, Any]) -> None:
    _dashboard_state['rolls'].append(entry)
    _dashboard_state['rolls_total'] = len(_dashboard_state['rolls'])
    _dashboard_emit_rolls()

def _dashboard_add_other_roll(entry: Dict[str, Any]) -> None:
    others = _dashboard_state.setdefault('others_rolls', [])
    others.append(entry)
    if len(others) > 20:
        del others[:-20]
    _dashboard_emit_other_rolls()

def _dashboard_mark_last_roll(key: str, value: Any) -> None:
    rolls = _dashboard_state.get('rolls')
    if not rolls:
        return
    rolls[-1][key] = value
    _dashboard_emit_rolls()

def _dashboard_set_roll_progress(remaining: Optional[int], target: Optional[int]) -> None:
    _dashboard_state['rolls_remaining'] = remaining
    if target is not None:
        _dashboard_state['rolls_target'] = target
    emit_state("roll_progress", {"remaining": remaining, "target": target})

def _dashboard_set_best_candidate(candidate: Optional[Dict[str, Any]]) -> None:
    _dashboard_state['best_candidate'] = candidate
    emit_state("best_candidate", candidate)

def _dashboard_set_summary(summary: Dict[str, Any]) -> None:
    _dashboard_state['summary'] = summary
    emit_state("summary", summary)

def _dashboard_set_predicted(status: str, minutes_to_wait: int) -> None:
    next_time = time.localtime(time.time() + minutes_to_wait * 60)
    _dashboard_state['predicted_at'] = time.strftime("%H:%M", next_time)
    _dashboard_state['predicted_status'] = status
    emit_state("predicted", {"status": status, "minutes_to_wait": minutes_to_wait, "predicted_at": _dashboard_state['predicted_at']})

def setConnectionStatus(status: str) -> None:
    _dashboard_state['connection_status'] = status
    emit_state("connection_status", {"status": status})
    if not DASHBOARD_ENABLED:
        return
    render_dashboard()

def startConnectionRetry(seconds_remaining: int) -> None:
    _dashboard_state['connection_retry_active'] = True
    _dashboard_state['connection_retry_sec'] = max(0, int(seconds_remaining))
    _dashboard_emit_connection_retry()
    if DASHBOARD_ENABLED:
        render_dashboard()

def updateConnectionRetry(seconds_remaining: int) -> None:
    if not _dashboard_state.get('connection_retry_active'):
        return
    _dashboard_state['connection_retry_sec'] = max(0, int(seconds_remaining))
    _dashboard_emit_connection_retry()
    if DASHBOARD_ENABLED:
        render_dashboard()

def stopConnectionRetry() -> None:
    _dashboard_state['connection_retry_active'] = False
    _dashboard_state['connection_retry_sec'] = 0
    _dashboard_emit_connection_retry()
    if DASHBOARD_ENABLED:
        render_dashboard()

def setDashboardState(state: str, last_action: Optional[str] = None, next_action: Optional[str] = None) -> None:
    _dashboard_state['state'] = state
    if last_action is not None:
        _dashboard_state['last_action'] = last_action
    if next_action is not None:
        _dashboard_state['next_action'] = next_action
    emit_state(
        "dashboard_state",
        {
            "state": state,
            "last_action": _dashboard_state.get('last_action'),
            "next_action": _dashboard_state.get('next_action'),
        },
    )
    if not DASHBOARD_ENABLED:
        return
    render_dashboard()

def startDashboardCountdown(status: Optional[Dict[str, Any]], total_seconds: int) -> None:
    if not status or total_seconds <= 0:
        return
    _dashboard_state['countdown_active'] = True
    _dashboard_state['countdown_total'] = total_seconds
    _dashboard_state['countdown_remaining'] = total_seconds
    _dashboard_state['countdown_status'] = status.copy()
    emit_state("countdown", {"active": True, "remaining": total_seconds, "total": total_seconds})
    if not DASHBOARD_ENABLED:
        return
    render_dashboard()

def updateDashboardCountdown(seconds_remaining: int) -> None:
    if not _dashboard_state.get('countdown_active'):
        return
    _dashboard_state['countdown_remaining'] = max(0, seconds_remaining)
    emit_state(
        "countdown",
        {
            "active": True,
            "remaining": _dashboard_state['countdown_remaining'],
            "total": _dashboard_state.get('countdown_total'),
        },
    )
    if not DASHBOARD_ENABLED:
        return
    render_dashboard()

def stopDashboardCountdown() -> None:
    _dashboard_state['countdown_active'] = False
    _dashboard_state['countdown_total'] = 0
    _dashboard_state['countdown_remaining'] = 0
    _dashboard_state['countdown_status'] = None
    emit_state("countdown", {"active": False, "remaining": 0, "total": 0})

def _dashboard_console_viewport_size() -> Optional[Tuple[int, int]]:
    """Return visible console viewport size (cols, rows) when available."""
    if os.name != 'nt':
        return None
    try:
        import ctypes  # type: ignore[import]
        from ctypes import wintypes  # type: ignore[import]

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        def _read_size_from_handle(handle: Any) -> Optional[Tuple[int, int]]:
            info = CONSOLE_SCREEN_BUFFER_INFO()
            if not handle:
                return None
            if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)) == 0:
                return None
            cols = int(info.srWindow.Right - info.srWindow.Left + 1)
            rows = int(info.srWindow.Bottom - info.srWindow.Top + 1)
            if cols <= 0 or rows <= 0:
                return None
            return (cols, rows)

        kernel32 = ctypes.windll.kernel32
        invalid_handle = ctypes.c_void_p(-1).value

        # First try the process stdout handle.
        std_handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if std_handle and std_handle != invalid_handle:
            size = _read_size_from_handle(std_handle)
            if size is not None:
                return size

        # Fallback for ConPTY/Windows Terminal edge cases.
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        conout = kernel32.CreateFileW(
            "CONOUT$",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if conout and conout != invalid_handle:
            try:
                size = _read_size_from_handle(conout)
                if size is not None:
                    return size
            finally:
                kernel32.CloseHandle(conout)
        return None
    except Exception:
        return None

def _dashboard_no_scroll_enabled() -> bool:
    return bool(getattr(Vars, 'DASHBOARD_NO_SCROLL', True))

def _dashboard_widechar_aware() -> bool:
    return bool(getattr(Vars, 'DASHBOARD_WIDECHAR_AWARE', True))

def _dashboard_safety_cols() -> int:
    try:
        val = int(getattr(Vars, 'DASHBOARD_RENDER_SAFETY_COLS', 2))
    except (TypeError, ValueError):
        val = 2
    return max(0, val)

def _dashboard_safety_rows() -> int:
    try:
        val = int(getattr(Vars, 'DASHBOARD_RENDER_SAFETY_ROWS', 2))
    except (TypeError, ValueError):
        val = 2
    return max(0, val)

def _dashboard_renderer_mode_hint() -> str:
    mode = str(getattr(Vars, 'DASHBOARD_RENDERER_MODE', 'auto') or 'auto').strip().lower()
    if mode in {"auto", "ansi_full", "win32", "legacy_clear", "status_line"}:
        return mode
    return "auto"

def _dashboard_should_probe_viewport() -> bool:
    """
    Decide whether to query Win32 viewport directly.
    In Windows Terminal + ANSI rendering, os.get_terminal_size is more reliable
    per-instance for multi-window runs.
    """
    if os.name != 'nt':
        return False
    mode_hint = _dashboard_renderer_mode_hint()
    if mode_hint == "win32":
        return True
    if mode_hint in {"ansi_full", "legacy_clear", "status_line"}:
        return False
    if os.environ.get('WT_SESSION'):
        return False
    return True

def _dashboard_get_os_terminal_size() -> Optional[os.terminal_size]:
    try:
        fileno = sys.stdout.fileno()
    except Exception:
        fileno = None

    if fileno is not None:
        try:
            return os.get_terminal_size(fileno)
        except TypeError:
            # Some tests monkeypatch get_terminal_size with a zero-arg callable.
            pass
        except OSError:
            pass
    try:
        return os.get_terminal_size()
    except OSError:
        return None

def _dashboard_terminal_cols(default: int = 78) -> int:
    viewport = _dashboard_console_viewport_size() if _dashboard_should_probe_viewport() else None
    if viewport is not None:
        cols = int(viewport[0])
    else:
        term_size = _dashboard_get_os_terminal_size()
        if term_size is not None:
            cols = int(term_size.columns)
        else:
            cols = default
    return max(20, cols)

def _dashboard_terminal_rows(default: int = 30) -> int:
    viewport = _dashboard_console_viewport_size() if _dashboard_should_probe_viewport() else None
    if viewport is not None:
        rows = int(viewport[1])
    else:
        term_size = _dashboard_get_os_terminal_size()
        if term_size is not None:
            rows = int(term_size.lines)
        else:
            rows = default
    return max(8, rows)

def _dashboard_mark_layout_dirty(reset_line_count: bool = True) -> None:
    if reset_line_count:
        _dashboard_state['last_render_lines'] = 0
    _dashboard_state['last_render_width'] = 0
    _dashboard_state['anchor_pos'] = None

def _dashboard_block_title(block: List[str]) -> str:
    if len(block) < 2:
        return ""
    raw = _ANSI_ESCAPE_RE.sub("", block[1]).strip()
    if raw.startswith("|") and raw.endswith("|"):
        raw = raw[1:-1].strip()
    return raw

def _dashboard_compact_block(block: List[str], width: int) -> List[str]:
    """Compact a section block to minimal informative form."""
    # Expected format: border, title, border, content..., border
    if len(block) <= 5:
        return block
    hidden = max(0, len(block) - 5)
    compact_line = _dashboard_line(f"... {hidden} more lines hidden", width)
    return [block[0], block[1], block[2], compact_line, block[-1]]

def _dashboard_reserved_rows() -> int:
    try:
        reserve_rows = int(os.environ.get('MUDAE_DASHBOARD_RESERVED_ROWS', '3') or 3)
    except (TypeError, ValueError):
        reserve_rows = 3
    return max(0, reserve_rows)

def _dashboard_fit_height(lines: List[str], width: int, budget_rows: Optional[int] = None) -> List[str]:
    """Fit dashboard output into current terminal viewport height."""
    fit_enabled = os.environ.get('MUDAE_DASHBOARD_FIT_HEIGHT', '1') != '0'
    no_scroll = _dashboard_no_scroll_enabled()
    if not fit_enabled and not no_scroll:
        return lines

    if budget_rows is None:
        max_rows = _dashboard_terminal_rows()
        budget = max(8, max_rows - _dashboard_reserved_rows() - _dashboard_safety_rows())
    else:
        budget = max(8, int(budget_rows))
    if len(lines) <= budget:
        return lines

    # Split into header (first block) and section blocks separated by blank lines.
    first_blank = lines.index("") if "" in lines else len(lines)
    header = lines[:first_blank + 1] if first_blank < len(lines) else lines[:]
    blocks: List[List[str]] = []
    i = first_blank + 1
    while i < len(lines):
        while i < len(lines) and lines[i] == "":
            i += 1
        if i >= len(lines):
            break
        start = i
        while i < len(lines) and lines[i] != "":
            i += 1
        blocks.append(lines[start:i])

    def _compose(active_blocks: List[List[str]]) -> List[str]:
        out = header[:]
        for block in active_blocks:
            if out and out[-1] != "":
                out.append("")
            out.extend(block)
        return out

    active_blocks = blocks[:]
    composed = _compose(active_blocks)
    if len(composed) <= budget:
        return composed

    # Drop lower-priority sections first when viewport is too short.
    drop_order = [
        "OTHERS' ROLLS (20 Most Recent)",
        "BEST CANDIDATE LATEST SESSION",
        "WISHLIST STATUS",
        "OURO STATUS",
        "LATEST ROLLS",
    ]
    for title in drop_order:
        if len(composed) <= budget:
            break
        idx = next((j for j, b in enumerate(active_blocks) if _dashboard_block_title(b) == title), None)
        if idx is not None:
            del active_blocks[idx]
            composed = _compose(active_blocks)

    # If still too tall, compact remaining blocks.
    if len(composed) > budget:
        compacted: List[List[str]] = []
        for block in active_blocks:
            compacted.append(_dashboard_compact_block(block, width))
        active_blocks = compacted
        composed = _compose(active_blocks)

    if len(composed) <= budget:
        return composed

    # Final hard cap: ensure never scrolls, include explicit indicator.
    keep = max(1, budget - 1)
    trimmed = composed[:keep]
    hidden_lines = max(0, len(composed) - keep)
    trimmed.append(_dashboard_truncate(f"... {hidden_lines} lines hidden (resize window for full dashboard)", width))
    return trimmed

def _dashboard_start_resize_watcher() -> None:
    """Re-render dashboard when terminal width changes at runtime."""
    global _dashboard_resize_watcher_started
    if _dashboard_resize_watcher_started:
        return
    if not DASHBOARD_ENABLED or not sys.stdout.isatty():
        return
    # Default off: render loop already re-checks terminal size frequently.
    if os.environ.get('MUDAE_DASHBOARD_RESIZE_WATCH', '0') == '0':
        return

    try:
        poll_sec = float(os.environ.get('MUDAE_DASHBOARD_RESIZE_POLL_SEC', '0.25') or 0.25)
    except (TypeError, ValueError):
        poll_sec = 0.25
    poll_sec = max(0.1, min(1.0, poll_sec))

    _dashboard_resize_watcher_started = True
    _dashboard_state['last_terminal_cols'] = _dashboard_terminal_cols()
    _dashboard_state['last_terminal_rows'] = _dashboard_terminal_rows()

    def _watcher() -> None:
        last_cols = int(_dashboard_state.get('last_terminal_cols') or _dashboard_terminal_cols())
        last_rows = int(_dashboard_state.get('last_terminal_rows') or _dashboard_terminal_rows())
        while True:
            time.sleep(poll_sec)
            try:
                cols = _dashboard_terminal_cols()
                rows = _dashboard_terminal_rows()
                if cols == last_cols and rows == last_rows:
                    continue
                last_cols = cols
                last_rows = rows
                _dashboard_state['last_terminal_cols'] = cols
                _dashboard_state['last_terminal_rows'] = rows
                now = time.time()
                last_change_ts = float(_dashboard_state.get('last_terminal_change_ts') or 0.0)
                if now - last_change_ts < max(0.2, poll_sec):
                    continue
                _dashboard_state['last_terminal_change_ts'] = now
                render_dashboard()
            except Exception:
                continue

    threading.Thread(target=_watcher, name="mudae-dashboard-resize", daemon=True).start()

def _dashboard_width() -> int:
    default_width = 78
    cols = _dashboard_terminal_cols(default_width)
    if _dashboard_no_scroll_enabled():
        cols = max(20, cols - _dashboard_safety_cols())

    try:
        min_width = int(getattr(Vars, 'DASHBOARD_MIN_WIDTH', 60) or 60)
    except (TypeError, ValueError):
        min_width = 60
    try:
        max_width = int(getattr(Vars, 'DASHBOARD_MAX_WIDTH', 120) or 120)
    except (TypeError, ValueError):
        max_width = 120

    auto_fit = bool(getattr(Vars, 'DASHBOARD_AUTO_FIT', True))

    # Keep sane bounds and avoid invalid states.
    min_width = max(20, min_width)
    if max_width <= 0:
        max_width = cols
    max_width = max(min_width, max_width)

    if auto_fit:
        # For narrow windows, prioritize fitting instead of forcing min width.
        return max(20, min(cols, max_width))

    # Legacy style: keep preferred minimum width (may exceed narrow windows).
    return max(min_width, min(cols, max_width))

def _dashboard_truncate(text: str, width: int) -> str:
    visible_len = _dashboard_visible_len(text)
    if visible_len <= width:
        return text
    if width <= 3:
        return _dashboard_slice_visible(text, width)
    return _dashboard_slice_visible(text, width - 3) + "..."

def _dashboard_wrap_lines(text: str, width: int) -> List[str]:
    if width <= 0:
        return [text]
    wrapped_lines: List[str] = []
    for part in text.splitlines():
        if not part:
            wrapped_lines.append("")
            continue
        remaining = part
        while remaining:
            if _dashboard_visible_len(remaining) <= width:
                wrapped_lines.append(remaining)
                break
            chunk = _dashboard_slice_visible(remaining, width)
            if not chunk:
                break
            wrapped_lines.append(chunk)
            remaining = remaining[len(chunk):]
    return wrapped_lines

def _dashboard_sanitize_text(text: str) -> str:
    replacements = {
        "\u2705": "OK",
        "\u274c": "X",
        "\u26a0\ufe0f": "!",
        "\u26a0": "!",
        "\u2b50": "*",
        "\u00e2\u009c\u0085": "OK",
        "\u00e2\u009d\u008c": "X",
        "\u00e2\u00ad\u0090": "*",
        "âœ…": "OK",
        "âŒ": "X",
        "â­": "*",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

def _dashboard_char_width(ch: str) -> int:
    if not ch:
        return 0
    codepoint = ord(ch)
    if codepoint in (0xFE0E, 0xFE0F):
        return 0
    if unicodedata.combining(ch):
        return 0
    if _dashboard_widechar_aware():
        # Emoji and dingbat ranges are rendered as double-width in Windows Terminal.
        if (
            0x1F000 <= codepoint <= 0x1FAFF
            or 0x2600 <= codepoint <= 0x26FF
            or 0x2700 <= codepoint <= 0x27BF
            or codepoint == 0x2B50
            or unicodedata.east_asian_width(ch) in ("W", "F")
        ):
            return 2
    return 1

def _dashboard_visible_len(text: str) -> int:
    plain = _ANSI_ESCAPE_RE.sub("", text)
    width = 0
    for ch in plain:
        width += _dashboard_char_width(ch)
    return width

def _dashboard_slice_visible(text: str, max_visible: int) -> str:
    if max_visible <= 0:
        return ""
    out: List[str] = []
    visible = 0
    i = 0
    while i < len(text) and visible < max_visible:
        if text[i] == "\x1b":
            match = _ANSI_ESCAPE_RE.match(text, i)
            if match:
                out.append(match.group(0))
                i = match.end()
                continue
        ch = text[i]
        ch_width = _dashboard_char_width(ch)
        if visible + ch_width > max_visible:
            break
        out.append(ch)
        visible += ch_width
        i += 1
    result = "".join(out)
    if _ANSI_ESCAPE_RE.search(result):
        result += ANSIColors.RESET
    return result

def _dashboard_border(width: int) -> str:
    return "+" + "-" * (width - 2) + "+"

def _dashboard_line(text: str, width: int) -> str:
    inner = width - 4
    trimmed = _dashboard_truncate(text, inner)
    visible = _dashboard_visible_len(trimmed)
    if visible < inner:
        trimmed = trimmed + (" " * (inner - visible))
    return f"| {trimmed} |"

def _dashboard_section(title: str, lines: List[str], width: int) -> List[str]:
    section_lines = [_dashboard_border(width), _dashboard_line(title, width), _dashboard_border(width)]
    for line in lines:
        section_lines.append(_dashboard_line(line, width))
    section_lines.append(_dashboard_border(width))
    return section_lines

def _dashboard_clear(force: bool = False) -> None:
    if not DASHBOARD_CLEAR and not force:
        return
    if not sys.stdout.isatty():
        return
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def _enable_ansi_if_needed() -> bool:
    global _ansi_available
    if _ansi_available is not None:
        return _ansi_available
    if os.name != 'nt':
        _ansi_available = True
        return True
    if os.environ.get('MUDAE_FORCE_ANSI', '') == '1':
        _ansi_available = True
        return True
    try:
        import ctypes  # type: ignore[import]
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            OPEN_EXISTING = 3
            handle = kernel32.CreateFileW(
                "CONOUT$",
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None
            )
            if handle == -1 or kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
                _ansi_available = False
                return False
        enable_vt = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if kernel32.SetConsoleMode(handle, enable_vt) == 0:
            _ansi_available = False
            return False
        _ansi_available = True
    except Exception:
        _ansi_available = False
    return _ansi_available

_DASHBOARD_RENDERER_MODES = {"auto", "ansi_full", "win32", "legacy_clear", "status_line"}

def _dashboard_renderer_config_mode() -> str:
    mode = str(getattr(Vars, 'DASHBOARD_RENDERER_MODE', 'auto') or 'auto').strip().lower()
    if mode not in _DASHBOARD_RENDERER_MODES:
        return "auto"
    return mode

def _dashboard_resolve_renderer_mode(force_clear: bool) -> str:
    if not getattr(Vars, 'DASHBOARD_LIVE_REDRAW', True):
        _dashboard_state['renderer_mode'] = 'status_line'
        return 'status_line'

    config_mode = _dashboard_renderer_config_mode()
    pinned = _dashboard_state.get('renderer_mode')
    if config_mode != 'auto':
        _dashboard_state['renderer_mode'] = config_mode
        return config_mode

    if isinstance(pinned, str) and pinned in _DASHBOARD_RENDERER_MODES and pinned != 'auto':
        return pinned

    if force_clear:
        chosen = 'legacy_clear'
    elif os.name == 'nt':
        if _enable_ansi_if_needed():
            chosen = 'ansi_full'
        elif sys.stdout.isatty():
            chosen = 'win32'
        else:
            chosen = 'legacy_clear'
    else:
        chosen = 'ansi_full' if sys.stdout.isatty() else 'legacy_clear'

    _dashboard_state['renderer_mode'] = chosen
    return chosen

def _render_dashboard_ansi_full(lines: List[str]) -> bool:
    if not sys.stdout.isatty():
        return False
    try:
        out: List[str] = ["\x1b[H\x1b[2J"]
        for idx, line in enumerate(lines):
            out.append("\x1b[2K")
            out.append(line)
            if idx < len(lines) - 1:
                out.append("\n")
        out.append("\x1b[0m")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        _dashboard_state['last_render_lines'] = len(lines)
        return True
    except Exception:
        return False

def _render_dashboard_legacy_clear(lines: List[str]) -> bool:
    _dashboard_mark_layout_dirty()
    _dashboard_clear(force=True)
    print("\n".join(lines), flush=True)
    _dashboard_state['last_render_lines'] = len(lines)
    return True

def _render_dashboard_win32(lines: List[str], width: int) -> bool:
    if os.name != 'nt' or not sys.stdout.isatty():
        return False
    try:
        import ctypes  # type: ignore[import]
        from ctypes import wintypes  # type: ignore[import]

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)) == 0:
            return False

        visible_cols = int(info.srWindow.Right - info.srWindow.Left + 1)
        visible_rows = int(info.srWindow.Bottom - info.srWindow.Top + 1)
        if visible_cols <= 0 or visible_rows <= 0:
            return False

        # Enforce viewport-based fit here as a hard guard (works even when external size
        # probing drifts under multi-instance + zoom scenarios).
        render_width = max(20, min(width, visible_cols))
        render_lines = _dashboard_fit_height(
            lines,
            render_width,
            budget_rows=max(8, visible_rows - _dashboard_reserved_rows()),
        )
        normalized_lines: List[str] = []
        for line in render_lines:
            trimmed = _dashboard_truncate(line, render_width)
            visible = _dashboard_visible_len(trimmed)
            if visible < render_width:
                trimmed = trimmed + (" " * (render_width - visible))
            normalized_lines.append(trimmed)

        window_top = int(info.srWindow.Top)
        window_bottom = int(info.srWindow.Bottom)
        anchor = _dashboard_state.get('anchor_pos')
        if not anchor:
            anchor = (0, window_top)
            _dashboard_state['anchor_pos'] = anchor
        else:
            anchor_x, anchor_y = int(anchor[0]), int(anchor[1])
            # Re-anchor if old cursor is outside current viewport (common after zoom).
            if anchor_y < window_top or anchor_y > window_bottom:
                anchor = (0, window_top)
                _dashboard_state['anchor_pos'] = anchor
        coord = COORD(anchor[0], anchor[1])
        if kernel32.SetConsoleCursorPosition(handle, coord) == 0:
            return False

        last_lines = int(_dashboard_state.get('last_render_lines', 0) or 0)
        extra = max(0, last_lines - len(normalized_lines))
        padded_lines = normalized_lines[:]
        if extra:
            padded_lines.extend([" " * render_width] * extra)
        text = "\r\n".join(padded_lines)

        written = wintypes.DWORD()
        if kernel32.WriteConsoleW(handle, text, len(text), ctypes.byref(written), None) == 0:
            return False

        _dashboard_state['last_render_lines'] = len(normalized_lines)
        _dashboard_state['last_render_width'] = render_width
        return True
    except Exception:
        return False

def _dashboard_emit_status_line(force: bool = False) -> None:
    if not DASHBOARD_ENABLED:
        return
    try:
        interval = float(getattr(Vars, 'DASHBOARD_STATUS_LOG_SEC', 10) or 10)
    except (TypeError, ValueError):
        interval = 10
    interval = max(1.0, interval)
    now = time.time()
    last_at = float(_dashboard_state.get('last_status_line_at') or 0.0)
    if not force and (now - last_at) < interval:
        return

    state = _dashboard_state.get('state') or 'INIT'
    last_action = _dashboard_state.get('last_action') or ''
    next_action = _dashboard_state.get('next_action') or ''
    countdown_active = bool(_dashboard_state.get('countdown_active'))
    countdown_remaining = int(_dashboard_state.get('countdown_remaining') or 0)
    last_message = _dashboard_state.get('last_message') or ''
    conn_status = _dashboard_state.get('connection_status') or ''
    conn_retry_active = bool(_dashboard_state.get('connection_retry_active'))
    conn_retry_sec = int(_dashboard_state.get('connection_retry_sec') or 0)

    parts = [f"STATE={state}"]
    if conn_status:
        parts.append(f"conn={conn_status}")
    if conn_retry_active and conn_retry_sec:
        parts.append(f"reconnect={formatTimeHrsMinSec(conn_retry_sec)}")
    if last_action:
        parts.append(f"last={last_action}")
    if next_action:
        parts.append(f"next={next_action}")
    if countdown_active and countdown_remaining:
        parts.append(f"cd={formatTimeHrsMinSec(countdown_remaining)}")
    if last_message:
        parts.append(f"msg={last_message}")

    line = " | ".join(parts)
    width = _dashboard_width()
    line = _dashboard_truncate(_dashboard_sanitize_text(line), width)
    curr_len = _dashboard_visible_len(line)
    prev_len = int(_dashboard_state.get('last_status_line_len') or 0)
    if prev_len > curr_len:
        line = line + (" " * (prev_len - curr_len))

    sys.stdout.write("\r" + line)
    sys.stdout.flush()
    _dashboard_state['last_status_line_at'] = now
    _dashboard_state['last_status_line_len'] = _dashboard_visible_len(line)

def render_dashboard(clear: bool = True) -> None:
    if not DASHBOARD_ENABLED:
        return
    with _dashboard_render_lock:
        force_clear_setting = bool(getattr(Vars, 'DASHBOARD_FORCE_CLEAR', False))
        force_clear_env = os.environ.get('MUDAE_FORCE_CLEAR', '') == '1'
        force_clear = force_clear_setting or force_clear_env
        if not force_clear and not getattr(Vars, 'DASHBOARD_LIVE_REDRAW', True):
            _dashboard_emit_status_line()
            return
        term_cols = _dashboard_terminal_cols()
        term_rows = _dashboard_terminal_rows()
        prev_cols = int(_dashboard_state.get('last_terminal_cols') or 0)
        prev_rows = int(_dashboard_state.get('last_terminal_rows') or 0)
        if prev_cols and prev_rows and (prev_cols != term_cols or prev_rows != term_rows):
            _dashboard_mark_layout_dirty(reset_line_count=False)
            _dashboard_state['last_terminal_change_ts'] = time.time()
        _dashboard_state['last_terminal_cols'] = term_cols
        _dashboard_state['last_terminal_rows'] = term_rows
        width = _dashboard_width()
        prev_width = int(_dashboard_state.get('last_render_width') or 0)
        if prev_width and prev_width != width:
            _dashboard_mark_layout_dirty(reset_line_count=False)
            _dashboard_state['last_terminal_change_ts'] = time.time()
        inner_width = width - 4
        lines: List[str] = []

        user = _dashboard_state.get('user') or 'Unknown'
        program_start = _dashboard_state.get('program_start') or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_PROGRAM_START_TS))
        program_runtime = _dashboard_runtime_str(_dashboard_state.get('program_start_ts'))
        session_start = _dashboard_state.get('session_start')

        lines.append(_dashboard_border(width))
        lines.append(_dashboard_line(f"MUDAE BOT SESSION - User: {user}", width))
        lines.append(_dashboard_line(f"Start: {program_start}", width))
        lines.append(_dashboard_line(f"Runtime: {program_runtime}", width))
        if session_start:
            lines.append(_dashboard_line(f"Last roll start: {session_start}", width))
        else:
            lines.append(_dashboard_line("Last roll start: N/A", width))
        conn_status = _dashboard_state.get('connection_status') or 'Connecting'
        lines.append(_dashboard_line(f"Connection: {conn_status}", width))
        if _dashboard_state.get('connection_retry_active'):
            retry_sec = int(_dashboard_state.get('connection_retry_sec') or 0)
            lines.append(_dashboard_line(f"Retrying to connect in: {formatTimeHrsMinSec(retry_sec)}", width))
        lines.append(_dashboard_border(width))
        lines.append("")

    status = _dashboard_state.get('status', {})
    countdown_active = _dashboard_state.get('countdown_active', False)
    countdown_status = _dashboard_state.get('countdown_status')
    countdown_total = _dashboard_state.get('countdown_total', 0)
    countdown_remaining = _dashboard_state.get('countdown_remaining', 0)
    use_countdown = bool(countdown_active and countdown_status)
    active_status = countdown_status if use_countdown else status

    status_lines: List[str] = []
    if not status and not use_countdown:
        status_lines.append("Status unavailable")
    else:
        state_label = _dashboard_state.get('state')
        if state_label:
            status_lines.append(f"State: {state_label}")
        last_action = _dashboard_state.get('last_action')
        if last_action:
            status_lines.append(f"Last action: {last_action}")
        next_action = _dashboard_state.get('next_action')
        if next_action:
            status_lines.append(f"Next action: {next_action}")
        rolls_left = active_status.get('rolls', 0)
        next_reset_min = active_status.get('next_reset_min', 0)
        claim_reset_min = active_status.get('claim_reset_min', 0)
        daily_reset_min = active_status.get('daily_reset_min', 0)
        rt_reset_min = active_status.get('rt_reset_min', 0)
        dk_reset_min = active_status.get('dk_reset_min', 0)
        can_claim_now = active_status.get('can_claim_now', False)
        daily_available = active_status.get('daily_available', False)
        rt_available = active_status.get('rt_available', False)
        dk_ready = active_status.get('dk_ready', False)
        current_power = active_status.get('current_power', 0)
        max_power = active_status.get('max_power', 100)
        kakera_cost = active_status.get('kakera_cost', 40) or 40

        if use_countdown:
            elapsed = max(0, countdown_total - countdown_remaining)
            base_next_sec = active_status.get('next_reset_sec')
            base_claim_sec = active_status.get('claim_reset_sec')
            next_reset_sec = max(0, int(base_next_sec) - elapsed) if isinstance(base_next_sec, int) else max(0, int(next_reset_min) * 60 - elapsed)
            claim_reset_sec = max(0, int(base_claim_sec) - elapsed) if isinstance(base_claim_sec, int) else max(0, int(claim_reset_min) * 60 - elapsed)
            daily_reset_sec = max(0, int(daily_reset_min) * 60 - elapsed)
            rt_reset_sec = max(0, int(rt_reset_min) * 60 - elapsed)
            dk_reset_sec = max(0, int(dk_reset_min) * 60 - elapsed)
            power_regen = elapsed // 180
            current_power = min(max_power, current_power + power_regen)
            can_claim_now = can_claim_now or claim_reset_sec == 0
            daily_available = daily_available or daily_reset_sec == 0
            rt_available = rt_available or rt_reset_sec == 0
            dk_ready = dk_ready or dk_reset_sec == 0
        else:
            next_reset_sec = 0
            claim_reset_sec = 0
            daily_reset_sec = 0
            rt_reset_sec = 0
            dk_reset_sec = 0

        rolls_remaining = _dashboard_state.get('rolls_remaining')
        rolls_left = rolls_remaining if rolls_remaining is not None else rolls_left
        if use_countdown:
            if next_reset_sec > 0:
                rolls_line = f"❌ Rolls: 0 ({formatTimeHrsMinSec(next_reset_sec)} remaining)"
            else:
                rolls_line = f"✅ Rolls: {rolls_left}"
        else:
            if rolls_left > 0:
                rolls_line = f"✅ Rolls: {rolls_left}"
            else:
                rolls_line = "❌ Rolls: 0"
                if next_reset_min:
                    rolls_line += f" ({formatTimeHrsMin(next_reset_min)} remaining)"
        status_lines.append(rolls_line)

        if can_claim_now:
            if use_countdown and claim_reset_min == 0 and claim_reset_sec == 0:
                status_lines.append("✅ Claim: Available")
            elif use_countdown:
                status_lines.append(f"✅ Claim: Available ({formatTimeHrsMinSec(claim_reset_sec)} until next refresh)")
            else:
                status_lines.append(f"✅ Claim: Available ({formatTimeHrsMin(claim_reset_min)} until next refresh)")
        else:
            claim_line = "❌ Claim: Cooldown"
            if use_countdown and claim_reset_sec:
                claim_line += f" ({formatTimeHrsMinSec(claim_reset_sec)} remaining)"
            elif claim_reset_min:
                claim_line += f" ({formatTimeHrsMin(claim_reset_min)} remaining)"
            status_lines.append(claim_line)

        if use_countdown:
            if current_power >= kakera_cost:
                reacts = current_power // kakera_cost
                status_lines.append(f"✅ Power: {current_power}% ({reacts}x React Bonus)")
            else:
                wait_sec = max(0, (kakera_cost - current_power) * 180)
                status_lines.append(f"❌ Power: {current_power}% ({formatTimeHrsMinSec(wait_sec)} until Cost {kakera_cost}%)")
        else:
            power_str, _ = calculatePowerStats(current_power, kakera_cost, dk_ready, max_power=max_power)
            status_lines.append(power_str)

        if daily_available:
            status_lines.append("✅ Daily: Available")
        else:
            if use_countdown:
                status_lines.append(f"❌ Daily: {formatTimeHrsMinSec(daily_reset_sec)}")
            else:
                status_lines.append(f"❌ Daily: {formatTimeHrsMin(daily_reset_min)}")

        if rt_available:
            status_lines.append("✅ Reset Claimtimer ($rt): Available")
        else:
            if use_countdown:
                status_lines.append(f"❌ Reset Claimtimer ($rt): {formatTimeHrsMinSec(rt_reset_sec)}")
            else:
                status_lines.append(f"❌ Reset Claimtimer ($rt): {formatTimeHrsMin(rt_reset_min)}")

        if dk_ready:
            status_lines.append("✅ Daily Kakera ($dk): Available")
        else:
            if use_countdown:
                status_lines.append(f"❌ Daily Kakera ($dk): {formatTimeHrsMinSec(dk_reset_sec)}")
            else:
                status_lines.append(f"❌ Daily Kakera ($dk): {formatTimeHrsMin(dk_reset_min)}")

        lines.extend(_dashboard_section("SESSION STATUS", status_lines, width))
        lines.append("")

        wishlist = _dashboard_state.get('wishlist', {})
        wishlist_lines: List[str] = []
        if not wishlist:
            wishlist_lines.append("Wishlist not loaded")
        else:
            star_wishes = wishlist.get('star_wishes', [])
            regular_wishes = wishlist.get('regular_wishes', [])
            wl_used = wishlist.get('wl_used')
            wl_total = wishlist.get('wl_total')
            sw_used = wishlist.get('sw_used')
            sw_total = wishlist.get('sw_total')
            if wl_used is not None and wl_total is not None and sw_used is not None and sw_total is not None:
                wishlist_lines.append(f"Total: {wl_used}/{wl_total} $wl + {sw_used}/{sw_total} $sw")
            else:
                wishlist_lines.append(f"Total: {len(regular_wishes)} $wl + {len(star_wishes)} $sw")

            if star_wishes:
                colored_stars = [colored(name, ANSIColors.YELLOW) for name in star_wishes]
                wishlist_lines.append(f"Star Wishes ({len(star_wishes)}): {', '.join(colored_stars)}")
            else:
                wishlist_lines.append("Star Wishes: 0")

            wishlist_lines.append(f"Regular Wishes: {len(regular_wishes)}")
            for idx, name in enumerate(regular_wishes, 1):
                wishlist_lines.append(f"{idx}. {colored(name, ANSIColors.YELLOW)}")

        lines.extend(_dashboard_section("WISHLIST STATUS", wishlist_lines, width))
        lines.append("")

        rolls = _dashboard_state.get('rolls', [])
        roll_lines: List[str] = []
        if not rolls:
            roll_lines.append("No rolls yet")
        else:
            rolls_target = _dashboard_state.get('rolls_target')
            display_limit = rolls_target if isinstance(rolls_target, int) and rolls_target > 0 else None
            display_rolls = rolls[-display_limit:] if display_limit else rolls
            for idx, roll in enumerate(display_rolls, 1):
                markers = ""
                if roll.get('kakera_react'):
                    markers += "💎"
                if roll.get('wishlist'):
                    markers += "⭐"
                if roll.get('candidate'):
                    markers += "❤️"
                name = roll.get('name', 'Unknown')
                series = roll.get('series', '')
                kakera_val = roll.get('kakera', '?')
                keys = roll.get('keys', [])
                key_str = " ".join(colored(f"K:{count}", ANSIColors.GREEN) for count in keys) if keys else ""
                name_color = ANSIColors.MAGENTA if roll.get('candidate') else ANSIColors.YELLOW
                display_name = colored(f"{name} {markers}".rstrip(), name_color)
                kakera_display = colored(f"P:{kakera_val}", ANSIColors.CYAN)
                if key_str:
                    roll_lines.append(f"{idx:2d}. {display_name} | {series} | {key_str} {kakera_display}")
                else:
                    roll_lines.append(f"{idx:2d}. {display_name} | {series} | {kakera_display}")

        lines.extend(_dashboard_section("LATEST ROLLS", roll_lines, width))
        lines.append("")

        candidate = _dashboard_state.get('best_candidate')
        candidate_lines: List[str] = []
        if not candidate:
            candidate_lines.append("None")
        else:
            candidate_lines.append(f"Character: {colored(candidate.get('name', 'Unknown'), ANSIColors.YELLOW)}")
            candidate_lines.append(f"Series: {candidate.get('series', '')}")
            candidate_lines.append(f"Kakera: {colored(str(candidate.get('kakera', '?')), ANSIColors.CYAN)}")
            if candidate.get('image_url'):
                candidate_lines.append(f"Image: {candidate.get('image_url')}")
            if candidate.get('status'):
                candidate_lines.append(f"Status: {candidate.get('status')}")

        lines.extend(_dashboard_section("BEST CANDIDATE LATEST SESSION", candidate_lines, width))
        lines.append("")

        others_rolls = _dashboard_state.get('others_rolls', [])
        others_lines: List[str] = []
        if not others_rolls:
            others_lines.append("No other rolls yet")
        else:
            display_others = others_rolls[-20:]
            for idx, roll in enumerate(display_others, 1):
                roller = roll.get('roller', 'unknown')
                name = roll.get('name', 'Unknown')
                series = roll.get('series', '')
                kakera_val = roll.get('kakera', '?')
                flags: List[str] = []
                if roll.get('wishlist'):
                    flags.append("⭐")
                if roll.get('claimed'):
                    flags.append("C")
                if roll.get('kakera_button'):
                    flags.append("💎")
                flag_str = f" ({' '.join(flags)})" if flags else ""
                colored_name = colored(name, ANSIColors.YELLOW)
                kakera_display = colored(f"P:{kakera_val}", ANSIColors.CYAN)
                others_lines.append(f"{idx:2d}. {roller} | {colored_name} | {series} | {kakera_display}{flag_str}")

        lines.extend(_dashboard_section("OTHERS' ROLLS (20 Most Recent)", others_lines, width))
        lines.append("")

        summary = _dashboard_state.get('summary', {})
        summary_lines: List[str] = []
        if not summary:
            summary_lines.append("Summary not available")
        else:
            if 'rolls_total' in summary:
                summary_lines.append(f"Rolls Made (Since Start): {summary['rolls_total']}")
            if 'claims_total' in summary:
                summary_lines.append(f"Claims (Since Start): {summary['claims_total']}")
            if 'claims_latest' in summary:
                summary_lines.append(f"Latest Claim: {summary['claims_latest']}")
            if 'kakera_total' in summary:
                summary_lines.append(f"Kakera Earned (Since Start): +{summary['kakera_total']}")
            if 'reaction_kakera_total' in summary:
                summary_lines.append(f"Bonus Kakera From Reactions: +{summary['reaction_kakera_total']}")
            if 'total_balance' in summary:
                summary_lines.append(f"Total Balance (From /tu): {summary['total_balance']} kakera")

            if _dashboard_state.get('predicted_at'):
                summary_lines.append(f"Next predicted session: {_dashboard_state['predicted_at']}")
            if _dashboard_state.get('predicted_status'):
                summary_lines.append("Predicted status:")
                predicted_status = _dashboard_sanitize_text(_dashboard_state['predicted_status'])
                summary_lines.extend(_dashboard_wrap_lines(predicted_status, inner_width))

        lines.extend(_dashboard_section("SUMMARY", summary_lines, width))
        lines.append("")

        # Ouro status section
        ouro_lines: List[str] = []
        oh_left = active_status.get('oh_left') if isinstance(active_status, dict) else None
        oc_left = active_status.get('oc_left') if isinstance(active_status, dict) else None
        oq_left = active_status.get('oq_left') if isinstance(active_status, dict) else None
        oh_stored = active_status.get('oh_stored') if isinstance(active_status, dict) else None
        oc_stored = active_status.get('oc_stored') if isinstance(active_status, dict) else None
        oq_stored = active_status.get('oq_stored') if isinstance(active_status, dict) else None
        sphere_balance = active_status.get('sphere_balance') if isinstance(active_status, dict) else None
        ouro_refill_min = active_status.get('ouro_refill_min') if isinstance(active_status, dict) else None

        def _fmt_val(value: Any) -> str:
            return "Unknown" if value is None else str(value)

        def _fmt_ouro(base_val: Any, stored_val: Any) -> str:
            if base_val is None:
                return "Unknown"
            try:
                base_num = int(base_val)
            except (TypeError, ValueError):
                return _fmt_val(base_val)
            stored_num = 0
            try:
                if stored_val is not None:
                    stored_num = int(stored_val)
            except (TypeError, ValueError):
                stored_num = 0
            if stored_num > 0:
                return f"{base_num} (+{stored_num} stored)"
            return str(base_num)

        ouro_lines.append(f"$oh left: {_fmt_ouro(oh_left, oh_stored)}")
        ouro_lines.append(f"$oc left: {_fmt_ouro(oc_left, oc_stored)}")
        ouro_lines.append(f"$oq left: {_fmt_ouro(oq_left, oq_stored)}")

        if ouro_refill_min is None:
            refill_str = "Unknown"
        else:
            if use_countdown:
                elapsed = max(0, int(countdown_total) - int(countdown_remaining))
                refill_sec = max(0, int(ouro_refill_min) * 60 - elapsed)
                refill_str = formatTimeHrsMinSec(refill_sec)
            else:
                refill_str = formatTimeHrsMin(int(ouro_refill_min))
        ouro_lines.append(f"Refill: {refill_str}")
        ouro_lines.append(f"Ourospheres: {_fmt_val(sphere_balance)}")

        lines.extend(_dashboard_section("OURO STATUS", ouro_lines, width))
        lines = _dashboard_fit_height(lines, width)

        renderer_mode = _dashboard_resolve_renderer_mode(force_clear)
        rendered = False

        if renderer_mode == 'status_line':
            _dashboard_emit_status_line(force=True)
            return

        if renderer_mode == 'ansi_full':
            rendered = _render_dashboard_ansi_full(lines)
            if not rendered:
                _dashboard_state['renderer_mode'] = 'legacy_clear'
                rendered = _render_dashboard_legacy_clear(lines)
        elif renderer_mode == 'win32':
            rendered = _render_dashboard_win32(lines, width)
            if not rendered:
                fallback = 'ansi_full' if _enable_ansi_if_needed() else 'legacy_clear'
                _dashboard_state['renderer_mode'] = fallback
                if fallback == 'ansi_full':
                    rendered = _render_dashboard_ansi_full(lines)
                if not rendered:
                    rendered = _render_dashboard_legacy_clear(lines)
        elif renderer_mode == 'legacy_clear':
            rendered = _render_dashboard_legacy_clear(lines)
        else:
            # Defensive fallback for invalid mode values.
            _dashboard_state['renderer_mode'] = 'legacy_clear'
            rendered = _render_dashboard_legacy_clear(lines)

        if not rendered:
            _dashboard_state['renderer_mode'] = 'legacy_clear'
            _render_dashboard_legacy_clear(lines)

        _dashboard_state['last_render_lines'] = len(lines)
        _dashboard_state['last_render_width'] = width
        return

def calculatePowerStats(current_power: int, kakera_cost: int, dk_ready: bool, minutes_to_wait: int = 0, max_power: int = 100) -> Tuple[str, int]:
    """Calculate power stats for status display. Returns (formatted_str, time_until_ready)
    
    Power regenerates at 1% every 3 minutes.
    $dk resets power to max power (not a cost reduction).
    Returns formatted power string with emoji and reaction capability info.
    """
    cost = kakera_cost
    
    # Calculate how many normal reacts are possible with current power
    normal_reacts = current_power // cost
    
    # Calculate reacts with $dk available: after $dk, power is max power again
    # So: current reacts + (max_power / cost) reacts after dk reset
    dk_bonus_reacts = max_power // cost if dk_ready else 0
    total_reacts_with_dk = normal_reacts + dk_bonus_reacts
    
    # Determine current state
    if current_power >= cost * 2:  # Can react at least 2x
        if dk_ready:
            status_str = f"Power: {current_power}% ✅ {normal_reacts}x React, {total_reacts_with_dk}x total with $dk"
        else:
            status_str = f"Power: {current_power}% ✅ {normal_reacts}x React"
        time_until_change = (cost * 2 - current_power) * 3  # Time until power drops to cost*2
        return (status_str, time_until_change)
    
    elif current_power >= cost:  # Can react at least 1x
        if dk_ready:
            status_str = f"Power: {current_power}% ✅ {normal_reacts}x React, {total_reacts_with_dk}x total with $dk"
        else:
            status_str = f"Power: {current_power}% ✅ {normal_reacts}x React"
        time_until_change = (cost - current_power) * 3 if current_power < cost else (cost * 2 - current_power) * 3
        return (status_str, time_until_change)
    
    else:  # Cannot react right now
        time_to_react = (cost - current_power) * 3  # Time until can react again (in minutes)
        if dk_ready:
            # After waiting, user can use $dk to reset to max power and get more reacts
            dk_reacts_after_wait = max_power // cost
            status_str = f"Power: {current_power}% ❌ {formatTimeHrsMin(time_to_react)} until Cost ({cost}%), then {dk_reacts_after_wait}x with $dk"
        else:
            status_str = f"Power: {current_power}% ❌ {formatTimeHrsMin(time_to_react)} until Cost ({cost}%)"
        return (status_str, time_to_react)

def formatDetailedStatus(tu_info: Dict[str, Any], is_prediction: bool = False, predicted_power: Optional[int] = None) -> str:
    """Format comprehensive status with hours/minutes and detailed power analysis
    
    Format: \"0 rolls ❌ 40min | Claim: ❌ 2hrs 40min | Power: 30% ❌ 30min until Cost (40%), 2x with $dk | Daily: ❌ 13hrs 50min | $rt: ✅ | $dk: ✅\"
    """
    if not tu_info:
        return "Status Unknown"
    
    rolls_left = tu_info.get('rolls', 0)
    rolls_reset_min = tu_info.get('next_reset_min', 0)
    claim_reset_min = tu_info.get('claim_reset_min', 0)
    rolls_reset_sec = tu_info.get('next_reset_sec')
    claim_reset_sec = tu_info.get('claim_reset_sec')
    can_claim_now = tu_info.get('can_claim_now', False)
    daily_available = tu_info.get('daily_available', False)
    rt_available = tu_info.get('rt_available', False)
    dk_ready = tu_info.get('dk_ready', False)
    current_power = predicted_power if is_prediction and predicted_power is not None else tu_info.get('current_power', 0)
    max_power = tu_info.get('max_power', 100)
    kakera_cost = tu_info.get('kakera_cost', 40)
    
    # Format rolls
    if rolls_left > 0:
        rolls_str = f"{rolls_left} rolls ✅"
    else:
        if isinstance(rolls_reset_sec, int):
            rolls_str = f"0 rolls ❌ {formatTimeHrsMinSec(rolls_reset_sec)}"
        else:
            rolls_str = f"0 rolls ❌ {formatTimeHrsMin(rolls_reset_min)}"

    # Format claim - show time until next reset regardless of status
    if can_claim_now:
        if isinstance(claim_reset_sec, int):
            claim_str = f"Claim: ✅ {formatTimeHrsMinSec(claim_reset_sec)} until next Refresh"
        else:
            claim_str = f"Claim: ✅ {formatTimeHrsMin(claim_reset_min)} until next Refresh"
    else:
        if isinstance(claim_reset_sec, int):
            claim_str = f"Claim: ❌ {formatTimeHrsMinSec(claim_reset_sec)} until available"
        else:
            claim_str = f"Claim: ❌ {formatTimeHrsMin(claim_reset_min)} until available"

    # Format power with detailed analysis
    power_str, _ = calculatePowerStats(current_power, kakera_cost, dk_ready, max_power=max_power)
    
    # Format daily
    if daily_available:
        daily_str = "Daily: ✅"
    else:
        daily_reset_min = tu_info.get('daily_reset_min', 0)
        daily_str = f"Daily: ❌ {formatTimeHrsMin(daily_reset_min)}"
    
    # Format $rt
    if rt_available:
        rt_str = "$rt: ✅"
    else:
        rt_reset_min = tu_info.get('rt_reset_min', 0)
        rt_str = f"$rt: ❌ {formatTimeHrsMin(rt_reset_min)}"
    
    # Format $dk
    if dk_ready:
        dk_str = "$dk: ✅"
    else:
        dk_reset_min = tu_info.get('dk_reset_min', 0)
        dk_str = f"$dk: ❌ {formatTimeHrsMin(dk_reset_min)}"
    
    return f"{rolls_str} | {claim_str} | {power_str} | {daily_str} | {rt_str} | {dk_str}"

def getClientAndAuth(token: str) -> Tuple[Any, Dict[str, str]]:
    """Create bot client and auth headers for a given token"""
    auth = {'authorization': token}
    bot = discum.Client(token=token, log=False)
    return bot, auth

def getUrl():
    """Get the message URL"""
    return f'{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{Vars.channelId}/messages'

def logRawResponse(label: str, response_data: Any) -> None:
    """Log raw Discord API responses to Rawresponse.json file (persistent)."""
    try:
        rawresponse_file = os.fspath(LOGS_DIR / 'Rawresponse.json')
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry: Dict[str, Any] = {
            'ts': timestamp,
            'label': label,
            'source': 'Function.logRawResponse'
        }
        if isinstance(response_data, requests.Response):
            entry['type'] = 'http'
            entry['status_code'] = response_data.status_code
            entry['headers'] = dict(response_data.headers)
            entry['content_type'] = response_data.headers.get('Content-Type', 'N/A')
            entry['body_text'] = response_data.text
            try:
                entry['body_json'] = response_data.json()
                entry['parse_error'] = None
            except Exception as exc:
                entry['body_json'] = None
                entry['parse_error'] = str(exc)
        else:
            entry['type'] = 'data'
            entry['body_text'] = str(response_data)
            entry['body_json'] = None
            entry['parse_error'] = None
        append_json_array(rawresponse_file, entry, lock=_rawlog_lock)
    except Exception as e:
        log(f"Error logging response: {e}")

def logSessionRawResponse(label: str, response_data: Any) -> None:
    """Log raw Discord API response data to SessionRawresponse.json (per session)."""
    try:
        session_rawresponse_file = getSessionRawResponseFile()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry: Dict[str, Any] = {
            'ts': timestamp,
            'label': label,
            'source': 'Function.logSessionRawResponse'
        }
        if isinstance(response_data, requests.Response):
            entry['type'] = 'http'
            entry['status_code'] = response_data.status_code
            entry['headers'] = dict(response_data.headers)
            entry['content_type'] = response_data.headers.get('Content-Type', 'N/A')
            entry['body_text'] = response_data.text
            try:
                entry['body_json'] = response_data.json()
                entry['parse_error'] = None
            except Exception as exc:
                entry['body_json'] = None
                entry['parse_error'] = str(exc)
        else:
            entry['type'] = 'data'
            entry['body_text'] = str(response_data)
            entry['body_json'] = None
            entry['parse_error'] = None
        append_json_array(session_rawresponse_file, entry, lock=_rawlog_lock)
    except Exception as e:
        log(f"Error logging session response: {e}")

def initializeSession(token: str, expected_username: str = "") -> None:
    """Initialize logging and send initial /tu command"""
    try:
        Latency.configure_from_vars()
        if _should_stop():
            return
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        user_id = resolved_id
        user_name = expected_username or resolved_name
        log(f"Debug identity (pre-/tu): name={user_name or 'unknown'} id={user_id or 'unknown'}")
        
        rawresponse_file = os.fspath(LOGS_DIR / 'Rawresponse.json')
        ensure_json_array_file(rawresponse_file)
        
        # Clear SessionRawresponse.json and start fresh for this session ONLY
        session_start_epoch = time.time()
        _session_start_epoch[token] = session_start_epoch
        session_start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session_start_epoch))
        _dashboard_reset_session(session_start_time)
        processed_external_roll_ids.clear()
        _processed_external_roll_queue.clear()
        external_after_ids.pop(token, None)
        initial_tu_cache.pop(token, None)
        _processed_manual_claim_ids[token] = set()
        _load_last_seen_cache()
        seeded_id = _get_last_seen(Vars.channelId)
        if seeded_id:
            external_after_ids[token] = seeded_id
        session_rawresponse_file = _build_session_artifact_path(
            "SessionRawresponse",
            ".json",
            user_name=user_name or resolved_name or expected_username,
            session_epoch=session_start_epoch,
        )
        setSessionRawResponseFile(session_rawresponse_file)
        ensure_json_array_file(session_rawresponse_file)
        append_json_array(session_rawresponse_file, {
            'ts': session_start_time,
            'ts_epoch': session_start_epoch,
            'type': 'session_start',
            'user': user_name or resolved_name or expected_username or '',
            'channel_id': Vars.channelId,
            'guild_id': Vars.serverId,
            'source': 'Function.initializeSession'
        }, lock=_rawlog_lock)

        log("🔍 Initializing session...")
        tuCommand = getSlashCommand(bot, ['tu'])
        if not tuCommand:
            log("Warning: /tu command not available")
            return
        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        _note_last_seen_from_messages(pre_messages)
        before_id = Fetch.get_latest_message_id(pre_messages)
        command_sent_at = time.perf_counter()
        trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=tuCommand)
        _emit_latency_event(
            "command_sent",
            flow="core",
            action="tu",
        )
        interaction_id = _record_interaction_context(token, 'tu', user_id, trigger_result)
        legacy_tier = Latency.get_active_tier() == "legacy"
        if legacy_tier and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
            return
        
        # Get the response to extract our user ID
        r, jsonResponse, our_message = Fetch.wait_for_interaction_message(
            url,
            auth,
            after_id=before_id,
            user_id=user_id,
            user_name=user_name,
            command_name='tu',
            interaction_id=interaction_id,
            attempts=4,
            delay_sec=Vars.SLEEP_MED_SEC,
            latency_context="core_tu_init",
            stop_check=_should_stop
        )
        if r is not None:
            logRawResponse("GET - Initial /tu command response", r)
            logSessionRawResponse("GET - Initial /tu command response", r)
        if jsonResponse:
            _note_last_seen_from_messages(jsonResponse)

        if our_message:
            _mark_last_seen(Vars.channelId, cast(Optional[str], our_message.get('id')))
            _emit_latency_event(
                "message_detected",
                flow="core",
                action="tu",
                detect_lag_ms=_latency_message_lag_ms(our_message),
                response_ms=int((time.perf_counter() - command_sent_at) * 1000.0),
            )
            resolved_user_id = Fetch.extract_interaction_user_id(our_message)
            resolved_user_name = Fetch.extract_interaction_user_name(our_message)
            if resolved_user_id or resolved_user_name:
                _set_user_identity_for_token(token, resolved_user_id, resolved_user_name)
                log(f"Session initialized (User ID: {resolved_user_id or 'unknown'}, User Name: {resolved_user_name or user_name or 'unknown'})")
                _cache_initial_tu(
                    token,
                    cast(str, our_message.get('content', '')),
                    cast(Optional[str], our_message.get('timestamp'))
                )
                cached_status = _parse_tu_message(cast(str, our_message.get('content', '')))
                if cached_status:
                    cached_status['max_power'] = getMaxPowerForToken(token)
                    _cache_tu_info(token, cached_status)
                    _run_auto_give_from_tu(token=token, status=cached_status, url=url, auth=auth)
                render_dashboard()
                return

        # Extract and store our user ID from the response
        # Match by username if provided, otherwise get first valid response
        for msg in jsonResponse:
            try:
                if not isinstance(msg, dict):
                    continue
                msg_content = msg.get('content', '')
                # Message starts with "**username**, you..."
                username_match = re.match(r'\*\*([^*]+)\*\*, you', msg_content)
                if username_match:
                    msg_username = username_match.group(1)
                    
                    # If expected_username provided, match exactly
                    if expected_username:
                        if msg_username.lower() == expected_username.lower():
                            user_id = Fetch.extract_interaction_user_id(msg)
                            user_name_from_msg = Fetch.extract_interaction_user_name(msg)
                            if user_id or user_name_from_msg:
                                _set_user_identity_for_token(token, user_id, user_name_from_msg or msg_username)
                                log(f"Session initialized (User ID: {user_id or 'unknown'}, User Name: {user_name_from_msg or msg_username or 'unknown'})")
                                _cache_initial_tu(
                                    token,
                                    cast(str, msg.get('content', '')),
                                    cast(Optional[str], msg.get('timestamp'))
                                )
                                cached_status = _parse_tu_message(cast(str, msg.get('content', '')))
                                if cached_status:
                                    cached_status['max_power'] = getMaxPowerForToken(token)
                                    _cache_tu_info(token, cached_status)
                                    _run_auto_give_from_tu(token=token, status=cached_status, url=url, auth=auth)
                                _mark_last_seen(Vars.channelId, cast(Optional[str], msg.get('id')))
                                render_dashboard()
                                return
                    else:
                        # No expected username, use first response
                        user_id = Fetch.extract_interaction_user_id(msg)
                        user_name_from_msg = Fetch.extract_interaction_user_name(msg)
                        if user_id or user_name_from_msg:
                            _set_user_identity_for_token(token, user_id, user_name_from_msg or msg_username)
                            log(f"Session initialized (User ID: {user_id or 'unknown'}, User Name: {user_name_from_msg or msg_username or 'unknown'})")
                            _cache_initial_tu(
                                token,
                                cast(str, msg.get('content', '')),
                                cast(Optional[str], msg.get('timestamp'))
                            )
                            cached_status = _parse_tu_message(cast(str, msg.get('content', '')))
                            if cached_status:
                                cached_status['max_power'] = getMaxPowerForToken(token)
                                _cache_tu_info(token, cached_status)
                                _run_auto_give_from_tu(token=token, status=cached_status, url=url, auth=auth)
                            _mark_last_seen(Vars.channelId, cast(Optional[str], msg.get('id')))
                            render_dashboard()
                            return
            except (KeyError, TypeError, AttributeError):
                continue
        
        log("✅ Session initialized")
        render_dashboard()
    except Exception as e:
        log(f"❌ Error initializing session: {e}")
        render_dashboard()

def predictStatusAfterCountdown(tu_info: Dict[str, Any], minutes_to_wait: int) -> str:
    """Predict the status after waiting X minutes with proper time calculations"""
    if not tu_info:
        return "Status Unknown"
    
    # Copy tu_info for modification
    predicted_info = tu_info.copy()
    
    # Calculate predicted rolls
    current_rolls = tu_info.get('rolls', 0)
    rolls_reset_min = tu_info.get('next_reset_min', 0)
    if minutes_to_wait >= rolls_reset_min and rolls_reset_min > 0:
        predicted_info['rolls'] = Vars.ROLLS_PER_RESET
    else:
        predicted_info['rolls'] = current_rolls
    
    # Calculate predicted claim reset time (subtract minutes_to_wait)
    claim_reset_min = tu_info.get('claim_reset_min', 0)
    rolls_reset_sec = tu_info.get('next_reset_sec')
    claim_reset_sec = tu_info.get('claim_reset_sec')
    remaining_claim_min = max(0, claim_reset_min - minutes_to_wait)
    predicted_info['claim_reset_min'] = remaining_claim_min
    predicted_info['can_claim_now'] = (remaining_claim_min == 0)
    
    # Calculate predicted daily reset time (subtract minutes_to_wait)
    daily_reset_min = tu_info.get('daily_reset_min', 0)  # Extract daily reset time
    remaining_daily_min = max(0, daily_reset_min - minutes_to_wait)
    predicted_info['daily_reset_min'] = remaining_daily_min
    predicted_info['daily_available'] = (remaining_daily_min == 0)
    
    # Calculate predicted power (regenerates 1% every 3 minutes)
    current_power = tu_info.get('current_power', 0)
    max_power = tu_info.get('max_power', 100)
    power_regen = int(minutes_to_wait / 3)  # 1% per 3 minutes
    predicted_power = min(max_power, current_power + power_regen)
    predicted_info['current_power'] = predicted_power
    
    # Calculate predicted $rt cooldown time (subtract minutes_to_wait)
    rt_available = tu_info.get('rt_available', False)
    rt_reset_min = tu_info.get('rt_reset_min', 0)
    if not rt_available and rt_reset_min > 0:
        remaining_rt_min = max(0, rt_reset_min - minutes_to_wait)
        predicted_info['rt_reset_min'] = remaining_rt_min
        predicted_info['rt_available'] = (remaining_rt_min == 0)
    else:
        predicted_info['rt_available'] = True
        predicted_info['rt_reset_min'] = 0
    
    # Calculate predicted $dk cooldown time (subtract minutes_to_wait)
    dk_ready = tu_info.get('dk_ready', False)
    dk_reset_min = tu_info.get('dk_reset_min', 0)
    if not dk_ready and dk_reset_min > 0:
        remaining_dk_min = max(0, dk_reset_min - minutes_to_wait)
        predicted_info['dk_reset_min'] = remaining_dk_min
        predicted_info['dk_ready'] = (remaining_dk_min == 0)
    else:
        predicted_info['dk_ready'] = True
        predicted_info['dk_reset_min'] = 0
    
    # Use the new format function for predictions
    predicted_status = formatDetailedStatus(predicted_info, is_prediction=True, predicted_power=predicted_power)
    _dashboard_set_predicted(predicted_status, minutes_to_wait)
    render_dashboard()
    return predicted_status

def getTuInfo(token: str) -> Optional[Dict[str, Any]]:
    """Send /tu command and extract comprehensive status information"""
    try:
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        user_id = resolved_id
        user_name = resolved_name
        
        tuCommand = getSlashCommand(bot, ['tu'])
        if not tuCommand:
            log("Warning: /tu command not available")
            return
        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        _note_last_seen_from_messages(pre_messages)
        before_id = Fetch.get_latest_message_id(pre_messages)
        trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=tuCommand)
        interaction_id = _record_interaction_context(token, 'tu', user_id, trigger_result)
        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
            return

        r, jsonResponse, our_message = Fetch.wait_for_interaction_message(
            url,
            auth,
            after_id=before_id,
            user_id=user_id,
            user_name=user_name if not user_id else None,
            command_name='tu',
            interaction_id=interaction_id,
            attempts=4,
            delay_sec=Vars.SLEEP_MED_SEC,
            latency_context="core_tu_status",
            stop_check=_should_stop
        )
        if r is not None:
            logRawResponse("GET - /tu command response", r)
            logSessionRawResponse("GET - /tu command response", r)
        if jsonResponse:
            _note_last_seen_from_messages(jsonResponse)

        if not jsonResponse:
            return None

        match_reason = "prefetched" if our_message else "none"
        selected_message, selected_reason = _select_tu_response_message(
            jsonResponse,
            token=token,
            user_id=user_id,
            user_name=user_name,
            interaction_id=interaction_id,
        )
        if selected_message is not None:
            our_message = selected_message
            match_reason = selected_reason

        if not our_message:
            log("Warning: No response from our user in /tu (responses received but none matched our user ID)")
            return None
        if match_reason not in {"none", "exact_interaction_user"}:
            log(f"Info: Matched /tu response via {match_reason}")

        _mark_last_seen(Vars.channelId, cast(Optional[str], our_message.get('id')))
        
        resolved_user_id = Fetch.extract_interaction_user_id(our_message)
        resolved_user_name = Fetch.extract_interaction_user_name(our_message)
        if resolved_user_id or resolved_user_name:
            _set_user_identity_for_token(token, resolved_user_id or user_id, resolved_user_name)
            if resolved_user_id:
                user_id = resolved_user_id
        
        message: str = cast(str, our_message.get('content', ''))
        status = _parse_tu_message(message)
        if not status:
            return None
        status['max_power'] = getMaxPowerForToken(token)
        parsed_timestamp = _parse_discord_timestamp(cast(Optional[str], our_message.get('timestamp')))
        if parsed_timestamp:
            status['tu_timestamp'] = parsed_timestamp

        if not status.get('maintenance', False):
            roll_sec, claim_sec = calculateFixedResetSeconds()
            status['next_reset_sec'] = roll_sec
            status['claim_reset_sec'] = claim_sec
            status['next_reset_min'] = int(math.ceil(roll_sec / 60)) if roll_sec > 0 else 0
            status['claim_reset_min'] = int(math.ceil(claim_sec / 60)) if claim_sec > 0 else 0

        # Log status with new detailed format
        detailed_status = formatDetailedStatus(status)
        log(f"📊 Status: {detailed_status}")
        _dashboard_set_status(status)
        render_dashboard()
        _cache_tu_info(token, status)
        _run_auto_give_from_tu(token=token, status=status, url=url, auth=auth)
        
        return status
    except Exception as e:
        log(f"❌ Error getting /tu info: {e}")

def useSpecialCommand(token: str, command_name: str) -> bool:
    """Use a special command (/daily, /rolls, /dk, /rollsutil resetclaimtimer)"""
    try:
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        user_id = resolved_id
        user_name = resolved_name

        interaction_name = command_name.split()[0]
        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        _note_last_seen_from_messages(pre_messages)
        before_id = Fetch.get_latest_message_id(pre_messages)

        # Handle different command formats
        if command_name == 'rollsutil resetclaimtimer':
            # This is a slash command: /rollsutil resetclaimtimer
            cmd = getSlashCommand(bot, ['rollsutil', 'resetclaimtimer'])
        else:
            # Other commands: /daily, /rolls, /dk
            cmd = getSlashCommand(bot, [command_name])
            if not cmd:
                log(f"Warning: /{command_name} command not available")
                return False

        jsonResponse: List[Dict[str, Any]] = []
        our_response: Optional[Dict[str, Any]] = None
        r: Optional[requests.Response] = None
        interaction_id: Optional[str] = None

        if not cmd:
            # Fallback for rollsutil resetclaimtimer: send $rt message instead
            if command_name == 'rollsutil resetclaimtimer':
                log(f"   Warning: Failed to get /rollsutil resetclaimtimer command, trying $rt message...")
                trigger_result = bot.sendMessage(Vars.channelId, '$rt')
                _record_interaction_context(token, '$rt', user_id, trigger_result)
                if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
                    return False
                content_hint = "$rt"
                r, jsonResponse, our_response = Fetch.wait_for_author_message(
                    url,
                    auth,
                    botID,
                    after_id=before_id,
                    attempts=4,
                    delay_sec=Vars.SLEEP_MED_SEC,
                    latency_context="core_special_rt_message",
                    content_contains=content_hint,
                    stop_check=_should_stop
                )
                if r is not None:
                    logRawResponse(f"GET - $rt message response", r)
                    logSessionRawResponse(f"GET - $rt message response", r)
                if jsonResponse:
                    _note_last_seen_from_messages(jsonResponse)
            else:
                log(f"Error using /{command_name}: command not found")
                return False
        else:
            trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=cmd)
            interaction_id = _record_interaction_context(token, interaction_name, user_id, trigger_result)
            if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
                return False

            r, jsonResponse, our_response = Fetch.wait_for_interaction_message(
                url,
                auth,
                after_id=before_id,
                user_id=user_id,
                user_name=user_name if not user_id else None,
                command_name=interaction_name,
                interaction_id=interaction_id,
                attempts=4,
                delay_sec=Vars.SLEEP_MED_SEC,
                latency_context=f"core_special_{interaction_name}",
                stop_check=_should_stop
            )
            if r is not None:
                logRawResponse(f"GET - /{command_name} response", r)
                logSessionRawResponse(f"GET - /{command_name} response", r)
            if jsonResponse:
                _note_last_seen_from_messages(jsonResponse)

        if not our_response and jsonResponse:
            candidates = _filter_messages_with_interaction(
                jsonResponse,
                user_id=user_id,
                user_name=user_name if not user_id else None,
                command_name=interaction_name,
                interaction_id=interaction_id,
                require_interaction=True
            )
            if candidates:
                our_response = candidates[0]

        if not our_response:
            log(f"Warning: No response from our user for /{command_name}")
            return False
        _mark_last_seen(Vars.channelId, cast(Optional[str], our_response.get('id')))

        resolved_user_id = Fetch.extract_interaction_user_id(our_response)
        resolved_user_name = Fetch.extract_interaction_user_name(our_response)
        if resolved_user_id or resolved_user_name:
            _set_user_identity_for_token(token, resolved_user_id or user_id, resolved_user_name)
            if resolved_user_id:
                user_id = resolved_user_id

        response_content: str = cast(str, our_response.get('content', ''))

        # Check for /rolls cooldown: "the roulette is limited to 14 uses per hour. **X** min left"
        if command_name == 'rolls' and ('Upvote Mudae' in response_content or 'one vote per' in response_content):
            log("/rolls requires an upvote to reset rolls (no reset applied)")
            return False
        if command_name == 'rolls' and 'the roulette is limited' in response_content:
            cooldown_match = re.search(r'limited to \d+ uses per hour\. \*\*(\d+)\*\* min', response_content)
            if cooldown_match:
                cooldown_min = cooldown_match.group(1)
                log(f"/rolls on cooldown: {cooldown_min} minutes remaining")
                return False

        if r is not None and r.status_code == 200:
            log(f"Used /{command_name}")
            return True
        if r is not None:
            log(f"Failed to use /{command_name}: {r.status_code}")
        else:
            log(f"Failed to use /{command_name}: no response")
        return False
    except Exception as e:
        log(f"Error using /{command_name}: {e}")
        return False

def isSessionEligible(tu_info: Dict[str, Any]) -> bool:
    """Check if a rolling session can proceed based on /tu status
    
    Conditions:
    - (rolls_left > 0 OR can_use_/rolls) AND
    - ((can_claim_now OR rt_ready) OR (current_power >= cost OR dk_ready))
    """
    # Path to rolls: either have rolls or a reset is already available
    rolls_left = tu_info.get('rolls', 0)
    rolls_reset_min = tu_info.get('next_reset_min')
    rolls_reset_sec = tu_info.get('next_reset_sec')
    has_path_to_rolls = rolls_left > 0
    if rolls_left == 0:
        if isinstance(rolls_reset_sec, int) and rolls_reset_sec == 0:
            has_path_to_rolls = True
        elif isinstance(rolls_reset_min, int) and rolls_reset_min == 0:
            has_path_to_rolls = True
    
    # Path to claim: either can claim now or $rt is ready
    has_path_to_claim = tu_info.get('can_claim_now', False) or tu_info.get('rt_available', False)
    
    # Path to react: either have enough power or /dk is ready
    has_path_to_react = (tu_info.get('current_power', 0) >= tu_info.get('kakera_cost', 40)) or tu_info.get('dk_ready', False)
    
    # Session is eligible if: (has rolls) AND (can claim OR can react)
    eligible = has_path_to_rolls and (has_path_to_claim or has_path_to_react)
    
    if eligible:
        log("✅ Session eligible to proceed")
    else:
        reasons = []
        if not has_path_to_rolls:
            reasons.append("no rolls available")
        if not has_path_to_claim:
            reasons.append("can't claim (need $rt or claim CD reset)")
        if not has_path_to_react:
            reasons.append("can't react (need $dk or power)")
        log(f"❌ Session ineligible: {', '.join(reasons)}")
    
    return eligible

def sendReport(token: str) -> Optional[list]:  # type: ignore[type-arg]
    """Send /report to get character claim and kakera updates"""
    try:
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        user_id = resolved_id
        user_name = resolved_name
        interaction_id: Optional[str] = None

        try:
            reportCommand = getSlashCommand(bot, ['report'])
            if not reportCommand:
                log("Warning: /report command not available")
                return None
            _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
            _note_last_seen_from_messages(pre_messages)
            before_id = Fetch.get_latest_message_id(pre_messages)
            trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=reportCommand)
            interaction_id = _record_interaction_context(token, 'report', user_id, trigger_result)
            if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
                return None
        except Exception:
            log("Warning: /report command not available")
            return None

        r, jsonResponse, our_response = Fetch.wait_for_interaction_message(
            url,
            auth,
            after_id=before_id,
            user_id=user_id,
            user_name=user_name if not user_id else None,
            command_name='report',
            interaction_id=interaction_id,
            attempts=4,
            delay_sec=Vars.SLEEP_LONG_SEC,
            latency_context="core_report"
        )
        if r is not None:
            logRawResponse("GET - /report response", r)
            logSessionRawResponse("GET - /report response", r)
        if jsonResponse:
            _note_last_seen_from_messages(jsonResponse)

        if not jsonResponse:
            return None

        if not our_response:
            candidates = _filter_messages_with_interaction(
                jsonResponse,
                user_id=user_id,
                user_name=user_name if not user_id else None,
                command_name='report',
                interaction_id=interaction_id,
                require_interaction=True
            )
            if candidates:
                our_response = candidates[0]

        if not our_response:
            log("Warning: No response from our user for /report")
            return None
        _mark_last_seen(Vars.channelId, cast(Optional[str], our_response.get('id')))

        resolved_user_id = Fetch.extract_interaction_user_id(our_response)
        resolved_user_name = Fetch.extract_interaction_user_name(our_response)
        if resolved_user_id or resolved_user_name:
            _set_user_identity_for_token(token, resolved_user_id or user_id, resolved_user_name)
            if resolved_user_id:
                user_id = resolved_user_id
            if resolved_user_name:
                user_name = resolved_user_name

        return jsonResponse
    except Exception as e:
        log(f"Error sending /report: {e}")
        return None

def matchesWishlist(cardName: str, cardSeries: str, mudae_star_wishes: Optional[List[str]] = None, mudae_regular_wishes: Optional[List[str]] = None) -> Tuple[bool, int]:
    """
    Check if card matches wishlist. Returns (is_match, priority_level)
    
    Priority levels:
    - 3: Mudae star wish (⭐) - highest
    - 2: Mudae regular wish or Vars.py wishlist
    - 1: Not in wishlist
    """
    def wish_matches(target: str, wish: str) -> bool:
        if not wish:
            return False
        try:
            pattern = rf'(?<!\w){re.escape(wish)}(?!\w)'
            return re.search(pattern, target, re.IGNORECASE) is not None
        except re.error:
            return wish.lower() == target.lower()

    if mudae_star_wishes is None:
        mudae_star_wishes = []
    if mudae_regular_wishes is None:
        mudae_regular_wishes = []
    
    # Check Mudae star wishes (highest priority)
    for wish in mudae_star_wishes:
        if wish_matches(cardName, wish) or wish_matches(cardSeries, wish):
            return (True, 3)
    
    # Check Mudae regular wishes and Vars.py wishlist (same priority)
    for wish in mudae_regular_wishes:
        if wish_matches(cardName, wish) or wish_matches(cardSeries, wish):
            return (True, 2)
    
    for item in cast(List[Any], Vars.wishlist):
        priority = 2
        wish_value = item
        if isinstance(item, dict):
            wish_value = item.get('name') or item.get('value') or ''
            try:
                priority = int(item.get('priority') or 2)
            except (TypeError, ValueError):
                priority = 2
            if bool(item.get('is_star') or item.get('star')):
                priority = max(priority, 3)
        if wish_matches(cardName, str(wish_value)) or wish_matches(cardSeries, str(wish_value)):
            return (True, max(1, priority))
    
    return (False, 1)

def pollExternalRolls(token: str, limit: int = 50) -> int:
    """Poll recent channel messages for other users' rolls."""
    lease = None
    try:
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        if bool(getattr(Vars, "ROLL_COORDINATION_ENABLED", True)):
            scope, owner_label = _build_roll_coordination_scope(token, resolved_id, resolved_name)
            lease = acquire_lease(
                scope,
                f"scan:{owner_label}",
                ttl_sec=min(30.0, max(5.0, float(getattr(Vars, "ROLL_LEASE_TTL_SEC", 90.0) or 90.0))),
                heartbeat_sec=max(1.0, float(getattr(Vars, "ROLL_LEASE_HEARTBEAT_SEC", 10.0) or 10.0)),
                wait_timeout_sec=0.0,
                cancel_check=_should_stop,
            )
            if not lease.acquired:
                return external_scan_activity.get(token, 0)
        after_id = external_after_ids.get(token)
        seeded_id = _get_last_seen(Vars.channelId)
        if seeded_id and (not after_id or _is_newer_message_id(seeded_id, after_id)):
            after_id = seeded_id
            external_after_ids[token] = seeded_id

        _, messages = Fetch.fetch_messages(url, auth, after_id=after_id, limit=limit)
        if not messages:
            return 0
        latest_id = Fetch.get_latest_message_id(messages)
        if latest_id:
            external_after_ids[token] = latest_id
            _mark_last_seen(Vars.channelId, latest_id)

        if after_id is None:
            return 0

        wishlist_data = _dashboard_state.get('wishlist', {}) if isinstance(_dashboard_state, dict) else {}
        star_wishes = wishlist_data.get('star_wishes', []) if isinstance(wishlist_data, dict) else []
        regular_wishes = wishlist_data.get('regular_wishes', []) if isinstance(wishlist_data, dict) else []

        _scan_external_rolls(
            messages,
            token,
            resolved_id,
            url,
            auth,
            bot,
            None,
            star_wishes,
            regular_wishes,
            Vars.EMOJI_CLAIM_REACT
        )
        return external_scan_activity.get(token, 0)
    except Exception as exc:
        log(f"Warning: External roll scan failed: {exc}")
        return -1
    finally:
        if lease is not None:
            lease.release()

def _try_external_kakera_react(
    message: Dict[str, Any],
    token: str,
    bot: Any,
    auth: Dict[str, str],
    url: str,
    tu_info: Dict[str, Any]
) -> Dict[str, Any]:
    """React to kakera buttons on another user's roll when possible."""
    _, resolved_name = _ensure_user_identity(token)
    tu_info = _get_steal_tu_info(token, tu_info)
    logged_missing_tu = False
    card_info = extractCardInfo(message)
    card_name = card_info[0] if card_info else "Unknown"
    card_series = card_info[1] if card_info else ""
    spheres, kakera_buttons = _group_reaction_buttons(message)
    if not spheres and not kakera_buttons:
        return tu_info

    for button in spheres:
        sphere_name = cast(str, button.get('emoji_name', ''))
        component = cast(Dict[str, Any], button.get('component', {}))
        if not component:
            continue
        log(f"[STEAL-REACT] Reacting to sphere: {card_name} - {card_series} | {sphere_name}")
        try:
            bot.click(
                message['author']['id'],
                channelID=message['channel_id'],
                guildID=Vars.serverId,
                messageID=message['id'],
                messageFlags=message.get('flags', 0),
                data={'component_type': 2, 'custom_id': component['custom_id']}
            )
            if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
                return tu_info
        except Exception as exc:
            log(f"[STEAL-REACT] Sphere click failed: {exc}")
            continue

    for button in kakera_buttons:
        kakera_type = cast(str, button.get('emoji_name', ''))
        component = cast(Dict[str, Any], button.get('component', {}))
        if not component:
            continue
        is_free_kakera = 'kakerap' in kakera_type.lower()

        log(f"[STEAL-REACT] Attempt: {card_name} - {card_series} | {kakera_type}")

        if not tu_info or 'current_power' not in tu_info:
            if not is_free_kakera and getattr(Vars, 'STEAL_ALLOW_TU_REFRESH', False):
                refreshed = getTuInfo(token)
                if refreshed:
                    tu_info = refreshed
            if not is_free_kakera and (not tu_info or 'current_power' not in tu_info):
                if not logged_missing_tu:
                    log("[STEAL-REACT] Skipped: no recent /tu cache (enable STEAL_ALLOW_TU_REFRESH to refresh)")
                    logged_missing_tu = True
                continue

        if not is_free_kakera and tu_info.get('current_power', 0) < tu_info.get('kakera_cost', 40):
            if tu_info.get('dk_ready', False):
                log(f"[STEAL-REACT] Using /dk (power {tu_info.get('current_power', 0)}%)")
                if not useSpecialCommand(token, 'dk'):
                    log("[STEAL-REACT] Skipped: /dk failed")
                    continue
                if _sleep_interruptible(Vars.SLEEP_MED_SEC):
                    return tu_info
                tu_info['current_power'] = getMaxPowerForToken(token)
                tu_info['dk_ready'] = False
                _cache_tu_info(token, tu_info)
            else:
                log(f"[STEAL-REACT] Skipped: power {tu_info.get('current_power', 0)}% < {tu_info.get('kakera_cost', 40)}% and /dk not ready")
                continue

        try:
            _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
            before_id = Fetch.get_latest_message_id(pre_messages)
            bot.click(
                message['author']['id'],
                channelID=message['channel_id'],
                guildID=Vars.serverId,
                messageID=message['id'],
                messageFlags=message.get('flags', 0),
                data={'component_type': 2, 'custom_id': component['custom_id']}
            )
            if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
                return tu_info
        except Exception as exc:
            log(f"[STEAL-REACT] Click failed: {exc}")
            continue

        r, jsonResp, our_resp = Fetch.wait_for_author_message(
            url,
            auth,
            botID,
            after_id=before_id,
            attempts=4,
            delay_sec=Vars.STEAL_REACT_DELAY_SEC,
            latency_context="core_steal_react",
            content_contains="($k)",
            stop_check=_should_stop
        )
        if r is not None:
            logRawResponse("GET - External kakera reaction response", r)
            logSessionRawResponse("GET - External kakera reaction response", r)

        if not our_resp and jsonResp:
            our_resp = jsonResp[0]
        if not our_resp:
            log(f"[STEAL-REACT] No response: {card_name} - {card_series} | {kakera_type}")
            continue

        resp_msg = our_resp.get('content', '')
        if resp_msg:
            resp_user_match = re.search(r'\*\*([^*]+)\*\*', resp_msg)
            resp_user = resp_user_match.group(1).strip() if resp_user_match else None
            if resp_user and '+' in resp_user:
                resp_user = resp_user.split('+', 1)[0].strip()
            if resp_user and resolved_name and resp_user.lower() != resolved_name.lower():
                log(f"[STEAL-REACT] Ignored response for {resp_user}: {card_name} - {card_series}")
                continue
            if '+' in resp_msg and '($k)' in resp_msg:
                match = re.search(r'\+(\d+)', resp_msg)
                if match:
                    kakera_gained = int(match.group(1))
                    log(f"[STEAL-REACT] Success: +{kakera_gained} ({kakera_type})")
                    updateClaimStats(f"[Reaction: {kakera_type}]", kakera_gained)
                    if not is_free_kakera:
                        tu_info['current_power'] = tu_info.get('current_power', 0) - tu_info.get('kakera_cost', 40)
                        _cache_tu_info(token, tu_info)
                else:
                    log(f"[STEAL-REACT] Success: {resp_msg}")
            elif "can't react to kakera" in resp_msg:
                match = re.search(r'for \*\*(\d+)\*\* min', resp_msg)
                if match:
                    cooldown_min = match.group(1)
                    log(f"[STEAL-REACT] Cooldown: {cooldown_min} min remaining")
                else:
                    log(f"[STEAL-REACT] Failed: {resp_msg}")
            else:
                log(f"[STEAL-REACT] Info: {resp_msg}")

    return tu_info

def _scan_external_rolls(
    messages: List[Dict[str, Any]],
    token: str,
    user_id: Optional[str],
    url: str,
    auth: Dict[str, str],
    bot: Any,
    tu_info: Optional[Dict[str, Any]],
    mudae_star_wishes: List[str],
    mudae_regular_wishes: List[str],
    claim_emoji: str
) -> Dict[str, Any]:
    """Scan recent messages for other users' rolls and attempt steal-claim on wishlist hits."""
    if not messages:
        external_scan_activity[token] = 0
        return tu_info or {}

    dashboard_updated = False
    if tu_info is None:
        tu_info = {}
    tu_info = _get_steal_tu_info(token, tu_info)
    logged_missing_tu = False
    external_count = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_id = msg.get('id')
        if not msg_id:
            continue
        msg_id_str = str(msg_id)
        if not _track_external_roll_id(msg_id_str):
            continue

        if Fetch.extract_interaction_name(msg) != Vars.rollCommand:
            continue

        msg_user_id = Fetch.extract_interaction_user_id(msg)
        if user_id and msg_user_id == user_id:
            continue
        if not user_id and not msg_user_id:
            # Can't confirm the owner, skip to avoid claiming our own roll.
            continue
        external_count += 1

        card_info = extractCardInfo(msg)
        if not card_info:
            continue
        card_name, card_series, card_kakera = card_info
        claim_button_custom_id = _find_claim_button(msg)

        roller_name = Fetch.extract_interaction_user_name(msg) or "unknown"
        claimed_flag = _card_is_claimed(msg)
        is_match, _priority = matchesWishlist(card_name, card_series, mudae_star_wishes, mudae_regular_wishes)
        has_kakera = _message_has_kakera_button(msg)
        skip_claim = _name_in_whitelist(roller_name, cast(List[str], Vars.steal_claim_whitelist))
        skip_react = _name_in_whitelist(roller_name, cast(List[str], Vars.steal_react_whitelist))
        _dashboard_add_other_roll({
            'roller': roller_name,
            'name': card_name,
            'series': card_series,
            'kakera': card_kakera,
            'wishlist': is_match,
            'claimed': claimed_flag,
            'kakera_button': has_kakera
        })
        other_flags: List[str] = []
        if is_match:
            other_flags.append("\u2b50")
        if has_kakera:
            other_flags.append("\U0001f48e")
        if claimed_flag:
            other_flags.append("C")
        if skip_claim:
            other_flags.append("NC")
        if skip_react:
            other_flags.append("NR")
        flags_text = f" [{' '.join(other_flags)}]" if other_flags else ""
        log(f"[OTHERS] {roller_name} | {card_name} - {card_series} | P:{card_kakera}{flags_text}")
        dashboard_updated = True

        if skip_react:
            log(f"[STEAL-REACT] Skipped (whitelist): {roller_name}")

        if is_match and not claimed_flag and not skip_claim:
            log_info(f"External wishlist roll: {roller_name} | {card_name}")

            if not tu_info or 'can_claim_now' not in tu_info:
                if getattr(Vars, 'STEAL_ALLOW_TU_REFRESH', False):
                    refreshed = getTuInfo(token)
                    if refreshed:
                        tu_info = refreshed
                if not tu_info or 'can_claim_now' not in tu_info:
                    if not logged_missing_tu:
                        log("[STEAL-CLAIM] Skipped: no recent /tu cache (enable STEAL_ALLOW_TU_REFRESH to refresh)")
                        logged_missing_tu = True
                    continue

            if not tu_info.get('can_claim_now', False):
                if tu_info.get('rt_available', False):
                    log("[STEAL-CLAIM] Using /rollsutil resetclaimtimer for steal-claim...")
                    if not useSpecialCommand(token, 'rollsutil resetclaimtimer'):
                        log("[STEAL-CLAIM] Skipped: /rt failed")
                        continue
                    if _sleep_interruptible(Vars.SLEEP_MED_SEC):
                        return tu_info
                    tu_info['can_claim_now'] = True
                    tu_info['rt_available'] = False
                    _cache_tu_info(token, tu_info)
                else:
                    log("[STEAL-CLAIM] Skipped: claim cooldown and /rt not ready")
                    continue
            claim_success, actual_kakera, method_used, interrupted = _attempt_claim_with_button_and_fallback(
                bot=bot,
                auth=auth,
                url=url,
                message_id=msg_id_str,
                channel_id=Vars.channelId,
                guild_id=Vars.serverId,
                author_id=str(msg.get('author', {}).get('id', botID)),
                message_flags=int(msg.get('flags', 0) or 0),
                claim_button_custom_id=cast(Optional[str], claim_button_custom_id),
                claim_emoji=claim_emoji,
                card_name=card_name,
                card_series=card_series,
                card_kakera=card_kakera,
                wait_attempts=4,
                wait_delay_sec=Vars.SLEEP_MED_SEC,
                post_react_sleep_sec=Vars.STEAL_REACT_DELAY_SEC,
                log_prefix="[STEAL-CLAIM]",
                log_label="Steal claim"
            )
            if interrupted:
                return tu_info
            if claim_success:
                updateClaimStats(card_name, actual_kakera)
                method_label = 'button' if method_used == 'button' else 'emoji' if method_used == 'emoji' else 'emoji fallback'
                log(f"[STEAL-CLAIM] Success via {method_label}: {card_name} (+{actual_kakera})")
                tu_info['can_claim_now'] = False
                if tu_info.get('rt_available', False):
                    tu_info['rt_available'] = False
                _cache_tu_info(token, tu_info)
            else:
                log(f"[STEAL-CLAIM] Failed: {card_name}")
        if not skip_react:
            tu_info = _try_external_kakera_react(msg, token, bot, auth, url, tu_info)

    if dashboard_updated:
        render_dashboard()
    external_scan_activity[token] = external_count
    return tu_info


def _normalize_wishlist_text(text: str) -> str:
    if not text:
        return text
    normalized = text
    if getattr(Vars, 'WISHLIST_NORMALIZE_TEXT', True):
        replacements = {
            # Common mojibake spellings for marker emojis.
            "âœ…": "\u2705",
            "âŒ": "\u274c",
            "â": "\u274c",
            "â­": "\u2b50",
            "\u00e2\u009c\u0085": "\u2705",
            "\u00e2\u009d\u008c": "\u274c",
            "\u00e2\u00ad\u0090": "\u2b50",
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        # Last-chance repair for latin1-decoded utf-8 payloads.
        try:
            repaired = normalized.encode('latin1').decode('utf-8')
            if repaired and repaired != normalized:
                normalized = repaired
        except Exception:
            pass
    return normalized

def _parse_wishlist_line(line: str) -> Tuple[str, bool, bool, bool]:
    """Return (name, has_star, is_claimed, is_failed) for a /wl line."""
    raw = _normalize_wishlist_text(line or "")
    has_star = ('\u2b50' in raw) or ('\U0001f31f' in raw) or (':star:' in raw)
    is_claimed = ('\u2705' in raw) or (':white_check_mark:' in raw)
    is_failed = ('\u274c' in raw) or ('\u274e' in raw) or (':x:' in raw)

    cleaned = raw.replace('**', '')
    cleaned = re.sub(r'^\s*\d+\.\s*', '', cleaned)
    cleaned = re.sub(r'\s*\+\d+%\s*', ' ', cleaned)
    for token in (
        '\u2b50',
        '\U0001f31f',
        '\u2705',
        '\u274c',
        '\u274e',
        '\ufe0f',
        ':star:',
        ':white_check_mark:',
        ':x:',
    ):
        cleaned = cleaned.replace(token, ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned, has_star, is_claimed, is_failed

def fetchAndParseMudaeWishlist(token: str) -> Dict[str, Any]:
    """
    Fetch Mudae's wishlist via /wl command and parse it.
    Returns organized wishlist with star wishes (⭐) and regular wishes prioritized.
    
    Priority order:
    1. ⭐ Star wishes (highest priority)
    2. Regular wishes with no emoji (same priority as Vars.py)
    3. Skipped: ✅ (already claimed) and ❌ (failed wishes)
    """
    try:
        bot, auth = getClientAndAuth(token)
        url = getUrl()
        resolved_id, resolved_name = _ensure_user_identity(token)
        user_id = resolved_id
        user_name = resolved_name
        fallback_names: List[str] = []
        if user_name:
            fallback_names.append(user_name)
        token_name = getUserNameForToken(token)
        if token_name and token_name.lower() not in {n.lower() for n in fallback_names}:
            fallback_names.append(token_name)
        if current_user_name and current_user_name.lower() not in {n.lower() for n in fallback_names}:
            fallback_names.append(current_user_name)
        
        log("📋 Fetching Mudae's wishlist via /wl...")
        
        cmd = getSlashCommand(bot, ['wl'])
        if not cmd:
            log("Warning: /wl command not available")
            return {'status': 'error', 'error': 'wl command not found'}
        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        _note_last_seen_from_messages(pre_messages)
        before_id = Fetch.get_latest_message_id(pre_messages)
        trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=cmd)
        interaction_id = _record_interaction_context(token, 'wl', user_id, trigger_result)
        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
            return {'status': 'error', 'error': 'interrupted'}
        
        # Get the response
        r, wishlist_resp, our_message = Fetch.wait_for_interaction_message(
            url,
            auth,
            after_id=before_id,
            user_id=user_id,
            user_name=user_name if not user_id else None,
            command_name='wl',
            interaction_id=interaction_id,
            attempts=4,
            delay_sec=Vars.SLEEP_LONG_SEC,
            latency_context="core_wishlist"
        )
        if r is not None:
            logRawResponse("GET - /wl response", r)
            logSessionRawResponse("GET - /wl response", r)
        if wishlist_resp:
            _note_last_seen_from_messages(wishlist_resp)
        
        if our_message:
            _mark_last_seen(Vars.channelId, cast(Optional[str], our_message.get('id')))
            resolved_user_id = Fetch.extract_interaction_user_id(our_message)
            resolved_user_name = Fetch.extract_interaction_user_name(our_message)
            if resolved_user_id or resolved_user_name:
                _set_user_identity_for_token(token, resolved_user_id or user_id, resolved_user_name)
                if resolved_user_id:
                    user_id = resolved_user_id
                if resolved_user_name:
                    user_name = resolved_user_name
        
        
        if r is not None and r.status_code == 200:
            
            # Parse the wishlist from embeds
            star_wishes = []
            regular_wishes = []
            
            if wishlist_resp and len(wishlist_resp) > 0:
                # Search through all messages to find one with embeds
                wishlist_embed = None
                candidates = _filter_messages_with_interaction(
                    wishlist_resp,
                    user_id=user_id,
                    user_name=user_name if not user_id else None,
                    command_name='wl',
                    interaction_id=interaction_id,
                    require_interaction=True
                )
                search_messages = candidates if candidates else wishlist_resp
                fallback_embed = None
                for msg in search_messages:
                    if isinstance(msg, dict) and 'embeds' in msg and len(msg.get('embeds', [])) > 0:
                        # Check if this embed contains wishlist data (has 'author' with name containing "Wishlist")
                        embed = msg['embeds'][0]
                        author_name = embed.get('author', {}).get('name', '')
                        matches_name = False
                        if fallback_names and author_name:
                            author_lower = author_name.lower()
                            matches_name = any(name.lower() in author_lower for name in fallback_names)
                        if 'Wishlist' in author_name or '$wl' in author_name or '$sw' in author_name:
                            if matches_name:
                                wishlist_embed = embed
                                break
                            if fallback_embed is None:
                                fallback_embed = embed
                
                if not wishlist_embed and fallback_embed:
                    wishlist_embed = fallback_embed

                if wishlist_embed:
                    description = wishlist_embed.get('description', '')
                    author_name = wishlist_embed.get('author', {}).get('name', '')

                    wl_used = None
                    wl_total = None
                    sw_used = None
                    sw_total = None
                    wl_match = re.search(r'(\d+)\s*/\s*(\d+)\s*\$wl', author_name)
                    sw_match = re.search(r'(\d+)\s*/\s*(\d+)\s*\$sw', author_name)
                    if wl_match:
                        wl_used = int(wl_match.group(1))
                        wl_total = int(wl_match.group(2))
                    if sw_match:
                        sw_used = int(sw_match.group(1))
                        sw_total = int(sw_match.group(2))
                    
                    log(f"\u2139\ufe0f  {author_name}")
                    
                    # Parse character list from description
                    lines = description.split('\n')
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue

                        char_name, has_star, is_claimed, is_failed = _parse_wishlist_line(line)
                        if not char_name:
                            continue
                        # Claimed/failed entries are not claimable and must be excluded.
                        if is_claimed or is_failed:
                            continue
                        if has_star:
                            star_wishes.append(char_name)
                        else:
                            regular_wishes.append(char_name)

                    log(f"Wishlist parse: {len(star_wishes)} active star, {len(regular_wishes)} active regular")
                    
                    wishlist_data = {
                        'status': 'success',
                        'star_wishes': star_wishes,
                        'regular_wishes': regular_wishes,
                        'all_wishes': star_wishes + regular_wishes,
                        'wl_used': wl_used,
                        'wl_total': wl_total,
                        'sw_used': sw_used,
                        'sw_total': sw_total
                    }
                    _dashboard_set_wishlist(wishlist_data)
                    render_dashboard()
                    return wishlist_data
            
            log("⚠️  No wishlist data found in response")
            empty_wishlist = {'status': 'success', 'star_wishes': [], 'regular_wishes': [], 'all_wishes': []}
            _dashboard_set_wishlist(empty_wishlist)
            render_dashboard()
            return empty_wishlist
        else:
            status = r.status_code if r is not None else 'no response'
            log(f"Failed to fetch wishlist: {status}")
            return {'status': 'error', 'code': status}
    except Exception as e:
        log(f"❌ Error fetching wishlist: {e}")
        return {'status': 'error', 'error': str(e)}

def run_ouro_auto(
    token: str,
    tu_info: Optional[Dict[str, Any]],
    user_name: Optional[str] = None,
    time_budget_sec: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Run auto ouro games ($oh/$oc/$oq) based on /tu info."""
    if not token or not tu_info:
        return tu_info
    if not (getattr(Vars, 'AUTO_OH', False) or getattr(Vars, 'AUTO_OC', False) or getattr(Vars, 'AUTO_OQ', False)):
        return tu_info

    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    rolls_left = _to_int(tu_info.get('rolls'))
    if rolls_left > 0:
        log_info("Skip auto Ouro: rolls are available (prioritize roll session)")
        return tu_info

    oh_left = _to_int(tu_info.get('oh_total') if tu_info.get('oh_total') is not None else tu_info.get('oh_left'))
    oc_left = _to_int(tu_info.get('oc_total') if tu_info.get('oc_total') is not None else tu_info.get('oc_left'))
    oq_left = _to_int(tu_info.get('oq_total') if tu_info.get('oq_total') is not None else tu_info.get('oq_left'))

    if oh_left <= 0 and oc_left <= 0 and oq_left <= 0:
        return tu_info

    total_runs = max(0, oh_left) + max(0, oc_left) + max(0, oq_left)
    required_sec = total_runs * 120
    available_sec = None
    if time_budget_sec is not None:
        available_sec = max(0, int(time_budget_sec))
    else:
        next_reset_sec = tu_info.get('next_reset_sec')
        if isinstance(next_reset_sec, int):
            available_sec = max(0, next_reset_sec)
        else:
            next_reset_min = tu_info.get('next_reset_min')
            if isinstance(next_reset_min, int):
                available_sec = max(0, next_reset_min * 60)
    if available_sec is not None and required_sec > available_sec:
        log_info(
            f"Skip auto Ouro: need {formatTimeHrsMinSec(required_sec)} "
            f"but only {formatTimeHrsMinSec(available_sec)} before next rolls"
        )
        return tu_info

    if _should_stop():
        return tu_info

    resolved_name = user_name or getUserNameForToken(token) or _ensure_user_identity(token)[1] or "Unknown"
    log_info("Starting auto Ouro games...")
    setDashboardState("OURO", last_action="Starting Ouro auto")

    def _run_loop(label: str, count: int, runner) -> Optional[Dict[str, Any]]:
        nonlocal tu_info
        if count <= 0:
            return tu_info
        for idx in range(count):
            if _should_stop():
                return tu_info
            log_info(f"{label} run {idx + 1}/{count}")
            try:
                result = runner(token, resolved_name, quiet=True)
            except BaseException as exc:
                log_warn(f"{label} auto failed: {exc}")
                break
            if isinstance(result, dict):
                if result.get('status') == 'busy':
                    log_info(f"Skip {label} auto: another same-account action is already in progress")
                    break
                if result.get('status') == 'cooldown':
                    break
                if result.get('status') == 'error':
                    break
            if _sleep_interruptible(Vars.SLEEP_MED_SEC):
                return tu_info
            refreshed = getTuInfo(token)
            if refreshed:
                tu_info = refreshed
        return tu_info

    if getattr(Vars, 'AUTO_OH', False) and oh_left > 0:
        try:
            from mudae.ouro.Oh_bot import run_session as run_oh_session
            _run_loop("$oh", oh_left, run_oh_session)
        except BaseException as exc:
            log_warn(f"Auto $oh unavailable: {exc}")

    if getattr(Vars, 'AUTO_OC', False) and oc_left > 0:
        try:
            from mudae.ouro.Oc_bot import run_session as run_oc_session
            _run_loop("$oc", oc_left, run_oc_session)
        except BaseException as exc:
            log_warn(f"Auto $oc unavailable: {exc}")

    if getattr(Vars, 'AUTO_OQ', False) and oq_left > 0:
        try:
            from mudae.ouro.Oq_bot import run_session as run_oq_session
            _run_loop("$oq", oq_left, run_oq_session)
        except BaseException as exc:
            log_warn(f"Auto $oq unavailable: {exc}")

    setDashboardState("READY", last_action="Ouro auto complete")
    return tu_info


def _build_roll_coordination_scope(
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
) -> Tuple[str, str]:
    return build_identity_scope(
        "mudae-action",
        server_id=Vars.serverId,
        channel_id=Vars.channelId,
        token=token,
        user_id=user_id,
        user_name=user_name,
    )


def _message_indicates_roll_exhausted(content: str) -> bool:
    lowered = content.lower()
    if "you don't have any rolls left" in lowered:
        return True
    if "you do not have any rolls left" in lowered:
        return True
    if "no rolls left" in lowered:
        return True
    return "rolls left" in lowered and "next rolls reset" in lowered


def _refresh_roll_status(token: str, reason: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    log(f"🔄 Refreshing /tu ({reason})...")
    refreshed = getTuInfo(token)
    if refreshed:
        return refreshed, True
    log_warn(f"Unable to refresh /tu ({reason})")
    return None, False


def _reconcile_roll_target(
    roll_count: int,
    rolls_to_make: int,
    refreshed_tu: Optional[Dict[str, Any]],
) -> Tuple[int, int, bool]:
    rolls_now = 0
    if refreshed_tu:
        try:
            rolls_now = max(0, int(refreshed_tu.get("rolls", 0) or 0))
        except (TypeError, ValueError):
            rolls_now = 0
    expected_remaining = max(rolls_to_make - roll_count, 0)
    new_target = rolls_to_make
    if rolls_now < expected_remaining:
        new_target = max(roll_count, roll_count + rolls_now)
    return (new_target, rolls_now, rolls_now <= 0)


def _run_enhanced_roll_session(
    *,
    token: str,
    bot: Any,
    auth: Dict[str, str],
    url: str,
    user_id: Optional[str],
    user_name: Optional[str],
    tu_info: Dict[str, Any],
    mudae_star_wishes: List[str],
    mudae_regular_wishes: List[str],
) -> Optional[Dict[str, Any]]:
    refreshed_tu, refreshed_ok = _refresh_roll_status(token, "pre-roll verification")
    if refreshed_ok and refreshed_tu:
        tu_info = refreshed_tu
    else:
        log_warn("Skipping roll session because /tu could not be revalidated before rolling")
        render_dashboard()
        return tu_info

    if tu_info.get("maintenance", False):
        maintenance_min = tu_info["next_reset_min"]
        log(f"⏸️  Waiting for maintenance to complete ({maintenance_min} minutes)...")
        log(f"⏸️  Bot ending - Mudae under maintenance")
        render_dashboard()
        return tu_info

    if tu_info.get("daily_available", False):
        log("📋 Using /daily to gain /rolls usage...")
        useSpecialCommand(token, "daily")
        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
            return None
        refreshed_tu, refreshed_ok = _refresh_roll_status(token, "post-/daily verification")
        if refreshed_ok and refreshed_tu:
            tu_info = refreshed_tu

    if not isSessionEligible(tu_info):
        log("⏸️  Session not eligible to proceed. Waiting for reset...")
        next_reset = min(
            tu_info.get("next_reset_min", 9999),
            tu_info.get("claim_reset_min", 9999),
            tu_info.get("react_cooldown_min", 9999),
        )
        log(f"Next opportunity in {next_reset} minutes")
        render_dashboard()
        return tu_info

    log("✅ Session eligible, proceeding with rolls")

    rolls_to_make = tu_info.get("rolls", 0)
    if rolls_to_make == 0:
        roll_reset_sec = tu_info.get("next_reset_sec")
        if roll_reset_sec is None:
            roll_reset_min = tu_info.get("next_reset_min", 0)
            roll_reset_sec = int(roll_reset_min * 60) if roll_reset_min else 0
        if roll_reset_sec and roll_reset_sec <= 5 * 60:
            log(f"📊 Skipping /rolls (refresh in {formatTimeHrsMinSec(roll_reset_sec)})")
            _dashboard_set_roll_progress(0, None)
            render_dashboard()
            return tu_info
        log("📊 No rolls left, using /rolls...")
        used_rolls = useSpecialCommand(token, "rolls")
        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
            return None
        if not used_rolls:
            log("Warning: /rolls unavailable; skipping roll session")
            render_dashboard()
            return tu_info
        refreshed_tu, refreshed_ok = _refresh_roll_status(token, "post-/rolls verification")
        if not refreshed_ok or not refreshed_tu:
            log("❌ Failed to refresh /tu after /rolls")
            render_dashboard()
            return None
        tu_info = refreshed_tu
        rolls_to_make = tu_info.get("rolls", 0)
        if rolls_to_make == 0:
            log("Warning: No rolls available after /rolls")
            render_dashboard()
            return tu_info

    log(f"✅ Starting with {rolls_to_make} rolls")
    _dashboard_set_roll_progress(rolls_to_make, rolls_to_make)
    render_dashboard()

    rollCommand = getSlashCommand(bot, [Vars.rollCommand])
    if not rollCommand:
        log("❌ Error getting roll command")
        render_dashboard()
        return None

    claimed = Vars.EMOJI_STATUS_CLAIMED
    unclaimed = Vars.EMOJI_STATUS_UNCLAIMED
    emoji = Vars.EMOJI_CLAIM_REACT
    roll_count = 0
    candidates: List[Dict[str, Any]] = []
    stalled_roll_attempts = 0
    stall_refresh_threshold = max(1, int(getattr(Vars, "ROLL_STALL_REFRESH_THRESHOLD", 2) or 2))
    stall_abort_threshold = max(
        stall_refresh_threshold,
        int(getattr(Vars, "ROLL_STALL_ABORT_THRESHOLD", 4) or 4),
    )

    def _handle_roll_stall(reason: str, *, immediate_refresh: bool = False) -> bool:
        nonlocal stalled_roll_attempts, tu_info, rolls_to_make
        stalled_roll_attempts += 1
        if not immediate_refresh and stalled_roll_attempts < stall_refresh_threshold:
            return False
        refreshed_status, refreshed_ok = _refresh_roll_status(
            token,
            f"{reason}; roll {roll_count + 1}/{max(rolls_to_make, 1)}",
        )
        if refreshed_ok and refreshed_status:
            tu_info = refreshed_status
            new_target, rolls_now, exhausted = _reconcile_roll_target(roll_count, rolls_to_make, refreshed_status)
            if new_target != rolls_to_make:
                log_warn(
                    f"Adjusting roll target from {rolls_to_make} to {new_target} "
                    f"after {reason} (/tu now shows {rolls_now} rolls left)"
                )
                rolls_to_make = new_target
            _dashboard_set_roll_progress(max(rolls_to_make - roll_count, 0), rolls_to_make)
            render_dashboard()
            if exhausted:
                log_warn(f"Stopping roll loop after {reason}: /tu confirms 0 rolls left")
                return True
            if stalled_roll_attempts >= stall_abort_threshold:
                log_warn(f"Stopping roll loop after repeated stalls ({stalled_roll_attempts}x): {reason}")
                return True
            return False
        if stalled_roll_attempts >= stall_abort_threshold:
            log_warn(f"Stopping roll loop after repeated stalls and failed /tu refresh: {reason}")
            return True
        return False

    while roll_count < rolls_to_make:
        log(f"🎯 Roll {roll_count + 1}/{rolls_to_make}")

        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        _note_last_seen_from_messages(pre_messages)
        before_id = Fetch.get_latest_message_id(pre_messages)
        roll_sent_at = time.perf_counter()
        trigger_result = bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=rollCommand)
        _emit_latency_event(
            "command_sent",
            flow="core",
            action="roll",
            roll_index=roll_count + 1,
        )
        interaction_id = _record_interaction_context(token, Vars.rollCommand, user_id, trigger_result)
        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.ROLL_TRIGGER_DELAY_SEC):
            return None

        r, jsonCard, card = Fetch.wait_for_interaction_message(
            url,
            auth,
            after_id=before_id,
            user_id=user_id,
            user_name=user_name if not user_id else None,
            command_name=Vars.rollCommand,
            interaction_id=interaction_id,
            attempts=4,
            delay_sec=Vars.SLEEP_LONG_SEC,
            latency_context="core_roll_fetch",
            stop_check=_should_stop,
        )
        if r is not None:
            logRawResponse("GET - Fetch card", r)
            logSessionRawResponse("GET - Fetch card", r)
        if not jsonCard:
            log("Warning: No response from API")
            if _handle_roll_stall("missing API response"):
                break
            continue
        _note_last_seen_from_messages(jsonCard)

        if not card:
            candidates = _filter_messages_with_interaction(
                jsonCard,
                user_id=user_id,
                user_name=user_name if not user_id else None,
                command_name=Vars.rollCommand,
                interaction_id=interaction_id,
                require_interaction=True,
            )
            if candidates:
                card = candidates[0]

        if not card:
            log("Warning: No card response from our user")
            if _handle_roll_stall("missing matching roll response"):
                break
            continue
        _mark_last_seen(Vars.channelId, cast(Optional[str], card.get("id")))
        _emit_latency_event(
            "message_detected",
            flow="core",
            action="roll",
            roll_index=roll_count + 1,
            response_ms=int((time.perf_counter() - roll_sent_at) * 1000.0),
            detect_lag_ms=_latency_message_lag_ms(card),
        )

        resolved_user_id = Fetch.extract_interaction_user_id(card)
        resolved_user_name = Fetch.extract_interaction_user_name(card)
        if resolved_user_id or resolved_user_name:
            _set_user_identity_for_token(token, resolved_user_id or user_id, resolved_user_name)
            if resolved_user_id:
                user_id = resolved_user_id
            if resolved_user_name:
                user_name = resolved_user_name

        if len(card.get("content", "")) > 0 and "embeds" not in card:
            content = cast(str, card.get("content", ""))
            log(f"⚠️ Bot message: {content[:80]}")
            if _handle_roll_stall(
                "bot roll message",
                immediate_refresh=_message_indicates_roll_exhausted(content),
            ):
                break
            continue

        card_info = extractCardInfo(card)
        if not card_info:
            log("Warning: Could not parse card info")
            if _handle_roll_stall("unparseable card response"):
                break
            continue
        stalled_roll_attempts = 0
        cardName, cardSeries, cardKakera = card_info
        keys = extractKeyCounts(card)
        image_url = extractCardImageUrl(card)
        claim_button_custom_id = _find_claim_button(card)

        is_claimed = _card_is_claimed(card)
        status = claimed if is_claimed else unclaimed
        power_label = f"P:{cardKakera}"
        if keys:
            power_label = f"{' '.join(f'K:{count}' for count in keys)} {power_label}"
        log(f"{status} | {power_label} | {cardName} - {cardSeries}")

        is_wishlist = False
        candidate_found = False
        if not is_claimed:
            is_match, priority = matchesWishlist(cardName, cardSeries, mudae_star_wishes, mudae_regular_wishes)
            if is_match:
                is_wishlist = True
                candidates.append({
                    "id": cast(str, card.get("id")),
                    "name": cardName,
                    "series": cardSeries,
                    "kakera": cardKakera,
                    "image_url": image_url,
                    "claim_button_custom_id": claim_button_custom_id,
                    "message_flags": card.get("flags", 0),
                    "wishlist": True,
                    "priority": priority,
                })
                candidate_found = True
                priority_emoji = "\u2b50" if priority == 3 else "\U0001f4cc"
                log(f"   → {priority_emoji} Candidate: Wishlist match (priority: {priority})")
            elif cardKakera >= Vars.minKakeratoclaim:
                candidates.append({
                    "id": cast(str, card.get("id")),
                    "name": cardName,
                    "series": cardSeries,
                    "kakera": cardKakera,
                    "image_url": image_url,
                    "claim_button_custom_id": claim_button_custom_id,
                    "message_flags": card.get("flags", 0),
                    "wishlist": False,
                    "priority": 1,
                })
                candidate_found = True
                log(f"   → 💰 Candidate: Kakera >= {Vars.minKakeratoclaim}")

        roll_entry = {
            "name": cardName,
            "series": cardSeries,
            "kakera": cardKakera,
            "image_url": image_url,
            "keys": keys,
            "wishlist": is_wishlist,
            "candidate": candidate_found,
            "kakera_react": False,
            "claimed": is_claimed,
        }
        _dashboard_add_roll(roll_entry)

        kakera_react_found = False
        try:
            spheres, kakera_buttons = _group_reaction_buttons(card)
            if spheres or kakera_buttons:
                kakera_react_found = True
            for button in spheres:
                sphere_name = cast(str, button.get("emoji_name", ""))
                component = cast(Dict[str, Any], button.get("component", {}))
                if not component:
                    continue
                log(f"   ? Reacting to sphere: {sphere_name}")
                bot.click(
                    card["author"]["id"],
                    channelID=card["channel_id"],
                    guildID=Vars.serverId,
                    messageID=card["id"],
                    messageFlags=card.get("flags", 0),
                    data={"component_type": 2, "custom_id": component["custom_id"]},
                )
                if _sleep_interruptible(Vars.SLEEP_SHORT_SEC):
                    return None
            for button in kakera_buttons:
                kakeraType = cast(str, button.get("emoji_name", ""))
                component = cast(Dict[str, Any], button.get("component", {}))
                if not component:
                    continue
                is_free_kakera = "kakerap" in kakeraType.lower()

                if not is_free_kakera and tu_info.get("current_power", 0) < tu_info.get("kakera_cost", 40):
                    if tu_info.get("dk_ready", False):
                        log(f"   🔋 Insufficient power ({tu_info['current_power']}%), using /dk...")
                        useSpecialCommand(token, "dk")
                        if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
                            return None
                        tu_info = getTuInfo(token)
                        log(f"   ⚡ Power refreshed to {tu_info.get('current_power', 0)}%")
                    else:
                        log(
                            f"   ⚠️  Not enough power ({tu_info['current_power']}% < "
                            f"{tu_info['kakera_cost']}%) and /dk not ready"
                        )
                        continue

                log(f"   ? Reacting to kakera: {kakeraType}")
                _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
                before_id = Fetch.get_latest_message_id(pre_messages)
                bot.click(
                    card["author"]["id"],
                    channelID=card["channel_id"],
                    guildID=Vars.serverId,
                    messageID=card["id"],
                    messageFlags=card.get("flags", 0),
                    data={"component_type": 2, "custom_id": component["custom_id"]},
                )
                if _sleep_interruptible(Vars.SLEEP_MED_SEC):
                    return None

                react_sent_at = time.perf_counter()
                _emit_latency_event(
                    "action_sent",
                    flow="core",
                    action="kakera_react",
                    kakera_type=kakeraType,
                )
                r, jsonResp, our_resp = Fetch.wait_for_author_message(
                    url,
                    auth,
                    botID,
                    after_id=before_id,
                    attempts=4,
                    delay_sec=Vars.KAKERA_REACT_DELAY_SEC,
                    latency_context="core_kakera_react_ack",
                    content_contains="($k)",
                    stop_check=_should_stop,
                )
                if r is not None:
                    logRawResponse("GET - Kakera reaction response", r)
                    logSessionRawResponse("GET - Kakera reaction response", r)

                if not our_resp and jsonResp:
                    our_resp = jsonResp[0]
                if our_resp:
                    _emit_latency_event(
                        "action_ack",
                        flow="core",
                        action="kakera_react",
                        response_ms=int((time.perf_counter() - react_sent_at) * 1000.0),
                        detect_lag_ms=_latency_message_lag_ms(our_resp),
                        status_code=r.status_code if r is not None else None,
                    )
                    resp_msg = our_resp.get("content", "")

                    if resp_msg:
                        if "+" in resp_msg and "($k)" in resp_msg:
                            match = re.search(r"\+(\d+)", resp_msg)
                            if match:
                                kakera_gained = int(match.group(1))
                                log(f"   ✅ Kakera reaction success: +{kakera_gained}")
                                updateClaimStats(f"[Reaction: {kakeraType}]", kakera_gained)
                                if not is_free_kakera:
                                    tu_info["current_power"] -= tu_info["kakera_cost"]
                            else:
                                log(f"   ✅ Kakera reaction success: {resp_msg}")
                        elif "can't react to kakera" in resp_msg:
                            match = re.search(r"for \*\*(\d+)\*\* min", resp_msg)
                            if match:
                                cooldown_min = match.group(1)
                                log(f"   ❌ Kakera on cooldown: {cooldown_min} min remaining")
                            else:
                                log(f"   ❌ Kakera reaction failed: {resp_msg}")
                        else:
                            log(f"   ← {resp_msg}")
        except (KeyError, IndexError, Exception):
            pass

        if kakera_react_found:
            _dashboard_mark_last_roll("kakera_react", True)

        roll_count += 1
        _dashboard_set_roll_progress(max(rolls_to_make - roll_count, 0), rolls_to_make)
        render_dashboard()

    if roll_count < rolls_to_make:
        log_warn(f"Roll session stopped early at {roll_count}/{rolls_to_make}")
    else:
        log(f"✅ Rolling complete! {roll_count} rolls made")

    log("🔍 Scanning for manual claims...")
    selected_user_name = user_name or getUserNameForToken(token)

    if selected_user_name:
        manual_claims = scanForManualClaims(token, selected_user_name)
        if manual_claims:
            log(f"📌 Found {len(manual_claims)} manual claim(s)")
            for claim in manual_claims:
                log(f"   ✨ Manual claim: {claim['character_name']} ({claim['series']}) - {claim['kakera']} kakera")
                updateClaimStats(claim["character_name"], claim["kakera"])
        else:
            log("   ✓ No manual claims detected")

    if candidates:
        best_candidate = max(candidates, key=lambda x: (x.get("priority", 1), x.get("kakera", 0)))
        best_candidate_state = {
            "name": best_candidate.get("name"),
            "series": best_candidate.get("series"),
            "kakera": best_candidate.get("kakera"),
            "image_url": best_candidate.get("image_url"),
            "status": "PENDING",
        }
        _dashboard_set_best_candidate(best_candidate_state)
        render_dashboard()

        if best_candidate["wishlist"]:
            claim_reason = f"✨ Wishlist match (priority {best_candidate.get('priority', 2)})"
        else:
            claim_reason = f"💰 Kakera: {best_candidate['kakera']}"
        log(f"\n📌 Best candidate to claim: {best_candidate['name']} - {best_candidate['series']} ({claim_reason})")
        if best_candidate.get("image_url"):
            log(f"   Image: {best_candidate.get('image_url')}")

        if tu_info.get("can_claim_now", False):
            log("   ✅ Claim cooldown available")
            best_candidate_state["status"] = "READY"
            _dashboard_set_best_candidate(best_candidate_state)
            render_dashboard()
        elif not tu_info.get("can_claim_now", False):
            if best_candidate["wishlist"]:
                if tu_info.get("rt_available", False):
                    best_candidate_state["status"] = "USING $rt"
                    _dashboard_set_best_candidate(best_candidate_state)
                    render_dashboard()
                    log("   🔄 Using /rollsutil resetclaimtimer to bypass claim cooldown for wished character...")
                    useSpecialCommand(token, "rollsutil resetclaimtimer")
                    if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
                        return None
                    tu_info = getTuInfo(token)
                else:
                    best_candidate_state["status"] = "CAN'T CLAIM (cooldown + $rt not ready)"
                    _dashboard_set_best_candidate(best_candidate_state)
                    render_dashboard()
                    log(
                        f"   ❌ Can't claim wished character: cooldown "
                        f"{tu_info.get('claim_reset_min', 'unknown')}m and $rt not ready"
                    )
                    return tu_info
            else:
                best_candidate_state["status"] = "SKIPPED (cooldown + $rt reserved)"
                _dashboard_set_best_candidate(best_candidate_state)
                render_dashboard()
                log(
                    f"   ⏭️  Skipping non-wished character: claim cooldown "
                    f"{tu_info.get('claim_reset_min', 'unknown')}m and $rt reserved for wished characters"
                )
                return tu_info

        try:
            claim_success, actual_kakera, method_used, interrupted = _attempt_claim_with_button_and_fallback(
                bot=bot,
                auth=auth,
                url=url,
                message_id=str(best_candidate["id"]),
                channel_id=Vars.channelId,
                guild_id=Vars.serverId,
                author_id=str(botID),
                message_flags=int(best_candidate.get("message_flags", 0) or 0),
                claim_button_custom_id=cast(Optional[str], best_candidate.get("claim_button_custom_id")),
                claim_emoji=emoji,
                card_name=cast(str, best_candidate.get("name", "")),
                card_series=cast(str, best_candidate.get("series", "")),
                card_kakera=cast(int, best_candidate.get("kakera", 0)),
                wait_attempts=4,
                wait_delay_sec=Vars.SLEEP_LONG_SEC,
                post_react_sleep_sec=Vars.SLEEP_LONG_SEC + Vars.SLEEP_MED_SEC,
                log_prefix="   ",
                log_label="Claim",
                dashboard_state=best_candidate_state,
            )
            if interrupted:
                return None

            if claim_success:
                method_label = "button" if method_used == "button" else "emoji" if method_used == "emoji" else "emoji fallback"
                log(f"   Claim success via {method_label}")
                if actual_kakera != best_candidate["kakera"]:
                    log(f"   Actual kakera gained: +{actual_kakera} (with bonuses)")
                updateClaimStats(best_candidate["name"], actual_kakera)
                log(f"   Successfully claimed {best_candidate['name']}!")
                best_candidate_state["status"] = "CLAIMED"
                _dashboard_set_best_candidate(best_candidate_state)
                render_dashboard()
            else:
                log("   Claim failed: Character not obtained")
                best_candidate_state["status"] = "CLAIM FAILED"
                _dashboard_set_best_candidate(best_candidate_state)
                render_dashboard()
        except Exception as e:
            log(f"   Claim failed: {e}")
            best_candidate_state["status"] = "CLAIM FAILED"
            _dashboard_set_best_candidate(best_candidate_state)
            render_dashboard()
    else:
        log("📌 No candidates found to claim")
        _dashboard_set_best_candidate(None)
        render_dashboard()

    log("📊 Checking final roll status...")
    tu_info = getTuInfo(token)

    stats_all = loadClaimStats()
    stats = getOrInitializeUserStats(stats_all, user_id) if user_id else {}
    kakera_reactions = [c for c in stats.get("characters_claimed", []) if c["name"].startswith("[Reaction:")]
    character_claims = [c for c in stats.get("characters_claimed", []) if not c["name"].startswith("[Reaction:")]

    if stats.get("total_claims", 0) > 0:
        log("\n" + "=" * 50)
        log("📈 CLAIM STATISTICS")
        log("=" * 50)

        if kakera_reactions:
            kakera_reaction_total = sum(c["kakera"] for c in kakera_reactions)
            log(f"Kakera reactions collected: {len(kakera_reactions)}")
            log(f"Total kakera from reactions: {kakera_reaction_total}")
            for reaction in kakera_reactions[-3:]:
                log(f"  • {reaction['name']} - {reaction['kakera']} kakera")

        if character_claims:
            character_claim_total = sum(c["kakera"] for c in character_claims)
            log(f"Character claims: {len(character_claims)}")
            log(f"Total kakera from claims: {character_claim_total}")
            for char in character_claims[-3:]:
                log(f"  • {char['name']} - {char['kakera']} kakera")

        log(f"\n📊 Increased Balance Since Start: {stats.get('total_kakera', 0)} kakera")

        if tu_info and tu_info.get("total_balance"):
            log(f"💰 Total Balance: {tu_info['total_balance']} kakera")

        log("=" * 50)

    global _program_rolls_total
    _program_rolls_total += roll_count
    summary: Dict[str, Any] = {"rolls_total": _program_rolls_total}
    summary["claims_total"] = len(character_claims)
    if character_claims:
        summary["claims_latest"] = character_claims[-1].get("name")
    summary["kakera_total"] = stats.get("total_kakera", 0)
    if kakera_reactions:
        summary["reaction_kakera_total"] = sum(c["kakera"] for c in kakera_reactions)
    if tu_info and tu_info.get("total_balance"):
        summary["total_balance"] = tu_info["total_balance"]
    _dashboard_set_summary(summary)
    render_dashboard()

    if Vars.pokeRoll:
        log("\n🎰 Attempting Pokeslot...")
        try:
            r = requests.post(url=url, headers=auth, data={"content": "$p"}, timeout=Fetch.get_timeout())
            logRawResponse("POST - Pokeslot command", r)
            logSessionRawResponse("POST - Pokeslot command", r)
        except Exception as e:
            log(f"❌ Pokeslot error: {e}")

    return tu_info


def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Enhanced rolling system with command management and eligibility checks"""
    bot, auth = getClientAndAuth(token)
    url = getUrl()
    resolved_id, resolved_name = _ensure_user_identity(token)
    user_id = resolved_id
    user_name = resolved_name
    
    session_start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    _dashboard_reset_roll_state(session_start)
    log(f"🎲 Enhanced Roll Session starting at {session_start}")
    render_dashboard()
    if _should_stop():
        return None
    
    # Step 1: Get initial status
    cached_tu = initial_tu_info if initial_tu_info is not None else initial_tu_cache.pop(token, None)
    if cached_tu:
        tu_info = cached_tu
        detailed_status = formatDetailedStatus(tu_info)
        log(f"📊 Status (cached): {detailed_status}")
        _dashboard_set_status(tu_info)
        render_dashboard()
        _cache_tu_info(token, tu_info)
    else:
        tu_info = getTuInfo(token)
    if not tu_info:
        log("❌ Failed to get /tu info")
        render_dashboard()
        return None

    # Step 1A: Fetch and parse Mudae's wishlist
    log("📋 Step 1A: Fetching Mudae's wishlist...")
    mudae_wishlist = fetchAndParseMudaeWishlist(token)
    
    if mudae_wishlist['status'] != 'success':
        log("⚠️  Could not fetch Mudae wishlist, continuing with Vars.py wishlist only")
        mudae_star_wishes = []
        mudae_regular_wishes = []
    else:
        mudae_star_wishes = mudae_wishlist.get('star_wishes', [])
        mudae_regular_wishes = mudae_wishlist.get('regular_wishes', [])
    
    if Latency.get_active_tier() == "legacy" and _sleep_interruptible(Vars.SLEEP_LONG_SEC):
        return None

    coordination_enabled = bool(getattr(Vars, "ROLL_COORDINATION_ENABLED", True))
    lease = None
    try:
        if coordination_enabled:
            scope, owner_label = _build_roll_coordination_scope(token, user_id, user_name)
            wait_sec = max(0.0, float(getattr(Vars, "ROLL_LEASE_WAIT_SEC", 120.0) or 120.0))
            ttl_sec = max(5.0, float(getattr(Vars, "ROLL_LEASE_TTL_SEC", 90.0) or 90.0))
            heartbeat_sec = max(1.0, float(getattr(Vars, "ROLL_LEASE_HEARTBEAT_SEC", 10.0) or 10.0))
            log("🔒 Waiting for roll coordination lease...")
            lease = acquire_lease(
                scope,
                owner_label,
                ttl_sec=ttl_sec,
                heartbeat_sec=heartbeat_sec,
                wait_timeout_sec=wait_sec,
                cancel_check=_should_stop,
            )
            if not lease.acquired:
                log_warn("Another instance still owns the roll lease; skipping this cycle")
                refreshed_after_wait, refreshed_ok = _refresh_roll_status(token, "lease timeout fallback")
                return refreshed_after_wait if refreshed_ok and refreshed_after_wait else tu_info
            if lease.waited_sec >= 0.25:
                log_info(f"Roll coordination lease acquired after {lease.waited_sec:.1f}s")
        return _run_enhanced_roll_session(
            token=token,
            bot=bot,
            auth=auth,
            url=url,
            user_id=user_id,
            user_name=user_name,
            tu_info=tu_info,
            mudae_star_wishes=mudae_star_wishes,
            mudae_regular_wishes=mudae_regular_wishes,
        )
    finally:
        if lease is not None:
            lease.release()






















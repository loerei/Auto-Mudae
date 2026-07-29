import os
import re
import sys
import time
import json
import threading
from typing import Optional, Any
from mudae.config import vars as Vars
from mudae.paths import LOGS_DIR
from mudae.web.bridge import emit_log
from mudae.ui.colors import success, error, warning, info, highlight, dimmed, colored, ANSIColors

SESSION_LOG_FILE = os.fspath(LOGS_DIR / 'Session.log')

_active_session_log_file: Optional[str] = None
_active_session_rawresponse_file: Optional[str] = None
_rawlog_lock = threading.Lock()
current_user_name: Optional[str] = None
current_user_id: Optional[str] = None

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

def getSessionLogFile() -> str:
    """Get the per-user session log filename"""
    if _active_session_log_file:
        return _active_session_log_file
    if current_user_name:
        log_file = os.fspath(LOGS_DIR / f"Session.{current_user_name}.log")
        return log_file
    return SESSION_LOG_FILE

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
    
    # Write uncolored version to per-user log file
    try:
        log_file = getSessionLogFile()
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(plain_formatted + '\n')
    except Exception as e:
        err_msg = f"Error writing to log: {e}"
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

def logRawResponse(label: str, response_data: Any) -> None:
    """Log raw Discord API responses to Rawresponse."""
    try:
        raw_file = os.fspath(LOGS_DIR / 'Rawresponse.json')
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry = {
            "timestamp": timestamp,
            "label": label,
            "data": response_data
        }
        with _rawlog_lock:
            with open(raw_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        log_warn(f"Failed to log raw response: {e}")

def logSessionRawResponse(label: str, response_data: Any) -> None:
    """Log raw Discord API response data to SessionRawresponse."""
    try:
        session_raw_file = getSessionRawResponseFile()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry = {
            "timestamp": timestamp,
            "label": label,
            "data": response_data
        }
        with _rawlog_lock:
            with open(session_raw_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        log_warn(f"Failed to log session raw response: {e}")

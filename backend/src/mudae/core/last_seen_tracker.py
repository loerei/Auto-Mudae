import os
import json
import time
import threading
from typing import Optional, Dict, Any, Tuple, List
from mudae.config import vars as Vars
from mudae.paths import PROJECT_ROOT
from mudae.discord import fetch as Fetch
from mudae.storage.coordination import acquire_lease, build_path_scope
from mudae.core.session_logging import log

_last_seen_cache: Dict[str, str] = {}
_last_seen_loaded: bool = False
_last_seen_dirty: bool = False
_last_seen_last_flush: float = 0.0
_last_seen_file_signature: Optional[Tuple[int, int]] = None
_last_seen_lock = threading.Lock()

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

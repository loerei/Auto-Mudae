import os
import json
import time
import threading
import requests
from typing import Optional, Dict, Any, Tuple, List, Set
from mudae.config import vars as Vars
from mudae.paths import CONFIG_DIR
from mudae.discord import fetch as Fetch
from mudae.storage.coordination import acquire_lease, build_path_scope
from mudae.core.session_logging import log_warn

AUTO_GIVE_STATE_FILE = os.fspath(CONFIG_DIR / 'auto_give_state.json')
_auto_give_seen_keys: Set[str] = set()
_auto_give_seen_lock = threading.Lock()

def _get_token_config_entry(token: str, Session: Any = None) -> Optional[Dict[str, Any]]:
    vars_obj = getattr(Session, "Vars", Vars) if Session else Vars
    if not token or not hasattr(vars_obj, "tokens") or not isinstance(vars_obj.tokens, list):
        return None
    for entry in vars_obj.tokens:
        if isinstance(entry, dict) and entry.get("token") == token:
            return entry
    return None

def _get_token_config_id(token: str, Session: Any = None) -> Optional[int]:
    entry = _get_token_config_entry(token, Session)
    if entry and "id" in entry:
        try:
            return int(entry["id"])
        except (TypeError, ValueError):
            pass
    return None

def _get_target_mention_for_config_id(config_id: int, Session: Any = None) -> Tuple[Optional[str], Optional[str], str]:
    vars_obj = getattr(Session, "Vars", Vars) if Session else Vars
    if hasattr(vars_obj, "tokens") and isinstance(vars_obj.tokens, list):
        for entry in vars_obj.tokens:
            if isinstance(entry, dict) and entry.get("id") == config_id:
                disc_id = entry.get("discord_user_id")
                name = entry.get("name") or entry.get("discordusername")
                if disc_id:
                    return f"<@{disc_id}>", name, str(disc_id)
                if name:
                    return f"@{name}", name, str(name)
    return f"<@{config_id}>", None, str(config_id)

def _normalize_give_pairs(raw_pairs: Any) -> List[Tuple[int, int]]:
    pairs: List[Tuple[int, int]] = []
    if not isinstance(raw_pairs, list):
        return pairs
    for item in raw_pairs:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                src = int(item[0])
                tgt = int(item[1])
                pairs.append((src, tgt))
            except (TypeError, ValueError):
                continue
    return pairs

def _resolve_give_target_id(pairs: List[Tuple[int, int]], source_id: int) -> Optional[int]:
    for src, tgt in pairs:
        if src == source_id:
            return tgt
    return None

def _auto_give_hour_bucket(now_ts: float) -> str:
    return time.strftime("%Y-%m-%d-%H", time.localtime(now_ts))

def _auto_give_entry_key(source_id: int, resource: str, bucket: str) -> str:
    return f"{source_id}:{resource.lower()}:{bucket}"

def _mark_auto_give_seen(*keys: str) -> None:
    with _auto_give_seen_lock:
        for k in keys:
            if k:
                _auto_give_seen_keys.add(k)

def _is_auto_give_seen(key: str) -> bool:
    if not key:
        return False
    with _auto_give_seen_lock:
        return key in _auto_give_seen_keys

def _load_auto_give_payload() -> Dict[str, Any]:
    from mudae.core import session_engine as Session
    state_file = getattr(Session, "AUTO_GIVE_STATE_FILE", AUTO_GIVE_STATE_FILE)
    if not os.path.exists(state_file):
        return {"entries": {}}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("entries", {})
                return data
    except Exception:
        pass
    return {"entries": {}}

def _save_auto_give_payload(payload: Dict[str, Any]) -> None:
    from mudae.core import session_engine as Session
    state_file = getattr(Session, "AUTO_GIVE_STATE_FILE", AUTO_GIVE_STATE_FILE)
    scope = build_path_scope("auto-give-state", state_file)
    try:
        with acquire_lease(
            scope,
            f"auto-give@pid{os.getpid()}",
            ttl_sec=15.0,
            heartbeat_sec=3.0,
            wait_timeout_sec=15.0,
        ) as lease:
            if not lease.acquired:
                return
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            temp_path = f"{state_file}.{os.getpid()}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, state_file)
    except Exception as exc:
        log_warn(f"Failed to save auto give state: {exc}")

def _load_auto_give_state() -> Dict[str, Any]:
    return _load_auto_give_payload()

def _save_auto_give_state(payload: Dict[str, Any]) -> None:
    _save_auto_give_payload(payload)

def _build_auto_give_entry(
    *,
    bucket: str,
    source_id: int,
    resource: str,
    status: str,
    amount: int,
    now_ts: float,
    target_id: Optional[int] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
        "bucket": bucket,
        "source_id": source_id,
        "target_id": target_id,
        "resource": resource,
        "status": status,
        "amount": amount,
        "note": note or "",
    }

def _send_text_command(
    *,
    url: str,
    auth: Dict[str, str],
    content: str,
    raw_label: str,
) -> bool:
    payload = {"content": content}
    try:
        r = requests.post(url, headers=auth, json=payload, timeout=Fetch.get_timeout())
        return r.status_code in (200, 204)
    except Exception as exc:
        log_warn(f"Failed to send command '{content}': {exc}")
        return False

def maybe_run_scheduled_transfers(
    token: str,
    now: Optional[float] = None,
) -> None:
    """Run scheduled transfers if configured."""
    if not token:
        return
    from mudae.core import session_engine as Session
    now_ts = now if now is not None else time.time()
    user_id = Session._get_user_id_for_token(token)
    user_name = Session._get_user_name_for_token(token)

    config_entry = _get_token_config_entry(token, Session)
    config_id = _get_token_config_id(token, Session) if config_entry else None
    if config_id is None:
        return

    vars_obj = getattr(Session, "Vars", Vars)
    kakera_target = _resolve_give_target_id(_normalize_give_pairs(getattr(vars_obj, "Kakera_Give", [])), config_id)
    sphere_target = _resolve_give_target_id(_normalize_give_pairs(getattr(vars_obj, "Sphere_Give", [])), config_id)
    if not kakera_target and not sphere_target:
        return

    bucket = _auto_give_hour_bucket(now_ts)
    payload = _load_auto_give_payload()
    entries = payload.setdefault("entries", {})

    kakera_key = f"{bucket}|{config_id}|kakera" if kakera_target else None
    sphere_key = f"{bucket}|{config_id}|sphere" if sphere_target else None

    if (kakera_key and kakera_key in entries) and (not sphere_key or sphere_key in entries):
        return
    if (sphere_key and sphere_key in entries) and (not kakera_key or kakera_key in entries):
        return

    gate_fn = getattr(Session, "_acquire_same_account_action_gate", lambda *a, **kw: (None, True))
    gate_lease, gate_acquired = gate_fn(token, user_id, user_name, wait_timeout_sec=0.0)

    cached_tu_cache = getattr(Session, "_last_tu_info_cache", {})
    cached_tu = cached_tu_cache.get(token) or {}

    if not gate_acquired:
        if kakera_key and kakera_key not in entries:
            entries[kakera_key] = {"status": "busy_skip", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}
        if sphere_key and sphere_key not in entries:
            entries[sphere_key] = {"status": "busy_skip", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}
        _save_auto_give_payload(payload)
        return

    url_fn = getattr(Session, "getUrl", lambda: "")
    send_fn = getattr(Session, "_send_text_command", _send_text_command)
    client_and_auth_fn = getattr(Session, "getClientAndAuth", lambda _t: (None, {}))
    _, auth = client_and_auth_fn(token)

    if kakera_key and kakera_key not in entries:
        bal = cached_tu.get("total_balance", 0)
        target_mention, _, _ = _get_target_mention_for_config_id(kakera_target, Session)
        if bal > 0 and target_mention:
            send_fn(url=url_fn(), auth=auth, content=f"$givek {target_mention} {bal}", raw_label="givek")
            send_fn(url=url_fn(), auth=auth, content="y", raw_label="givek_confirm")
            entries[kakera_key] = {"status": "confirmed", "amount": bal, "target_id": kakera_target, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}
            cached_tu["total_balance"] = 0
        else:
            entries[kakera_key] = {"status": "no_balance", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}

    if sphere_key and sphere_key not in entries:
        bal = cached_tu.get("sphere_balance", 0)
        target_mention, _, _ = _get_target_mention_for_config_id(sphere_target, Session)
        if bal and bal > 0 and target_mention:
            send_fn(url=url_fn(), auth=auth, content=f"$givesp {target_mention} {bal}", raw_label="givesp")
            send_fn(url=url_fn(), auth=auth, content="y", raw_label="givesp_confirm")
            entries[sphere_key] = {"status": "confirmed", "amount": bal, "target_id": sphere_target, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}
            cached_tu["sphere_balance"] = 0
        else:
            entries[sphere_key] = {"status": "no_balance", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))}

    _save_auto_give_payload(payload)

def _run_auto_give_from_tu(
    *,
    token: str,
    status: Dict[str, Any],
    url: str,
    auth: Dict[str, str],
) -> None:
    pass

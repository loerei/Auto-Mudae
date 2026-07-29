"""
common_bot.py - Common helper functions shared across Ouro bot runners (Oh_bot, Oc_bot, Oq_bot).
"""

import json
import re
import time
import requests
from typing import Any, Dict, List, Optional, Tuple

from mudae.config import vars as Vars
from mudae.core import latency as Latency
from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file
from mudae.storage.latency_metrics import record_event as record_latency_event
from mudae.web.bridge import emit_runner_event

ACTION_LEASE_TTL_SEC = 180.0
ACTION_LEASE_HEARTBEAT_SEC = 20.0


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def message_url(channel_id: Optional[str] = None) -> str:
    c_id = channel_id or Vars.channelId
    return f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{c_id}/messages"


def base_url() -> str:
    return f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}"


def message_lag_ms(message: Optional[Dict[str, Any]]) -> Optional[int]:
    if not message:
        return None
    timestamp = message.get("timestamp")
    parsed = _parse_discord_timestamp(timestamp) if timestamp else None
    if parsed is None:
        return None
    return int(max(0.0, (time.time() - parsed) * 1000.0))


def emit_bot_latency(flow: str, event: str, **fields: Any) -> None:
    payload = dict(fields)
    payload.setdefault("flow", flow)
    payload.setdefault("tier", Latency.get_active_tier())
    record_latency_event(event, **payload)


def poll_delay(
    attempt_index: int,
    delay_sec: float,
    schedule: Optional[List[float]],
    *,
    legacy_scale: float,
    legacy_cap: float,
) -> float:
    if schedule:
        if attempt_index < len(schedule):
            return max(0.0, float(schedule[attempt_index]))
        return max(0.0, float(schedule[-1]))
    return min(legacy_cap, delay_sec * (legacy_scale ** max(0, attempt_index)))


def post_action_pause(default_sec: float) -> None:
    if Latency.get_active_tier() == "legacy":
        time.sleep(default_sec)
        return
    time.sleep(min(0.08, max(0.0, default_sec)))


def acquire_action_lease(token: str, user_name: str):
    scope, owner_label = build_identity_scope(
        "mudae-action",
        server_id=Vars.serverId,
        channel_id=Vars.channelId,
        token=token,
        user_name=user_name,
    )
    return acquire_lease(
        scope,
        owner_label,
        ttl_sec=ACTION_LEASE_TTL_SEC,
        heartbeat_sec=ACTION_LEASE_HEARTBEAT_SEC,
        wait_timeout_sec=0.0,
    )


def select_user() -> Dict[str, str]:
    print("\n" + "=" * 50)
    print("Available Users:")
    print("=" * 50)
    if not Vars.tokens:
        raise RuntimeError("No tokens configured in Vars.tokens")
    for i, user in enumerate(Vars.tokens, 1):
        print(f"{i}. {user['name']}")
    while True:
        try:
            choice = input("\nSelect user (enter number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(Vars.tokens):
                selected = Vars.tokens[idx]
                print(f"Selected: {selected['name']}")
                return selected
            print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a valid number.")


def parse_clicks_and_time(content: str, default_clicks: int, default_time_sec: int) -> Tuple[int, int]:
    clicks = default_clicks
    time_sec = default_time_sec
    if content:
        match_clicks = re.search(r"click\s*\*\*(\d+)\*\*", content, re.IGNORECASE)
        if match_clicks:
            try:
                clicks = int(match_clicks.group(1))
            except ValueError:
                pass
        match_minutes = re.search(r"for\s+(\d+)\s+minute", content, re.IGNORECASE)
        if match_minutes:
            try:
                time_sec = int(match_minutes.group(1)) * 60
            except ValueError:
                pass
    return clicks, time_sec


def safe_response_json(response: Optional[requests.Response]) -> Optional[Any]:
    if response is None:
        return None
    try:
        return response.json()
    except Exception:
        return None


def truncate_text(text: Optional[str], max_len: int) -> Optional[str]:
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...(truncated {len(text) - max_len} chars)"


def serialize_full_response(payload: Any, response: Optional[requests.Response]) -> Optional[str]:
    text = None
    if payload is not None:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except Exception:
            text = str(payload)
    elif response is not None:
        try:
            text = response.text
        except Exception:
            text = None
    return truncate_text(text, 100_000)


def extract_error_fields(payload: Any) -> Tuple[Optional[str], Optional[Any]]:
    if isinstance(payload, dict):
        return payload.get("message"), payload.get("code")
    return (None, None)


def log_refresh_error(
    log_file: str,
    source_name: str,
    label: str,
    response: Optional[requests.Response],
    payload: Any,
    message_id: str,
    refresh_source: str,
    attempt: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    error_message, error_code = extract_error_fields(payload)
    response_snippet = None
    if response is not None:
        try:
            response_snippet = truncate_text(response.text, 500)
        except Exception:
            response_snippet = None
    entry: Dict[str, Any] = {
        "type": "warn",
        "message": label,
        "message_id": message_id,
        "refresh_source": refresh_source,
        "attempt": attempt,
        "status_code": response.status_code if response is not None else None,
        "error_message": error_message,
        "error_code": error_code,
        "response_snippet": response_snippet,
        "full_response": serialize_full_response(payload, response),
        "source": source_name,
        "ts": _timestamp(),
    }
    if extra:
        entry.update(extra)
    ensure_json_array_file(log_file)
    append_json_array(log_file, entry)
    emit_runner_event(source_name, entry)


def fetch_reward_messages(url: str, auth: Dict[str, str], limit: int = 15) -> List[Dict[str, Any]]:
    headers = dict(auth)
    params = {"limit": str(limit)}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def message_hash(message: Dict[str, Any]) -> str:
    try:
        payload = {
            "content": message.get("content", ""),
            "components": message.get("components", []),
            "embeds": message.get("embeds", []),
            "edited_timestamp": message.get("edited_timestamp"),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(message.get("id", ""))

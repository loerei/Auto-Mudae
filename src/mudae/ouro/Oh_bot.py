"""
Oh_bot.py - Standalone Ouro Harvest ($oh) auto-player.
Selects user, sends $oh, chooses clicks via EV + lookahead, and logs results.
"""

import os
import re
import sys
import time
import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from mudae.config import vars as Vars
from mudae.core import latency as Latency
from mudae.discord import fetch as Fetch
from mudae.paths import LOGS_DIR, ensure_runtime_dirs
from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file
from mudae.storage.latency_metrics import record_event as record_latency_event
from mudae.ouro.oh_config import load_oh_config, update_stats, default_stats_path
from mudae.ouro.oh_parse import parse_oh_message, summarize_grid, OhGrid, diagnose_oh_message
from mudae.ouro.oh_solver import OhSolver
from mudae.ouro.sphere_reward_parse import parse_reward_message

try:
    import discum  # type: ignore[import]
except ImportError:
    print("ERROR: discum library not found. Please install it: pip install discum")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ensure_runtime_dirs()
LOG_FILE = os.fspath(LOGS_DIR / "OuroHarvest.json")

REWARD_LINE_RE = re.compile(r"<:([^:>]+):\d+>\s*\*\*\+(\d+)\*\*")
TURNS_INTO_RE = re.compile(r"<:([^:>]+):\d+>\s+turns into\s+<:([^:>]+):\d+>")
OH_COOLDOWN_RE = re.compile(
    r"You don't have enough \$oh.*?Time to wait before the refill:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


def _make_emitter(quiet: bool):
    if not quiet:
        def _emit(message: str, level: str = "INFO") -> None:
            print(message)
        return _emit
    try:
        from mudae.core.session_engine import log_info, log_warn, log_error
    except Exception:
        def _emit(message: str, level: str = "INFO") -> None:
            return
        return _emit

    def _emit(message: str, level: str = "INFO") -> None:
        if level == "WARN":
            log_warn(message)
        elif level == "ERROR":
            log_error(message)
        else:
            log_info(message)
    return _emit


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _message_lag_ms(message: Optional[Dict[str, Any]]) -> Optional[int]:
    if not message:
        return None
    timestamp = message.get("timestamp")
    parsed = _parse_discord_timestamp(timestamp) if timestamp else None
    if parsed is None:
        return None
    return int(max(0.0, (time.time() - parsed) * 1000.0))


def _emit_latency(event: str, **fields: Any) -> None:
    payload = dict(fields)
    payload.setdefault("flow", "oh")
    payload.setdefault("tier", Latency.get_active_tier())
    record_latency_event(event, **payload)


def _poll_delay(
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


def _post_action_pause(default_sec: float) -> None:
    if Latency.get_active_tier() == "legacy":
        time.sleep(default_sec)
        return
    time.sleep(min(0.08, max(0.0, default_sec)))


def _message_url() -> str:
    return f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{Vars.channelId}/messages"


def _base_url() -> str:
    return f"{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}"


def _log_event(entry: Dict[str, Any]) -> None:
    ensure_json_array_file(LOG_FILE)
    payload = dict(entry)
    payload["ts"] = _timestamp()
    append_json_array(LOG_FILE, payload)


def _select_user() -> Dict[str, str]:
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


def _parse_clicks_and_time(content: str, default_clicks: int, default_time_sec: int) -> Tuple[int, int]:
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


def _message_hash(message: Dict[str, Any]) -> str:
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


def _find_oh_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        components = msg.get("components", [])
        if not isinstance(components, list) or not components:
            continue
        if len(components) != 5:
            continue
        valid_grid = True
        for row in components:
            if not isinstance(row, dict):
                valid_grid = False
                break
            row_components = row.get("components", [])
            if not isinstance(row_components, list) or len(row_components) != 5:
                valid_grid = False
                break
        if not valid_grid:
            continue
        if "You can click" in msg.get("content", ""):
            return msg
        candidates.append(msg)
    return candidates[0] if candidates else None


def _find_oh_cooldown_message(messages: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], Optional[str]]]:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or "$oh" not in content:
            continue
        if "don't have enough $oh" in content:
            match = OH_COOLDOWN_RE.search(content)
            wait_text = match.group(1).strip() if match else None
            return (msg, wait_text)
    return None


def _wait_for_oh_message(
    url: str,
    auth: Dict[str, str],
    after_id: Optional[str],
    attempts: int = 8,
    delay_sec: float = 1.0,
) -> Tuple[Optional[requests.Response], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    last_response: Optional[requests.Response] = None
    base_after = after_id
    schedule = Latency.get_delay_schedule(delay_sec, attempts)
    effective_attempts = max(max(1, attempts), len(schedule) if schedule else 0)
    for attempt in range(effective_attempts):
        response, messages = Fetch.fetch_messages(url, auth, after_id=base_after, limit=50)
        last_response = response
        if response is None:
            Latency.record_poll_result(None, error=True, context="oh_wait_message")
            time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
            continue
        cooldown = _find_oh_cooldown_message(messages)
        if cooldown:
            Latency.record_poll_result(response.status_code, context="oh_wait_message")
            return response, None, cooldown[0], cooldown[1]
        oh_msg = _find_oh_message(messages)
        if oh_msg:
            Latency.record_poll_result(response.status_code, context="oh_wait_message")
            return response, oh_msg, None, None
        newest = Fetch.get_latest_message_id(messages)
        if newest and newest != base_after:
            base_after = newest
        retry_after = Fetch._parse_retry_after(response)
        Latency.record_poll_result(response.status_code, retry_after=retry_after, context="oh_wait_message")
        if retry_after is not None:
            time.sleep(max(retry_after, _poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0)))
            continue
        time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
    return last_response, None, None, None


def _increment_count(counts: Dict[str, int], color: str, delta: int = 1) -> None:
    if not color:
        return
    counts[color] = counts.get(color, 0) + int(delta)


def _parse_reward_content(content: str, emoji_map: Dict[str, str]) -> Dict[str, Any]:
    if not content:
        return {}
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    last_reward_line = None
    reward_lines = [line for line in lines if "+" in line]
    if reward_lines:
        last_reward_line = reward_lines[-1]

    reward_color = None
    reward_value = None
    if last_reward_line:
        m = REWARD_LINE_RE.search(last_reward_line)
        if m:
            emoji_name = m.group(1)
            reward_color = emoji_map.get(emoji_name)
            try:
                reward_value = int(m.group(2))
            except ValueError:
                reward_value = None

    resolved_color = None
    turns = None
    for line in lines:
        m2 = TURNS_INTO_RE.search(line)
        if m2:
            turns = (m2.group(1), m2.group(2))
    if turns:
        resolved_color = emoji_map.get(turns[1])

    return {
        "reward_color": reward_color,
        "reward_value": reward_value,
        "resolved_color": resolved_color,
        "raw": content[:2000],
    }


def _fetch_latest_reward_message(url: str, auth: Dict[str, str]) -> Optional[Dict[str, Any]]:
    response, messages = Fetch.fetch_messages(url, auth, limit=10)
    if response is None or not messages:
        return None
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and "+" in content:
            return msg
    return None


def _fetch_reward_messages(url: str, auth: Dict[str, str], limit: int = 15) -> List[Dict[str, Any]]:
    response, messages = Fetch.fetch_messages(url, auth, limit=limit)
    if response is None or not messages:
        return []
    reward_messages: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if "**+" not in content and "Stock:" not in content:
            continue
        if "<:" not in content:
            continue
        reward_messages.append(msg)
    return reward_messages


def _message_id_int(message: Dict[str, Any]) -> int:
    try:
        return int(message.get("id", 0))
    except (TypeError, ValueError):
        return 0


def _collect_reward_delta(
    messages: List[Dict[str, Any]],
    processed_counts: Dict[str, int],
) -> Tuple[int, List[Dict[str, Any]], Optional[int], Optional[Dict[str, Any]]]:
    if not messages:
        return 0, [], None, None
    sorted_messages = sorted(messages, key=_message_id_int)
    delta = 0
    new_entries: List[Dict[str, Any]] = []
    latest_stock: Optional[int] = None
    latest_message: Optional[Dict[str, Any]] = None
    for msg in sorted_messages:
        msg_id = str(msg.get("id", ""))
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = ""
        entries, stock = parse_reward_message(content)
        if stock is not None:
            latest_stock = stock
        latest_message = msg
        prev_count = processed_counts.get(msg_id, 0)
        if prev_count < len(entries):
            for entry in entries[prev_count:]:
                entry_payload = dict(entry)
                entry_payload["message_id"] = msg_id
                new_entries.append(entry_payload)
                try:
                    delta += int(entry.get("amount", 0))
                except (TypeError, ValueError):
                    pass
        processed_counts[msg_id] = len(entries)
    return delta, new_entries, latest_stock, latest_message


def _grid_snapshot(grid: OhGrid) -> List[List[str]]:
    return [[cell.emoji for cell in row] for row in grid.cells]


def _resolve_cell(grid: OhGrid, row: int, col: int) -> Optional[Any]:
    if row < 0 or col < 0:
        return None
    if row >= grid.rows or col >= grid.cols:
        return None
    return grid.cells[row][col]


def _update_revealed_positions(
    grid: OhGrid,
    revealed_positions: Dict[Tuple[int, int], str],
    color_counts_delta: Dict[str, int],
) -> None:
    for cell in grid.iter_cells():
        if cell.color == "HIDDEN":
            continue
        key = (cell.row, cell.col)
        if key in revealed_positions:
            continue
        revealed_positions[key] = cell.color
        _increment_count(color_counts_delta, cell.color, 1)


def _try_parse_grid(message: Optional[Dict[str, Any]], cfg) -> Optional[OhGrid]:
    if not message:
        return None
    grid = parse_oh_message(message, cfg)
    if grid:
        return grid
    # Fallback: some responses might return components as list directly
    components = message.get("components", [])
    if isinstance(components, list) and components and isinstance(components[0], list):
        # Normalize list-of-list to action row dicts
        normalized = []
        for row in components:
            if isinstance(row, list):
                normalized.append({"components": row})
        message = dict(message)
        message["components"] = normalized
        return parse_oh_message(message, cfg)
    return None


def _log_parse_failure(message: Optional[Dict[str, Any]], label: str) -> None:
    if not message:
        _log_event({"type": "warn", "message": f"{label}: empty message", "source": "Oh_bot"})
        return
    components = message.get("components", None)
    comp_len = len(components) if isinstance(components, list) else None
    diagnosis = diagnose_oh_message(message)
    _log_event({
        "type": "warn",
        "message": f"{label}: parse grid failed",
        "message_id": str(message.get("id", "")),
        "components_len": comp_len,
        "content": str(message.get("content", ""))[:200],
        "diagnosis": diagnosis,
        "source": "Oh_bot",
    })

def _dynamic_samples(base: int, unknown_count: int) -> int:
    try:
        base_val = int(base)
    except (TypeError, ValueError):
        base_val = 1
    base_val = max(1, base_val)
    if unknown_count >= 20:
        return max(4, min(base_val, 6))
    if unknown_count >= 15:
        return max(6, min(base_val, 10))
    if unknown_count >= 10:
        return max(8, min(base_val, 16))
    if unknown_count >= 6:
        return max(12, min(base_val, 32))
    return base_val



def _safe_response_json(response: Optional[requests.Response]) -> Optional[Any]:
    if response is None:
        return None
    try:
        return response.json()
    except Exception:
        return None


def _truncate_text(text: Optional[str], max_len: int) -> Optional[str]:
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...(truncated {len(text) - max_len} chars)"


def _serialize_full_response(payload: Any, response: Optional[requests.Response]) -> Optional[str]:
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
    return _truncate_text(text, 100_000)


def _extract_error_fields(payload: Any) -> Tuple[Optional[str], Optional[Any]]:
    if isinstance(payload, dict):
        return payload.get("message"), payload.get("code")
    return (None, None)


def _log_refresh_error(
    label: str,
    response: Optional[requests.Response],
    payload: Any,
    message_id: str,
    refresh_source: str,
    attempt: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    error_message, error_code = _extract_error_fields(payload)
    response_snippet = None
    if response is not None:
        try:
            response_snippet = _truncate_text(response.text, 500)
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
        "full_response": _serialize_full_response(payload, response),
        "source": "Oh_bot",
    }
    if extra:
        entry.update(extra)
    _log_event(entry)


def _fetch_oh_message_snapshot(
    url: str,
    base_url: str,
    auth: Dict[str, str],
    message_id: str,
    attempts: int = 3,
    delay_sec: float = 0.5,
) -> Optional[Dict[str, Any]]:
    schedule = Latency.get_delay_schedule(delay_sec, attempts)
    effective_attempts = max(max(1, attempts), len(schedule) if schedule else 0)
    for attempt in range(1, effective_attempts + 1):
        # Try list fetch first
        resp_list, messages = Fetch.fetch_messages(url, auth, limit=50)
        payload_list = _safe_response_json(resp_list)

        if resp_list is None:
            Latency.record_poll_result(None, error=True, context="oh_snapshot_list")
            _log_refresh_error(
                "refresh_error: list fetch no response",
                resp_list,
                payload_list,
                message_id,
                "list",
                attempt,
            )
        elif resp_list.status_code != 200:
            _log_refresh_error(
                "refresh_error: list fetch status",
                resp_list,
                payload_list,
                message_id,
                "list",
                attempt,
            )
            Latency.record_poll_result(resp_list.status_code, context="oh_snapshot_list")
        else:
            # status 200
            found = None
            for msg in messages:
                if str(msg.get("id", "")) == message_id:
                    found = msg
                    break
            if found:
                Latency.record_poll_result(resp_list.status_code, context="oh_snapshot_list")
                return found
            _log_refresh_error(
                "refresh_error: message not found in list",
                resp_list,
                {"list_len": len(messages), "sample_ids": [m.get("id") for m in messages[:5]]},
                message_id,
                "list",
                attempt,
            )
            Latency.record_poll_result(resp_list.status_code, context="oh_snapshot_list")

        # fallback to by-id
        resp_msg, msg = Fetch.fetch_message_by_id(base_url, auth, Vars.channelId, message_id)
        payload_msg = msg if msg is not None else _safe_response_json(resp_msg)
        if resp_msg is None:
            Latency.record_poll_result(None, error=True, context="oh_snapshot_by_id")
            _log_refresh_error(
                "refresh_error: by_id no response",
                resp_msg,
                payload_msg,
                message_id,
                "by_id",
                attempt,
            )
        elif resp_msg.status_code != 200:
            _log_refresh_error(
                "refresh_error: by_id status",
                resp_msg,
                payload_msg,
                message_id,
                "by_id",
                attempt,
            )
            Latency.record_poll_result(resp_msg.status_code, context="oh_snapshot_by_id")
        else:
            if isinstance(msg, dict) and str(msg.get("id", "")) == message_id:
                Latency.record_poll_result(resp_msg.status_code, context="oh_snapshot_by_id")
                return msg
            _log_refresh_error(
                "refresh_error: by_id payload mismatch",
                resp_msg,
                payload_msg,
                message_id,
                "by_id",
                attempt,
            )
            Latency.record_poll_result(resp_msg.status_code, context="oh_snapshot_by_id")

        # retry with backoff / retry-after
        retry_after = None
        if resp_list is not None and resp_list.status_code == 429:
            retry_after = Fetch._parse_retry_after(resp_list)
        if resp_msg is not None and resp_msg.status_code == 429:
            retry_after = Fetch._parse_retry_after(resp_msg)

        if retry_after is not None:
            time.sleep(max(retry_after, _poll_delay(attempt - 1, delay_sec, schedule, legacy_scale=1.5, legacy_cap=2.0)))
        else:
            time.sleep(_poll_delay(attempt - 1, delay_sec, schedule, legacy_scale=1.5, legacy_cap=2.0))

    return None


def run_session(token: str, user_name: Optional[str] = None, quiet: bool = False) -> Dict[str, Any]:
    emit = _make_emitter(quiet)
    cfg = load_oh_config()
    stats_path = default_stats_path()

    resolved_name = user_name or "Unknown"

    oh_message_id = ""
    grid: Optional[OhGrid] = None
    max_clicks = 5
    time_limit_sec = cfg.time_limit_sec
    clicks_used = 0
    revealed_positions: Dict[Tuple[int, int], str] = {}
    color_counts_delta: Dict[str, int] = {}
    reward_processed_counts: Dict[str, int] = {}
    session_total = 0
    last_stock: Optional[int] = None
    session_started = False
    session_end_logged = False

    try:
        bot = discum.Client(token=token, log=False)
        auth = {"authorization": token}
        url = _message_url()
        base_url = _base_url()
        command_sent_at: Optional[float] = None

        # Get last message id before issuing $oh
        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        before_id = Fetch.get_latest_message_id(pre_messages)

        # Send $oh
        try:
            command_sent_at = time.perf_counter()
            r = requests.post(url, headers=auth, data={"content": "$oh"}, timeout=Fetch.get_timeout())
            _emit_latency("command_sent", action="oh_call")
            _log_event({
                "type": "command",
                "command": "$oh",
                "status_code": r.status_code if r is not None else None,
                "user": resolved_name,
                "channel_id": Vars.channelId,
                "guild_id": Vars.serverId,
                "source": "Oh_bot",
            })
            try:
                payload = r.json() if r is not None else None
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("id"):
                before_id = str(payload.get("id"))
        except Exception as exc:
            _log_event({
                "type": "error",
                "error": f"Failed to send $oh: {exc}",
                "source": "Oh_bot",
            })
            emit(f"Failed to send $oh: {exc}", "ERROR")
            return {"status": "error", "error": str(exc)}

        # Wait for oh message
        _, oh_message, cooldown_msg, cooldown_wait = _wait_for_oh_message(url, auth, after_id=before_id)
        if cooldown_msg:
            _log_event({
                "type": "cooldown",
                "message": "You don't have enough $oh for today",
                "wait": cooldown_wait,
                "message_id": str(cooldown_msg.get("id", "")),
                "source": "Oh_bot",
            })
            if cooldown_wait:
                emit(f"You don't have enough $oh for today. Time to wait before the refill: {cooldown_wait}", "WARN")
            else:
                emit("You don't have enough $oh for today.", "WARN")
            return {"status": "cooldown", "wait": cooldown_wait}
        if not oh_message:
            _log_event({
                "type": "error",
                "error": "No $oh response detected",
                "source": "Oh_bot",
            })
            emit("No $oh response detected.", "ERROR")
            return {"status": "error", "error": "No $oh response detected"}
        _emit_latency(
            "message_detected",
            action="oh_message",
            response_ms=int((time.perf_counter() - command_sent_at) * 1000.0) if command_sent_at else None,
            detect_lag_ms=_message_lag_ms(oh_message),
        )

        oh_message_id = str(oh_message.get("id", ""))
        grid = _try_parse_grid(oh_message, cfg)
        if not grid:
            _log_event({
                "type": "error",
                "error": "Failed to parse $oh grid",
                "message_id": oh_message_id,
                "source": "Oh_bot",
            })
            emit("Failed to parse $oh grid.", "ERROR")
            return {"status": "error", "error": "Failed to parse $oh grid"}

        default_clicks = 5
        max_clicks, time_limit_sec = _parse_clicks_and_time(
            oh_message.get("content", ""),
            default_clicks,
            cfg.time_limit_sec,
        )

        start_time = time.time()

        _log_event({
            "type": "session_start",
            "user": resolved_name,
            "channel_id": Vars.channelId,
            "guild_id": Vars.serverId,
            "message_id": oh_message_id,
            "max_clicks": max_clicks,
            "time_limit_sec": time_limit_sec,
            "grid": _grid_snapshot(grid),
            "source": "Oh_bot",
        })
        session_started = True

        solver = OhSolver(
            priors=cfg.priors,
            expected_values=cfg.expected_values,
            reveal_counts=cfg.reveal_counts,
            monte_carlo_samples=cfg.monte_carlo_samples,
        )

        # initial revealed tracking (count initial visible tiles)
        _update_revealed_positions(grid, revealed_positions, color_counts_delta)

        refresh_fail_streak = 0
        while clicks_used < max_clicks:
            if time.time() - start_time > time_limit_sec:
                emit("Time limit reached.", "WARN")
                break

            # refresh grid (robust)
            oh_message = _fetch_oh_message_snapshot(url, base_url, auth, oh_message_id)
            if not oh_message:
                refresh_fail_streak += 1
                emit("Failed to refresh $oh message. Retrying...", "WARN")
                if refresh_fail_streak >= 3:
                    emit("Refresh failed too many times. Ending session.", "WARN")
                    break
                _post_action_pause(0.5)
                continue
            refresh_fail_streak = 0
            prev_grid = grid
            grid = _try_parse_grid(oh_message, cfg)
            if not grid:
                # attempt to wait for update and retry
                _log_parse_failure(oh_message, "refresh")
                _, updated_message, _ = Fetch.wait_for_message_update(
                    base_url,
                    auth,
                    Vars.channelId,
                    oh_message_id,
                    prev_hash=_message_hash(oh_message),
                    attempts=3,
                    delay_sec=0.5,
                    latency_context="oh_message_update",
                )
                grid = _try_parse_grid(updated_message, cfg) if updated_message else None
            if not grid:
                # attempt to fetch recent messages and match by id
                _, recent = Fetch.fetch_messages(url, auth, limit=20)
                for msg in recent:
                    if str(msg.get("id", "")) == oh_message_id:
                        grid = _try_parse_grid(msg, cfg)
                        break
            if not grid:
                emit("Failed to parse updated grid. Using last known grid.", "WARN")
                grid = prev_grid

            # track new reveals (auto)
            _update_revealed_positions(grid, revealed_positions, color_counts_delta)

            summary = summarize_grid(grid, hidden_color="HIDDEN")
            unknown_positions = summary["unknown_positions"]
            known_positions = summary["known_positions"]
            unknown_count = summary["unknown_count"]
            clickable_positions = summary["clickable_positions"]

            solver.samples = _dynamic_samples(cfg.monte_carlo_samples, unknown_count)

            if unknown_count <= 0 and not known_positions:
                emit("No clickable tiles left.", "WARN")
                break

            # build known values list for solver
            known_values: List[float] = []
            known_map: Dict[Tuple[int, int], float] = {}
            for r, c in known_positions:
                cell = _resolve_cell(grid, r, c)
                if not cell:
                    continue
                val = cfg.expected_values.get(cell.color, 0.0)
                known_values.append(val)
                known_map[(r, c)] = val

            try:
                action, ev_exploit, ev_explore = solver.choose_action(
                    max_clicks - clicks_used,
                    unknown_count,
                    known_values,
                )
            except Exception as exc:
                _log_event({
                    "type": "warn",
                    "message": f"choose_action failed: {exc}",
                    "message_id": oh_message_id,
                    "source": "Oh_bot",
                })
                action, ev_exploit, ev_explore = ("explore", -1.0, -1.0)

            if action == "exploit" and not known_positions and unknown_positions:
                action = "explore"
            if action == "explore" and not unknown_positions and known_positions:
                action = "exploit"

            # choose actual position
            if action == "exploit" and known_positions:
                best_pos = max(known_positions, key=lambda pos: (known_map.get(pos, 0.0), -pos[0], -pos[1]))
            else:
                # explore: pick the first unknown position (row-major)
                best_pos = sorted(unknown_positions, key=lambda pos: (pos[0], pos[1]))[0]

            target_cell = _resolve_cell(grid, best_pos[0], best_pos[1])
            if (not target_cell or not target_cell.custom_id) and clickable_positions:
                fallback_pos = clickable_positions[0]
                target_cell = _resolve_cell(grid, fallback_pos[0], fallback_pos[1])
                best_pos = fallback_pos
            if not target_cell or not target_cell.custom_id:
                _log_event({
                    "type": "warn",
                    "message": "Target cell not clickable",
                    "message_id": oh_message_id,
                    "clickable_count": len(clickable_positions),
                    "unknown_count": unknown_count,
                    "known_count": len(known_positions),
                    "source": "Oh_bot",
                })
                emit("Target cell not clickable.", "WARN")
                break

            reward_msg = _fetch_latest_reward_message(url, auth)
            reward_msg_id_before = str(reward_msg.get("id")) if reward_msg else None

            # click
            click_sent_at = time.perf_counter()
            _emit_latency(
                "action_sent",
                action="oh_click",
                click_index=clicks_used + 1,
                row=best_pos[0],
                col=best_pos[1],
            )
            bot.click(
                Vars.MUDAE_BOT_ID,
                channelID=Vars.channelId,
                guildID=Vars.serverId,
                messageID=oh_message_id,
                messageFlags=int(oh_message.get("flags", 0) or 0),
                data={"component_type": 2, "custom_id": target_cell.custom_id},
            )

            # refresh after click
            _post_action_pause(0.3)
            updated_message = _fetch_oh_message_snapshot(url, base_url, auth, oh_message_id)
            if updated_message:
                oh_message = updated_message
                parsed = _try_parse_grid(oh_message, cfg)
                if parsed:
                    grid = parsed
                else:
                    _log_parse_failure(oh_message, "post-click")

            # track reveals from this click
            _update_revealed_positions(grid, revealed_positions, color_counts_delta)

            # determine observed color
            observed_color = "UNKNOWN"
            updated_cell = _resolve_cell(grid, best_pos[0], best_pos[1]) if grid else None
            if updated_cell:
                if updated_cell.color != "HIDDEN":
                    observed_color = updated_cell.color
                elif not updated_cell.clickable:
                    observed_color = "HIDDEN"

            # scan latest reward message for color resolution
            latest_reward_msg = _fetch_latest_reward_message(url, auth)
            reward_info = _parse_reward_content(
                latest_reward_msg.get("content", "") if latest_reward_msg else "",
                cfg.emoji_map,
            )

            resolved_color = reward_info.get("resolved_color") or reward_info.get("reward_color")
            if resolved_color and observed_color != resolved_color and updated_cell:
                # adjust counts if we already counted observed reveal
                if (updated_cell.row, updated_cell.col) in revealed_positions:
                    _increment_count(color_counts_delta, observed_color, -1)
                _increment_count(color_counts_delta, resolved_color, 1)
            else:
                # If clicked cell is hidden (no reveal), count it directly.
                if observed_color == "HIDDEN":
                    _increment_count(color_counts_delta, observed_color, 1)

            # collect exact reward amounts from Mudae response
            reward_delta = 0
            reward_lines: List[Dict[str, Any]] = []
            reward_stock: Optional[int] = None
            max_reward_attempts = 4
            need_stock = (clicks_used + 1) >= max_clicks
            for attempt in range(max_reward_attempts):
                reward_messages = _fetch_reward_messages(url, auth, limit=15)
                delta, new_entries, stock, latest_msg = _collect_reward_delta(
                    reward_messages,
                    reward_processed_counts,
                )
                if delta:
                    reward_delta += delta
                    reward_lines.extend(new_entries)
                if stock is not None:
                    last_stock = stock
                    reward_stock = stock
                if new_entries and (not need_stock or reward_stock is not None):
                    break
                if need_stock and reward_stock is not None:
                    break
                _post_action_pause(0.4)
            if reward_delta:
                session_total += reward_delta
            if reward_delta == 0 and not reward_lines:
                _log_event({
                    "type": "warn",
                    "message": "reward not detected",
                    "message_id": oh_message_id,
                    "click_index": clicks_used + 1,
                    "source": "Oh_bot",
                })
            _emit_latency(
                "action_ack",
                action="oh_click",
                click_index=clicks_used + 1,
                response_ms=int((time.perf_counter() - click_sent_at) * 1000.0),
                detect_lag_ms=_message_lag_ms(oh_message),
                reward_delta=reward_delta,
            )

            _log_event({
                "type": "click",
                "click_index": clicks_used + 1,
                "row": best_pos[0],
                "col": best_pos[1],
                "emoji": grid.cells[best_pos[0]][best_pos[1]].emoji if grid else None,
                "observed_color": observed_color,
                "action": action,
                "ev_exploit": ev_exploit,
                "ev_explore": ev_explore,
                "reward_gained": reward_delta,
                "reward_lines": reward_lines,
                "session_total": session_total,
                "stock": reward_stock,
                "source": "Oh_bot",
            })

            clicks_used += 1

            if not quiet:
                reward_text = f"+{reward_delta}" if reward_delta else "+0"
                stock_text = f" | Stock {reward_stock}" if reward_stock is not None else ""
                emit(
                    f"Click {clicks_used}/{max_clicks}: ({best_pos[0]+1},{best_pos[1]+1}) "
                    f"observed={observed_color} | gain {reward_text} | total {session_total}{stock_text}"
                )

        _log_event({
            "type": "session_end",
            "user": resolved_name,
            "message_id": oh_message_id,
            "clicks_used": clicks_used,
            "total_reward": session_total,
            "color_counts_delta": color_counts_delta,
            "final_stock": last_stock,
            "source": "Oh_bot",
        })
        session_end_logged = True

        if not quiet:
            if last_stock is not None:
                emit(f"Session complete. Total received: {session_total} | Stock: {last_stock}")
            else:
                emit(f"Session complete. Total received: {session_total}")

        update_stats(stats_path, color_counts_delta)

        return {
            "status": "ok",
            "clicks_used": clicks_used,
            "total_reward": session_total,
            "final_stock": last_stock,
            "message_id": oh_message_id,
        }
    except Exception as exc:
        _log_event({
            "type": "error",
            "error": f"Unhandled exception: {exc}",
            "message_id": oh_message_id,
            "source": "Oh_bot",
        })
        emit(f"Oh_bot failed: {exc}", "ERROR")
        if session_started and not session_end_logged:
            _log_event({
                "type": "session_end",
                "user": resolved_name,
                "message_id": oh_message_id,
                "clicks_used": clicks_used,
                "total_reward": session_total,
                "color_counts_delta": color_counts_delta,
                "final_stock": last_stock,
                "source": "Oh_bot",
            })
        return {"status": "error", "error": str(exc)}

def main() -> None:
    print("\n" + "=" * 50)
    print("Ouro Harvest ($oh) Auto-Player")
    print("=" * 50)

    selected_user = _select_user()
    run_session(selected_user["token"], selected_user["name"], quiet=False)


if __name__ == "__main__":
    main()













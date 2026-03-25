"""
Oc_bot.py - Standalone Ouro Chest ($oc) auto-player.
Selects user, sends $oc, chooses clicks via existing OC solver, and logs results.
"""

import os
import re
import sys
import time
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set

import requests

from mudae.config import vars as Vars
from mudae.core import latency as Latency
from mudae.discord import fetch as Fetch
from mudae.paths import LOGS_DIR, ensure_runtime_dirs
from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file
from mudae.storage.latency_metrics import record_event as record_latency_event
from mudae.ouro import Oc_interactive_solver as oc_solver
from mudae.ouro.Oc_interactive_solver import InteractiveSolver, SphereColor
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
LOG_FILE = os.fspath(LOGS_DIR / "OuroChest.json")

OC_COOLDOWN_RE = re.compile(
    r"You don't have enough \$oc.*?Time to wait before the refill:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
ACTION_LEASE_TTL_SEC = 180.0
ACTION_LEASE_HEARTBEAT_SEC = 20.0


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


@dataclass
class OcCell:
    row: int
    col: int
    emoji: str
    custom_id: Optional[str]
    disabled: bool
    clickable: bool


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
    payload.setdefault("flow", "oc")
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


def _acquire_action_lease(token: str, user_name: str):
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
        "source": "Oc_bot",
    }
    if extra:
        entry.update(extra)
    _log_event(entry)


def _fetch_oc_message_snapshot(
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
        resp_list, messages = Fetch.fetch_messages(url, auth, limit=50)
        payload_list = _safe_response_json(resp_list)

        if resp_list is None:
            Latency.record_poll_result(None, error=True, context="oc_snapshot_list")
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
            Latency.record_poll_result(resp_list.status_code, context="oc_snapshot_list")
        else:
            found = None
            for msg in messages:
                if str(msg.get("id", "")) == message_id:
                    found = msg
                    break
            if found:
                Latency.record_poll_result(resp_list.status_code, context="oc_snapshot_list")
                return found
            _log_refresh_error(
                "refresh_error: message not found in list",
                resp_list,
                {"list_len": len(messages), "sample_ids": [m.get("id") for m in messages[:5]]},
                message_id,
                "list",
                attempt,
            )
            Latency.record_poll_result(resp_list.status_code, context="oc_snapshot_list")

        resp_msg, msg = Fetch.fetch_message_by_id(base_url, auth, Vars.channelId, message_id)
        payload_msg = msg if msg is not None else _safe_response_json(resp_msg)
        if resp_msg is None:
            Latency.record_poll_result(None, error=True, context="oc_snapshot_by_id")
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
            Latency.record_poll_result(resp_msg.status_code, context="oc_snapshot_by_id")
        else:
            if isinstance(msg, dict) and str(msg.get("id", "")) == message_id:
                Latency.record_poll_result(resp_msg.status_code, context="oc_snapshot_by_id")
                return msg
            _log_refresh_error(
                "refresh_error: by_id payload mismatch",
                resp_msg,
                payload_msg,
                message_id,
                "by_id",
                attempt,
            )
            Latency.record_poll_result(resp_msg.status_code, context="oc_snapshot_by_id")

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
) -> Tuple[int, List[Dict[str, Any]], Optional[int]]:
    if not messages:
        return 0, [], None
    sorted_messages = sorted(messages, key=_message_id_int)
    delta = 0
    new_entries: List[Dict[str, Any]] = []
    latest_stock: Optional[int] = None
    for msg in sorted_messages:
        msg_id = str(msg.get("id", ""))
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = ""
        entries, stock = parse_reward_message(content)
        if stock is not None:
            latest_stock = stock
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
    return delta, new_entries, latest_stock


def _get_emoji_name(component: Dict[str, Any]) -> str:
    emoji = component.get("emoji")
    if isinstance(emoji, dict):
        name = emoji.get("name")
        if isinstance(name, str):
            return name
    return ""


def _parse_grid(message: Dict[str, Any]) -> Optional[List[List[OcCell]]]:
    components = message.get("components", [])
    if not isinstance(components, list) or not components:
        return None

    rows: List[List[OcCell]] = []
    for row_idx, row in enumerate(components):
        row_components: List[Any]
        if isinstance(row, dict):
            row_components = row.get("components", [])
        elif isinstance(row, list):
            row_components = row
        else:
            continue
        if not isinstance(row_components, list):
            continue
        row_cells: List[OcCell] = []
        for col_idx, component in enumerate(row_components):
            if not isinstance(component, dict):
                continue
            emoji_name = _get_emoji_name(component)
            custom_id = component.get("custom_id")
            disabled = bool(component.get("disabled", False))
            clickable = (not disabled) and bool(custom_id)
            row_cells.append(
                OcCell(
                    row=row_idx,
                    col=col_idx,
                    emoji=emoji_name,
                    custom_id=str(custom_id) if custom_id else None,
                    disabled=disabled,
                    clickable=clickable,
                )
            )
        if row_cells:
            rows.append(row_cells)

    if not rows:
        return None
    if len(rows) != 5 or any(len(row) != 5 for row in rows):
        return None
    return rows


def _grid_snapshot(grid: List[List[OcCell]]) -> List[List[str]]:
    return [[cell.emoji for cell in row] for row in grid]


def _find_oc_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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
        content = msg.get("content", "")
        if "red sphere" in content.lower() or "never at the center" in content.lower():
            return msg
        if "You can click" in content and "Blue spheres unveil" not in content:
            candidates.append(msg)
    return candidates[0] if candidates else None


def _find_oc_cooldown_message(messages: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], Optional[str]]]:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or "$oc" not in content:
            continue
        if "don't have enough $oc" in content:
            match = OC_COOLDOWN_RE.search(content)
            wait_text = match.group(1).strip() if match else None
            return (msg, wait_text)
    return None


def _wait_for_oc_message(
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
            Latency.record_poll_result(None, error=True, context="oc_wait_message")
            time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
            continue
        cooldown = _find_oc_cooldown_message(messages)
        if cooldown:
            Latency.record_poll_result(response.status_code, context="oc_wait_message")
            return response, None, cooldown[0], cooldown[1]
        oc_msg = _find_oc_message(messages)
        if oc_msg:
            Latency.record_poll_result(response.status_code, context="oc_wait_message")
            return response, oc_msg, None, None
        newest = Fetch.get_latest_message_id(messages)
        if newest and newest != base_after:
            base_after = newest
        retry_after = Fetch._parse_retry_after(response)
        Latency.record_poll_result(response.status_code, retry_after=retry_after, context="oc_wait_message")
        if retry_after is not None:
            time.sleep(max(retry_after, _poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0)))
            continue
        time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
    return last_response, None, None, None


def _map_emoji_to_color(name: str) -> Optional[SphereColor]:
    mapping = {
        "spB": SphereColor.BLUE,
        "spT": SphereColor.TEAL,
        "spG": SphereColor.GREEN,
        "spY": SphereColor.YELLOW,
        "spO": SphereColor.ORANGE,
        "spR": SphereColor.RED,
        "sp": SphereColor.RED,
    }
    return mapping.get(name)


def run_session(token: str, user_name: Optional[str] = None, quiet: bool = False) -> Dict[str, Any]:
    emit = _make_emitter(quiet)
    resolved_name = user_name or "Unknown"
    lease = _acquire_action_lease(token, resolved_name)
    if not lease.acquired:
        _log_event({
            "type": "busy",
            "message": "Skipped $oc because another same-account action is already running",
            "user": resolved_name,
            "channel_id": Vars.channelId,
            "guild_id": Vars.serverId,
            "source": "Oc_bot",
        })
        emit("Skipped $oc because another same-account action is already running.", "WARN")
        return {"status": "busy", "reason": "same-account action already in progress"}
    try:
        bot = discum.Client(token=token, log=False)
        auth = {"authorization": token}
        url = _message_url()
        base_url = _base_url()
        command_sent_at: Optional[float] = None

        _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
        before_id = Fetch.get_latest_message_id(pre_messages)

        try:
            command_sent_at = time.perf_counter()
            r = requests.post(url, headers=auth, data={"content": "$oc"}, timeout=Fetch.get_timeout())
            _emit_latency("command_sent", action="oc_call")
            _log_event({
                "type": "command",
                "command": "$oc",
                "status_code": r.status_code if r is not None else None,
                "user": resolved_name,
                "channel_id": Vars.channelId,
                "guild_id": Vars.serverId,
                "source": "Oc_bot",
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
                "error": f"Failed to send $oc: {exc}",
                "source": "Oc_bot",
            })
            emit(f"Failed to send $oc: {exc}", "ERROR")
            return {"status": "error", "error": str(exc)}

        _, oc_message, cooldown_msg, cooldown_wait = _wait_for_oc_message(url, auth, after_id=before_id)
        if cooldown_msg:
            _log_event({
                "type": "cooldown",
                "message": "You don't have enough $oc for today",
                "wait": cooldown_wait,
                "message_id": str(cooldown_msg.get("id", "")),
                "source": "Oc_bot",
            })
            if cooldown_wait:
                emit(f"You don't have enough $oc for today. Time to wait before the refill: {cooldown_wait}", "WARN")
            else:
                emit("You don't have enough $oc for today.", "WARN")
            return {"status": "cooldown", "wait": cooldown_wait}
        if not oc_message:
            _log_event({
                "type": "error",
                "error": "No $oc response detected",
                "source": "Oc_bot",
            })
            emit("No $oc response detected.", "ERROR")
            return {"status": "error", "error": "No $oc response detected"}
        _emit_latency(
            "message_detected",
            action="oc_message",
            response_ms=int((time.perf_counter() - command_sent_at) * 1000.0) if command_sent_at else None,
            detect_lag_ms=_message_lag_ms(oc_message),
        )

        oc_message_id = str(oc_message.get("id", ""))
        grid = _parse_grid(oc_message)
        if not grid:
            _log_event({
                "type": "error",
                "error": "Failed to parse $oc grid",
                "message_id": oc_message_id,
                "source": "Oc_bot",
            })
            emit("Failed to parse $oc grid.", "ERROR")
            return {"status": "error", "error": "Failed to parse $oc grid"}

        max_clicks, time_limit_sec = _parse_clicks_and_time(
            oc_message.get("content", ""),
            5,
            120,
        )

        _log_event({
            "type": "session_start",
            "user": resolved_name,
            "channel_id": Vars.channelId,
            "guild_id": Vars.serverId,
            "message_id": oc_message_id,
            "max_clicks": max_clicks,
            "time_limit_sec": time_limit_sec,
            "grid": _grid_snapshot(grid),
            "source": "Oc_bot",
        })

        try:
            if quiet:
                oc_solver.PARALLEL_EVAL = False
                oc_solver.PARALLEL_WORKERS = 0
            if quiet:
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    solver = InteractiveSolver()
            else:
                solver = InteractiveSolver()
        except Exception as exc:
            _log_event({
                "type": "error",
                "error": f"Failed to initialize solver: {exc}",
                "source": "Oc_bot",
            })
            emit(f"Failed to initialize solver: {exc}", "ERROR")
            return {"status": "error", "error": str(exc)}

        revealed_map: Dict[Tuple[int, int], SphereColor] = {}
        clicked_map: Set[Tuple[int, int]] = set()
        reward_processed_counts: Dict[str, int] = {}
        session_total = 0
        last_stock: Optional[int] = None
        start_time = time.time()
        clicks_used = 0

        def _update_solver_from_grid(current_grid: List[List[OcCell]]) -> None:
            for row in current_grid:
                for cell in row:
                    if cell.emoji == "spU":
                        continue
                    color = _map_emoji_to_color(cell.emoji)
                    if color is None:
                        _log_event({
                            "type": "warn",
                            "message": "Unknown emoji in grid",
                            "emoji": cell.emoji,
                            "row": cell.row,
                            "col": cell.col,
                            "source": "Oc_bot",
                        })
                        continue
                    key = (cell.row, cell.col)
                    if key not in revealed_map:
                        revealed_map[key] = color
                        solver.observed[(cell.row, cell.col)] = color
                        solver.bayes_update((cell.row, cell.col), color)
                        if color == SphereColor.RED:
                            solver.red_found = True
                            solver.red_pos = key
                    if not cell.clickable:
                        if key not in clicked_map:
                            clicked_map.add(key)
                            solver.revealed[cell.row][cell.col] = True
            # clicks_used should be driven by actual clicks, not revealed count
            solver.clicks_used = clicks_used

        _update_solver_from_grid(grid)

        refresh_fail_streak = 0
        while clicks_used < max_clicks:
            if time.time() - start_time > time_limit_sec:
                emit("Time limit reached.", "WARN")
                break

            oc_message = _fetch_oc_message_snapshot(url, base_url, auth, oc_message_id)
            if not oc_message:
                refresh_fail_streak += 1
                emit("Failed to refresh $oc message. Retrying...", "WARN")
                if refresh_fail_streak >= 3:
                    emit("Refresh failed too many times. Ending session.", "WARN")
                    break
                _post_action_pause(0.5)
                continue
            refresh_fail_streak = 0

            parsed_grid = _parse_grid(oc_message)
            if parsed_grid:
                grid = parsed_grid
            else:
                _log_event({
                    "type": "warn",
                    "message": "refresh: parse grid failed",
                    "message_id": oc_message_id,
                    "source": "Oc_bot",
                })
            _update_solver_from_grid(grid)

            if clicks_used >= max_clicks:
                break

            suggestion = solver.pick_click_ev()
            if suggestion is None:
                break
            target_r, target_c = suggestion

            # fallback if suggested cell not clickable
            target_cell = None
            if 0 <= target_r < len(grid) and 0 <= target_c < len(grid[0]):
                candidate = grid[target_r][target_c]
                if candidate.clickable:
                    target_cell = candidate

            if target_cell is None:
                clickable_cells = [cell for row in grid for cell in row if cell.clickable]
                if not clickable_cells:
                    emit("No clickable tiles left.", "WARN")
                    break
                target_cell = clickable_cells[0]
                target_r, target_c = target_cell.row, target_cell.col

            # click
            click_sent_at = time.perf_counter()
            _emit_latency(
                "action_sent",
                action="oc_click",
                click_index=clicks_used + 1,
                row=target_r,
                col=target_c,
            )
            bot.click(
                Vars.MUDAE_BOT_ID,
                channelID=Vars.channelId,
                guildID=Vars.serverId,
                messageID=oc_message_id,
                messageFlags=int(oc_message.get("flags", 0) or 0),
                data={"component_type": 2, "custom_id": target_cell.custom_id},
            )
            clicks_used += 1
            solver.clicks_used = clicks_used

            _post_action_pause(0.4)
            oc_message = _fetch_oc_message_snapshot(url, base_url, auth, oc_message_id) or oc_message
            parsed_grid = _parse_grid(oc_message)
            if parsed_grid:
                grid = parsed_grid
            _update_solver_from_grid(grid)

            observed_color = None
            if 0 <= target_r < len(grid) and 0 <= target_c < len(grid[0]):
                observed_color = _map_emoji_to_color(grid[target_r][target_c].emoji)

            # collect exact reward amounts from Mudae response
            reward_delta = 0
            reward_lines: List[Dict[str, Any]] = []
            reward_stock: Optional[int] = None
            max_reward_attempts = 4
            need_stock = clicks_used >= max_clicks
            for attempt in range(max_reward_attempts):
                reward_messages = _fetch_reward_messages(url, auth, limit=15)
                delta, new_entries, stock = _collect_reward_delta(
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
                    "message_id": oc_message_id,
                    "click_index": clicks_used,
                    "source": "Oc_bot",
                })
            _emit_latency(
                "action_ack",
                action="oc_click",
                click_index=clicks_used,
                response_ms=int((time.perf_counter() - click_sent_at) * 1000.0),
                detect_lag_ms=_message_lag_ms(oc_message),
                reward_delta=reward_delta,
            )

            metrics = solver.last_metrics if hasattr(solver, "last_metrics") else None
            p_red = None
            exp_score = None
            if metrics and isinstance(metrics, tuple) and len(metrics) >= 2:
                p_red, exp_score = metrics[0], metrics[1]

            if not quiet:
                reward_text = f"+{reward_delta}" if reward_delta else "+0"
                stock_text = f" | Stock {reward_stock}" if reward_stock is not None else ""
                emit(
                    f"Click {clicks_used}/{max_clicks}: ({target_r+1},{target_c+1}) "
                    f"observed={observed_color.name if observed_color else None} | gain {reward_text} "
                    f"| total {session_total}{stock_text}"
                )

            _log_event({
                "type": "click",
                "click_index": clicks_used,
                "row": target_r,
                "col": target_c,
                "emoji": grid[target_r][target_c].emoji if grid else None,
                "observed_color": observed_color.name if observed_color else None,
                "p_red": p_red,
                "expected_score": exp_score,
                "reward_gained": reward_delta,
                "reward_lines": reward_lines,
                "session_total": session_total,
                "stock": reward_stock,
                "source": "Oc_bot",
            })

        _log_event({
            "type": "session_end",
            "user": resolved_name,
            "message_id": oc_message_id,
            "clicks_used": clicks_used,
            "red_found": solver.red_found,
            "observed_colors": {f"{r},{c}": col.name for (r, c), col in revealed_map.items()},
            "total_reward": session_total,
            "final_stock": last_stock,
            "source": "Oc_bot",
        })

        if not quiet:
            if last_stock is not None:
                emit(f"Session complete. Total received: {session_total} | Stock: {last_stock}")
            else:
                emit(f"Session complete. Total received: {session_total}")

        return {
            "status": "ok",
            "clicks_used": clicks_used,
            "total_reward": session_total,
            "final_stock": last_stock,
            "message_id": oc_message_id,
        }
    except Exception as exc:
        _log_event({
            "type": "error",
            "error": f"Unhandled exception: {exc}",
            "source": "Oc_bot",
        })
        emit(f"Oc_bot failed: {exc}", "ERROR")
        return {"status": "error", "error": str(exc)}
    finally:
        lease.release()


def main() -> None:
    print("\n" + "=" * 50)
    print("Ouro Chest ($oc) Auto-Player")
    print("=" * 50)

    selected_user = _select_user()
    run_session(selected_user["token"], selected_user["name"], quiet=False)


if __name__ == "__main__":
    main()








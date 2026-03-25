"""
Oq_bot.py - Standalone Ouro Quest ($oq) auto-player.
Selects user, sends $oq, chooses clicks via existing OQ solver, and logs results.
"""

import os
import re
import sys
import time
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from mudae.config import vars as Vars
from mudae.core import latency as Latency
from mudae.discord import fetch as Fetch
from mudae.paths import LOGS_DIR, OURO_CACHE_DIR, ensure_runtime_dirs
from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file
from mudae.storage.latency_metrics import record_event as record_latency_event
from mudae.ouro.Oq_solver import (
    OQ_BEAM_K_DEFAULT,
    OQ_DEFAULT_CACHE_MAX_GB,
    OqSolver,
    idx_to_rc,
    rc_to_idx,
    TARGET_PURPLES,
    OBS_BLUE,
    OBS_TEAL,
    OBS_GREEN,
    OBS_YELLOW,
    OBS_ORANGE,
    OBS_PURPLE,
)

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
LOG_FILE = os.fspath(LOGS_DIR / "OuroQuest.json")
OQ_EMOJI_LEARNING_FILE = OURO_CACHE_DIR / "oq_emoji_learning.json"
OQ_BASE_OBS_EMOJI_MAP = {
    "spB": OBS_BLUE,
    "spT": OBS_TEAL,
    "spG": OBS_GREEN,
    "spY": OBS_YELLOW,
    "spO": OBS_ORANGE,
    "spP": OBS_PURPLE,
}
OQ_BASE_RED_EMOJIS = {"spR", "sp"}

OQ_COOLDOWN_RE = re.compile(
    r"You don't have enough \$oq.*?Time to wait before the refill:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class OqCell:
    row: int
    col: int
    emoji: str
    custom_id: Optional[str]
    disabled: bool
    clickable: bool


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
    payload.setdefault("flow", "oq")
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
        "source": "Oq_bot",
    }
    if extra:
        entry.update(extra)
    _log_event(entry)


def _fetch_oq_message_snapshot(
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
            Latency.record_poll_result(None, error=True, context="oq_snapshot_list")
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
            Latency.record_poll_result(resp_list.status_code, context="oq_snapshot_list")
        else:
            found = None
            for msg in messages:
                if str(msg.get("id", "")) == message_id:
                    found = msg
                    break
            if found:
                Latency.record_poll_result(resp_list.status_code, context="oq_snapshot_list")
                return found
            _log_refresh_error(
                "refresh_error: message not found in list",
                resp_list,
                {"list_len": len(messages), "sample_ids": [m.get("id") for m in messages[:5]]},
                message_id,
                "list",
                attempt,
            )
            Latency.record_poll_result(resp_list.status_code, context="oq_snapshot_list")

        resp_msg, msg = Fetch.fetch_message_by_id(base_url, auth, Vars.channelId, message_id)
        payload_msg = msg if msg is not None else _safe_response_json(resp_msg)
        if resp_msg is None:
            Latency.record_poll_result(None, error=True, context="oq_snapshot_by_id")
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
            Latency.record_poll_result(resp_msg.status_code, context="oq_snapshot_by_id")
        else:
            if isinstance(msg, dict) and str(msg.get("id", "")) == message_id:
                Latency.record_poll_result(resp_msg.status_code, context="oq_snapshot_by_id")
                return msg
            _log_refresh_error(
                "refresh_error: by_id payload mismatch",
                resp_msg,
                payload_msg,
                message_id,
                "by_id",
                attempt,
            )
            Latency.record_poll_result(resp_msg.status_code, context="oq_snapshot_by_id")

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


def _get_emoji_name(component: Dict[str, Any]) -> str:
    emoji = component.get("emoji")
    if isinstance(emoji, dict):
        name = emoji.get("name")
        if isinstance(name, str):
            return name
    return ""


def _parse_grid(message: Dict[str, Any]) -> Optional[List[List[OqCell]]]:
    components = message.get("components", [])
    if not isinstance(components, list) or not components:
        return None

    rows: List[List[OqCell]] = []
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
        row_cells: List[OqCell] = []
        for col_idx, component in enumerate(row_components):
            if not isinstance(component, dict):
                continue
            emoji_name = _get_emoji_name(component)
            custom_id = component.get("custom_id")
            disabled = bool(component.get("disabled", False))
            clickable = (not disabled) and bool(custom_id)
            row_cells.append(
                OqCell(
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
    return rows


def _grid_snapshot(grid: List[List[OqCell]]) -> List[List[str]]:
    return [[cell.emoji for cell in row] for row in grid]


def _find_oq_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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
        content_lower = content.lower() if isinstance(content, str) else ""
        if "you can click" in content_lower and "purple" in content_lower:
            return msg
        candidates.append(msg)
    return candidates[0] if candidates else None


def _find_oq_cooldown_message(messages: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], Optional[str]]]:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        author = msg.get("author", {})
        if not isinstance(author, dict):
            continue
        if str(author.get("id", "")) != str(Vars.MUDAE_BOT_ID):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or "$oq" not in content:
            continue
        if "don't have enough $oq" in content:
            match = OQ_COOLDOWN_RE.search(content)
            wait_text = match.group(1).strip() if match else None
            return (msg, wait_text)
    return None


def _wait_for_oq_message(
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
            Latency.record_poll_result(None, error=True, context="oq_wait_message")
            time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
            continue
        cooldown = _find_oq_cooldown_message(messages)
        if cooldown:
            Latency.record_poll_result(response.status_code, context="oq_wait_message")
            return response, None, cooldown[0], cooldown[1]
        oq_msg = _find_oq_message(messages)
        if oq_msg:
            Latency.record_poll_result(response.status_code, context="oq_wait_message")
            return response, oq_msg, None, None
        newest = Fetch.get_latest_message_id(messages)
        if newest and newest != base_after:
            base_after = newest
        retry_after = Fetch._parse_retry_after(response)
        Latency.record_poll_result(response.status_code, retry_after=retry_after, context="oq_wait_message")
        if retry_after is not None:
            time.sleep(max(retry_after, _poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0)))
            continue
        time.sleep(_poll_delay(attempt, delay_sec, schedule, legacy_scale=1.3, legacy_cap=2.0))
    return last_response, None, None, None


def _normalize_emoji_aliases(raw: Any) -> Set[str]:
    names: Set[str] = set()
    if raw is None:
        return names
    if isinstance(raw, str):
        items: List[Any] = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return names
    for item in items:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name:
            names.add(name)
    return names


def _coerce_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _load_emoji_learning_state(path: Path) -> Dict[str, Any]:
    fallback = {"version": 1, "higher_than_red_emojis": []}
    try:
        if not path.exists():
            return dict(fallback)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return dict(fallback)
        higher = sorted(_normalize_emoji_aliases(payload.get("higher_than_red_emojis", [])))
        return {"version": 1, "higher_than_red_emojis": higher}
    except Exception:
        return dict(fallback)


def _save_emoji_learning_state(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        to_write = {
            "version": 1,
            "higher_than_red_emojis": sorted(
                _normalize_emoji_aliases(payload.get("higher_than_red_emojis", []))
            ),
        }
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(to_write, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except Exception:
        return


def _map_emoji_to_obs(name: str) -> Optional[int]:
    return OQ_BASE_OBS_EMOJI_MAP.get(name)


def _is_red_emoji(name: str, extra_red_aliases: Optional[Set[str]] = None) -> bool:
    if name in OQ_BASE_RED_EMOJIS:
        return True
    return bool(extra_red_aliases and name in extra_red_aliases)


def _is_higher_than_red_emoji(name: str, higher_aliases: Set[str]) -> bool:
    return bool(name) and name in higher_aliases


def run_session(token: str, user_name: Optional[str] = None, quiet: bool = False) -> Dict[str, Any]:
    emit = _make_emitter(quiet)
    resolved_name = user_name or "Unknown"

    bot = discum.Client(token=token, log=False)
    auth = {"authorization": token}
    url = _message_url()
    base_url = _base_url()
    command_sent_at: Optional[float] = None

    _, pre_messages = Fetch.fetch_messages(url, auth, limit=1)
    before_id = Fetch.get_latest_message_id(pre_messages)

    try:
        command_sent_at = time.perf_counter()
        r = requests.post(url, headers=auth, data={"content": "$oq"}, timeout=Fetch.get_timeout())
        _emit_latency("command_sent", action="oq_call")
        _log_event({
            "type": "command",
            "command": "$oq",
            "status_code": r.status_code if r is not None else None,
            "user": resolved_name,
            "channel_id": Vars.channelId,
            "guild_id": Vars.serverId,
            "source": "Oq_bot",
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
            "error": f"Failed to send $oq: {exc}",
            "source": "Oq_bot",
        })
        emit(f"Failed to send $oq: {exc}", "ERROR")
        return {"status": "error", "error": str(exc)}

    _, oq_message, cooldown_msg, cooldown_wait = _wait_for_oq_message(url, auth, after_id=before_id)
    if cooldown_msg:
        _log_event({
            "type": "cooldown",
            "message": "You don't have enough $oq for today",
            "wait": cooldown_wait,
            "message_id": str(cooldown_msg.get("id", "")),
            "source": "Oq_bot",
        })
        if cooldown_wait:
            emit(f"You don't have enough $oq for today. Time to wait before the refill: {cooldown_wait}", "WARN")
        else:
            emit("You don't have enough $oq for today.", "WARN")
        return {"status": "cooldown", "wait": cooldown_wait}
    if not oq_message:
        _log_event({
            "type": "error",
            "error": "No $oq response detected",
            "source": "Oq_bot",
        })
        emit("No $oq response detected.", "ERROR")
        return {"status": "error", "error": "No $oq response detected"}
    _emit_latency(
        "message_detected",
        action="oq_message",
        response_ms=int((time.perf_counter() - command_sent_at) * 1000.0) if command_sent_at else None,
        detect_lag_ms=_message_lag_ms(oq_message),
    )

    oq_message_id = str(oq_message.get("id", ""))
    grid = _parse_grid(oq_message)
    if not grid:
        _log_event({
            "type": "error",
            "error": "Failed to parse $oq grid",
            "message_id": oq_message_id,
            "source": "Oq_bot",
        })
        emit("Failed to parse $oq grid.", "ERROR")
        return {"status": "error", "error": "Failed to parse $oq grid"}

    max_clicks, time_limit_sec = _parse_clicks_and_time(
        oq_message.get("content", ""),
        7,
        120,
    )

    cache_ram_mb = int(getattr(Vars, "OQ_RAM_CACHE_MB", 512) or 512)
    beam_k = int(getattr(Vars, "OQ_BEAM_K", OQ_BEAM_K_DEFAULT) or OQ_BEAM_K_DEFAULT)
    cache_max_gb = float(getattr(Vars, "OQ_CACHE_MAX_GB", OQ_DEFAULT_CACHE_MAX_GB) or OQ_DEFAULT_CACHE_MAX_GB)
    auto_learn_higher = _coerce_bool(getattr(Vars, "OQ_AUTO_LEARN_HIGHER_EMOJIS", True), True)
    configured_higher_aliases = _normalize_emoji_aliases(getattr(Vars, "OQ_HIGHER_THAN_RED_EMOJIS", []))
    configured_red_aliases = _normalize_emoji_aliases(getattr(Vars, "OQ_RED_EMOJI_ALIASES", []))
    learning_state = _load_emoji_learning_state(OQ_EMOJI_LEARNING_FILE)
    learned_higher_aliases = _normalize_emoji_aliases(learning_state.get("higher_than_red_emojis", []))
    higher_than_red_aliases: Set[str] = set(configured_higher_aliases) | set(learned_higher_aliases)
    red_aliases: Set[str] = set(configured_red_aliases)
    warned_unknown_emojis: Set[str] = set()

    _log_event({
        "type": "session_start",
        "user": resolved_name,
        "channel_id": Vars.channelId,
        "guild_id": Vars.serverId,
        "message_id": oq_message_id,
        "max_clicks": max_clicks,
        "time_limit_sec": time_limit_sec,
        "cache_ram_mb": cache_ram_mb,
        "beam_k": beam_k,
        "cache_max_gb": cache_max_gb,
        "auto_learn_higher_emojis": auto_learn_higher,
        "known_higher_emoji_aliases": sorted(higher_than_red_aliases),
        "known_red_emoji_aliases": sorted(OQ_BASE_RED_EMOJIS | red_aliases),
        "grid": _grid_snapshot(grid),
        "source": "Oq_bot",
    })

    solver = OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=cache_ram_mb,
        beam_k=beam_k,
        cache_max_gb=cache_max_gb,
    )
    start_time = time.time()
    red_pos: Optional[Tuple[int, int]] = None
    post_red_mode = False
    post_red_queue: List[int] = []

    def _mark_generic_nonpurple(pos_idx: int) -> None:
        if solver.is_revealed(pos_idx):
            return
        # Unknown OQ symbols still mean the tile is revealed and consumed a click.
        solver.revealed_mask |= (1 << pos_idx)
        solver.consume_click(1)

    def _learn_higher_emoji(name: str, row: int, col: int) -> None:
        if not auto_learn_higher:
            return
        if not name or name in higher_than_red_aliases:
            return
        higher_than_red_aliases.add(name)
        learned_now = _normalize_emoji_aliases(learning_state.get("higher_than_red_emojis", []))
        learned_now.add(name)
        learning_state["higher_than_red_emojis"] = sorted(learned_now)
        _save_emoji_learning_state(OQ_EMOJI_LEARNING_FILE, learning_state)
        _log_event({
            "type": "emoji_learned",
            "emoji": name,
            "category": "higher_than_red",
            "row": row,
            "col": col,
            "source": "Oq_bot",
        })
        emit(f"Learned higher-than-red OQ emoji alias: {name}", "INFO")

    def _update_solver_from_grid(current_grid: List[List[OqCell]]) -> None:
        nonlocal red_pos
        for row in current_grid:
            for cell in row:
                if cell.emoji == "spU":
                    continue
                if _is_red_emoji(cell.emoji, red_aliases):
                    red_pos = (cell.row, cell.col)
                    solver.note_red(rc_to_idx(cell.row, cell.col))
                    continue
                obs_code = _map_emoji_to_obs(cell.emoji)
                if obs_code is None:
                    pos_idx = rc_to_idx(cell.row, cell.col)
                    if cell.clickable:
                        if solver.found_purples >= TARGET_PURPLES and red_pos is None:
                            # Some servers use non-standard emoji names for the self-revealed bonus sphere.
                            # Treat an unknown clickable tile as the bonus target and click it first.
                            red_pos = (cell.row, cell.col)
                            if cell.emoji not in warned_unknown_emojis:
                                warned_unknown_emojis.add(cell.emoji)
                                _log_event({
                                    "type": "info",
                                    "message": "Unknown clickable reveal emoji treated as bonus sphere",
                                    "emoji": cell.emoji,
                                    "row": cell.row,
                                    "col": cell.col,
                                    "source": "Oq_bot",
                                })
                        continue
                    treat_as_higher = (
                        post_red_mode
                        or solver.found_purples >= TARGET_PURPLES
                        or _is_higher_than_red_emoji(cell.emoji, higher_than_red_aliases)
                    )
                    if treat_as_higher:
                        if not _is_higher_than_red_emoji(cell.emoji, higher_than_red_aliases):
                            _learn_higher_emoji(cell.emoji, cell.row, cell.col)
                        _mark_generic_nonpurple(pos_idx)
                        continue
                    _mark_generic_nonpurple(pos_idx)
                    if cell.emoji not in warned_unknown_emojis:
                        warned_unknown_emojis.add(cell.emoji)
                        _log_event({
                            "type": "warn",
                            "message": "Unknown emoji in grid (generic non-purple fallback)",
                            "emoji": cell.emoji,
                            "row": cell.row,
                            "col": cell.col,
                            "source": "Oq_bot",
                        })
                    continue
                solver.apply_observation(rc_to_idx(cell.row, cell.col), obs_code)

    _update_solver_from_grid(grid)

    refresh_fail_streak = 0
    while solver.clicks_left > 0:
        if time.time() - start_time > time_limit_sec:
            emit("Time limit reached.", "WARN")
            break

        oq_message = _fetch_oq_message_snapshot(url, base_url, auth, oq_message_id)
        if not oq_message:
            refresh_fail_streak += 1
            emit("Failed to refresh $oq message. Retrying...", "WARN")
            if refresh_fail_streak >= 3:
                emit("Refresh failed too many times. Ending session.", "WARN")
                break
            _post_action_pause(0.5)
            continue
        refresh_fail_streak = 0

        parsed_grid = _parse_grid(oq_message)
        if parsed_grid:
            grid = parsed_grid
        _update_solver_from_grid(grid)

        if solver.clicks_left <= 0:
            break

        forced_red = False
        target_cell = None
        target_r = None
        target_c = None

        if post_red_mode:
            while post_red_queue:
                pos_idx = post_red_queue.pop(0)
                rr, cc = idx_to_rc(pos_idx)
                if 0 <= rr < len(grid) and 0 <= cc < len(grid[0]):
                    candidate = grid[rr][cc]
                    if candidate.clickable:
                        target_cell = candidate
                        target_r, target_c = rr, cc
                        break
            if target_cell is None:
                emit("No clickable tiles left.", "WARN")
                break
        else:
            if red_pos is not None:
                rr, cc = red_pos
                if 0 <= rr < len(grid) and 0 <= cc < len(grid[0]):
                    candidate = grid[rr][cc]
                    if candidate.clickable:
                        target_cell = candidate
                        target_r, target_c = candidate.row, candidate.col
                        forced_red = True

            if target_cell is None:
                if solver.found_purples >= TARGET_PURPLES and red_pos is None:
                    _post_action_pause(0.4)
                    continue
                suggestion = solver.pick_next_click()
                if suggestion is not None:
                    target_r, target_c = idx_to_rc(suggestion)

            if target_cell is None and target_r is not None and target_c is not None:
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
            action="oq_click",
            click_index=solver.clicks_used + 1,
            row=target_r,
            col=target_c,
        )
        bot.click(
            Vars.MUDAE_BOT_ID,
            channelID=Vars.channelId,
            guildID=Vars.serverId,
            messageID=oq_message_id,
            messageFlags=int(oq_message.get("flags", 0) or 0),
            data={"component_type": 2, "custom_id": target_cell.custom_id},
        )

        if forced_red:
            solver.consume_click(1)
            post_red_mode = True
            post_red_queue = solver.post_red_click_order()

        _post_action_pause(0.4)
        oq_message = _fetch_oq_message_snapshot(url, base_url, auth, oq_message_id) or oq_message
        parsed_grid = _parse_grid(oq_message)
        if parsed_grid:
            grid = parsed_grid
        _update_solver_from_grid(grid)

        observed_code: Optional[Any] = None
        if target_r is not None and target_c is not None:
            if 0 <= target_r < len(grid) and 0 <= target_c < len(grid[0]):
                target_emoji = grid[target_r][target_c].emoji
                if _is_red_emoji(target_emoji, red_aliases):
                    observed_code = "RED"
                else:
                    mapped = _map_emoji_to_obs(target_emoji)
                    if mapped is not None:
                        observed_code = mapped
                    elif _is_higher_than_red_emoji(target_emoji, higher_than_red_aliases):
                        observed_code = "HIGH"
                    else:
                        observed_code = "UNKNOWN"

        if not quiet and target_r is not None and target_c is not None:
            emit(
                f"Click {solver.clicks_used}/{max_clicks}: ({target_r+1},{target_c+1}) "
                f"obs={observed_code} purples={solver.found_purples}"
            )

        if target_r is not None and target_c is not None:
            _log_event({
                "type": "click",
                "click_index": solver.clicks_used,
                "row": target_r,
                "col": target_c,
                "emoji": grid[target_r][target_c].emoji if grid else None,
                "observed_code": observed_code,
                "found_purples": solver.found_purples,
                "source": "Oq_bot",
            })
        _emit_latency(
            "action_ack",
            action="oq_click",
            click_index=solver.clicks_used,
            response_ms=int((time.perf_counter() - click_sent_at) * 1000.0),
            detect_lag_ms=_message_lag_ms(oq_message),
        )

        if forced_red:
            emit("Red sphere clicked. Switching to score clicks.", "INFO")

    _log_event({
        "type": "session_end",
        "user": resolved_name,
        "message_id": oq_message_id,
        "clicks_used": solver.clicks_used,
        "found_purples": solver.found_purples,
        "source": "Oq_bot",
    })

    if not quiet:
        emit("Session complete.")

    return {
        "status": "ok",
        "clicks_used": solver.clicks_used,
        "found_purples": solver.found_purples,
        "message_id": oq_message_id,
    }


def main() -> None:
    print("\n" + "=" * 50)
    print("Ouro Quest ($oq) Auto-Player")
    print("=" * 50)

    selected_user = _select_user()
    run_session(selected_user["token"], selected_user["name"], quiet=False)


if __name__ == "__main__":
    main()






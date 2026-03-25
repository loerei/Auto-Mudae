from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _to_env_style_name(name: str) -> str:
    step1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    step2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step1)
    return step2.replace("__", "_").upper()


def _has_closed_quote(value: str, quote_char: str) -> bool:
    escaped = False
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == quote_char:
            return True
    return False


def _container_balanced(value: str) -> bool:
    in_single = False
    in_double = False
    escaped = False
    square = 0
    curly = 0
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if ch == "[":
            square += 1
        elif ch == "]":
            square -= 1
        elif ch == "{":
            curly += 1
        elif ch == "}":
            curly -= 1
    return square == 0 and curly == 0 and not in_single and not in_double


def _load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return values
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            i += 1
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            i += 1
            continue

        if value and value[0] in {"'", '"'}:
            quote = value[0]
            if len(value) == 1 or not _has_closed_quote(value[1:], quote):
                chunks: List[str] = [value[1:]]
                i += 1
                while i < len(lines):
                    next_raw = lines[i]
                    pos = next_raw.find(quote)
                    if pos >= 0:
                        chunks.append(next_raw[:pos])
                        break
                    chunks.append(next_raw)
                    i += 1
                value = "\n".join(chunks)
            else:
                closing = value.find(quote, 1)
                value = value[1:closing]
        elif value and value[0] in {"[", "{"} and not _container_balanced(value):
            chunks = [value]
            i += 1
            while i < len(lines):
                next_raw = lines[i]
                chunks.append(next_raw.strip())
                merged = "\n".join(chunks)
                if _container_balanced(merged):
                    break
                i += 1
            value = "\n".join(chunks)

        values[key] = value
        i += 1
    return values


def _effective_env() -> Dict[str, str]:
    merged: Dict[str, str] = {}
    explicit = os.environ.get("MUDAE_ENV_FILE", "").strip()
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([PROJECT_ROOT / ".env", PROJECT_ROOT / "config.env"])
    for path in candidates:
        merged.update(_load_env_file(path))
    merged.update({k: v for k, v in os.environ.items()})
    return merged


def _parse_structured(raw: str) -> Any:
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(raw)
        except Exception:
            continue
    return raw


def _coerce_value(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on", "y"}:
            return True
        if lowered in {"0", "false", "no", "off", "n"}:
            return False
        return current
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(raw.strip())
        except Exception:
            return current
    if isinstance(current, float):
        try:
            return float(raw.strip())
        except Exception:
            return current
    if isinstance(current, dict):
        parsed = _parse_structured(raw)
        return parsed if isinstance(parsed, dict) else current
    if isinstance(current, list):
        parsed = _parse_structured(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, tuple):
            return list(parsed)
        if isinstance(parsed, str):
            parts = [p.strip() for p in parsed.split(",")]
            return [p for p in parts if p]
        return current
    if isinstance(current, tuple):
        parsed = _parse_structured(raw)
        if isinstance(parsed, (list, tuple)):
            return tuple(parsed)
        return current
    if isinstance(current, str):
        return raw
    return current


_ENV_ALIASES = {
    "tokens": ["TOKENS_JSON", "TOKENS"],
    "channelId": ["CHANNEL_ID", "CHANNELID"],
    "serverId": ["SERVER_ID", "SERVERID"],
    "rollCommand": ["ROLL_COMMAND"],
    "pokeRoll": ["POKE_ROLL"],
    "wishlist": ["WISHLIST"],
    "minKakeratoclaim": ["MIN_KAKERA_TO_CLAIM"],
    "steal_claim_whitelist": ["STEAL_CLAIM_WHITELIST"],
    "steal_react_whitelist": ["STEAL_REACT_WHITELIST"],
    "Kakera_Give": ["KAKERA_GIVE"],
    "Sphere_Give": ["SPHERE_GIVE"],
    "KAKERA_REACTION_PRIORITY": ["KAKERA_PRIORITY"],
}


def _apply_env_overrides(namespace: Dict[str, Any]) -> None:
    env = _effective_env()
    for name, current in list(namespace.items()):
        if name.startswith("_"):
            continue
        if not isinstance(current, (bool, int, float, str, list, tuple, dict)):
            continue

        candidates: List[str] = []
        candidates.extend(_ENV_ALIASES.get(name, []))
        candidates.append(name)
        candidates.append(name.upper())
        candidates.append(_to_env_style_name(name))

        seen = set()
        env_value = None
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)
            if key in env:
                env_value = env[key]
                break
        if env_value is None:
            continue
        namespace[name] = _coerce_value(current, env_value)


# Keep empty in code; populate via TOKENS_JSON in .env/config.env.
tokens = []
# Keep empty in code; populate via CHANNEL_ID / SERVER_ID in .env/config.env.
channelId = ""
serverId = ""

# Personal runtime settings can be overridden in .env/config.env.
rollCommand = 'wx'
pokeRoll = False

# Auto Ouro toggles (main bot)
AUTO_OURO_AFTER_ROLL = True
AUTO_OH = True
AUTO_OC = True
AUTO_OQ = True
OQ_RAM_CACHE_MB = 512
OQ_BEAM_K = 3
OQ_CACHE_MAX_GB = 10.0
OQ_AUTO_LEARN_HIGHER_EMOJIS = True
OQ_HIGHER_THAN_RED_EMOJIS = []
OQ_RED_EMOJI_ALIASES = []

# Auto transfer after each /tu parse in core session engine.
# Format: list of (from_id, to_id) pairs using tokens[].id.
# Example: [[1,2]] in .env -> user id 1 sends full balances to user id 2.
Kakera_Give = []
Sphere_Give = []

# Optional kakera reaction priority overrides.
# Keep this empty to use parser defaults; set via KAKERA_PRIORITY in .env.
# Example: {"kakerag": 0, "kakerat": 0}
KAKERA_REACTION_PRIORITY = {}

# Wishlist: add character names or series names to claim (prefer .env).
wishlist = []

# Minimum kakera value to claim a card (claims cards with kakera >= this value).
minKakeratoclaim = 200

# Users to exclude from steal-claim / steal-react (match by username, case-insensitive).
steal_claim_whitelist = []
steal_react_whitelist = []

# Interaction correlation: match responses to the interaction id (if available)
ENABLE_INTERACTION_ID_CORRELATION = True

# Persist last seen message id per channel for safer restarts
LAST_SEEN_PATH = 'config/last_seen.json'
LAST_SEEN_FLUSH_SEC = 60

# Dashboard redraw control (set False to use single-line status logs)
DASHBOARD_LIVE_REDRAW = True
DASHBOARD_STATUS_LOG_SEC = 10

# Force clear + full redraw every update (matches 20260113 backup behavior)
DASHBOARD_FORCE_CLEAR = True

# Renderer policy:
# - auto: pick once per process and keep stable.
# - ansi_full: deterministic full-frame ANSI redraw.
# - win32: Win32 cursor/write renderer.
# - legacy_clear: clear screen + print lines.
DASHBOARD_RENDERER_MODE = "auto"

# No-scroll policy and viewport safety margins.
DASHBOARD_NO_SCROLL = True
DASHBOARD_WIDECHAR_AWARE = True
DASHBOARD_RENDER_SAFETY_COLS = 2
DASHBOARD_RENDER_SAFETY_ROWS = 2

# Dashboard layout sizing
# - AUTO_FIT: keep dashboard width aligned to terminal width (recommended)
# - MIN/MAX are soft bounds; AUTO_FIT always prioritizes fitting current window
DASHBOARD_AUTO_FIT = True
DASHBOARD_MIN_WIDTH = 60
DASHBOARD_MAX_WIDTH = 120

# Steal behavior: avoid /tu refresh in steal path by default
STEAL_ALLOW_TU_REFRESH = True
STEAL_TU_MAX_AGE_SEC = 180

# Discord API configuration
DISCORD_API_BASE = 'https://discord.com/api'
DISCORD_API_VERSION_MESSAGES = 'v8'
DISCORD_API_VERSION_USERS = 'v9'

# Mudae bot config
MUDAE_BOT_ID = '432610292342587392'

# Emoji configuration (use unicode escapes to keep this file ASCII)
EMOJI_CLAIM_REACT = '\U0001F43F\ufe0f'  # 🐿️
EMOJI_STATUS_CLAIMED = '\u2764\ufe0f'  # ❤️
EMOJI_STATUS_UNCLAIMED = '\U0001F90D'  # 🤍
EMOJI_KAKERA = '\U0001F48E'  # 💎

# Logging configuration
LOG_LEVEL_DEFAULT = 'INFO'
LOG_USE_EMOJI = True
LOG_EMOJI = {
    'DEBUG': '',
    'INFO': '\u2139\ufe0f',    # ℹ️
    'SUCCESS': '\u2705',      # ✅
    'WARN': '\u26a0\ufe0f',   # ⚠️
    'ERROR': '\u274c',        # ❌
}

# Timing configuration (seconds)
SLEEP_SHORT_SEC = 0.2
SLEEP_MED_SEC = 0.5
SLEEP_LONG_SEC = 1.0
ROLL_TRIGGER_DELAY_SEC = 1.8
KAKERA_REACT_DELAY_SEC = 0.8
STEAL_REACT_DELAY_SEC = 0.4
REACT_CLICK_WAIT_SEC = 0.5
STEAL_REACT_CLICK_WAIT_SEC = 0.2
WISH_CLAIM_RETRY_COUNT = 5
WISH_CLAIM_RETRY_DELAY_SEC = 1.0

# Multi-instance coordination for roll-consuming actions.
ROLL_COORDINATION_ENABLED = True
ROLL_LEASE_TTL_SEC = 90.0
ROLL_LEASE_HEARTBEAT_SEC = 10.0
ROLL_LEASE_WAIT_SEC = 120.0
ROLL_STALL_REFRESH_THRESHOLD = 2
ROLL_STALL_ABORT_THRESHOLD = 4

# Latency optimization profile
# - aggressive_auto: aggressive timings with automatic degrade/recover
# - aggressive / balanced / legacy: fixed behavior
LATENCY_PROFILE_DEFAULT = 'aggressive_auto'
LATENCY_FORCE_PROFILE = ''
LATENCY_AUTO_DEGRADE = True
LATENCY_METRICS_ENABLED = True
LATENCY_METRICS_PATH = 'logs/LatencyMetrics.jsonl'

# Roll limits
ROLLS_PER_RESET = 14

# Alt+C behavior hardening.
ALT_C_DEBOUNCE_MS = 250
ALT_C_INPUT_GUARD_MS = 600

# Wishlist parsing hardening for mojibake marker variants.
WISHLIST_NORMALIZE_TEXT = True


_apply_env_overrides(globals())
    

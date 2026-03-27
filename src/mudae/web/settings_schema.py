from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from mudae.web.config import DEFAULT_UI_SETTINGS, collect_mudae_settings, normalize_ui_settings


SETTINGS_SECTIONS: List[Dict[str, Any]] = [
    {"id": "appearance", "title": "Appearance", "description": "Theme and presentation preferences for the WebUI."},
    {"id": "webui_runtime", "title": "WebUI Runtime", "description": "Local daemon binding, retention, and launch behavior."},
    {"id": "core_runtime_timing", "title": "Core Runtime & Timing", "description": "Command pacing, latency strategy, and general runtime behavior."},
    {"id": "roll_claim_react", "title": "Roll / Claim / React", "description": "Roll-cycle behavior, claim/react policy, and wishlist-related runtime controls."},
    {"id": "ouro", "title": "Ouro", "description": "Auto-Ouro behavior and OQ-specific tuning."},
    {"id": "dashboard_logging", "title": "Dashboard & Logging", "description": "Terminal dashboard behavior, logging, and local operator UX."},
    {"id": "advanced_integration", "title": "Advanced Integration", "description": "Discord/API identity, channel targeting, and low-level path or integration fields."},
]

SECTION_FIELDS: Dict[str, List[Tuple[str, str]]] = {
    "appearance": [
        ("ui_settings", "theme"),
    ],
    "webui_runtime": [
        ("ui_settings", "bind_host"),
        ("ui_settings", "bind_port"),
        ("ui_settings", "retention_days"),
        ("ui_settings", "auto_open_browser"),
    ],
    "core_runtime_timing": [
        ("app_settings", "rollCommand"),
        ("app_settings", "pokeRoll"),
        ("app_settings", "ENABLE_INTERACTION_ID_CORRELATION"),
        ("app_settings", "SLEEP_SHORT_SEC"),
        ("app_settings", "SLEEP_MED_SEC"),
        ("app_settings", "SLEEP_LONG_SEC"),
        ("app_settings", "ROLL_TRIGGER_DELAY_SEC"),
        ("app_settings", "KAKERA_REACT_DELAY_SEC"),
        ("app_settings", "STEAL_REACT_DELAY_SEC"),
        ("app_settings", "REACT_CLICK_WAIT_SEC"),
        ("app_settings", "STEAL_REACT_CLICK_WAIT_SEC"),
        ("app_settings", "WISH_CLAIM_RETRY_COUNT"),
        ("app_settings", "WISH_CLAIM_RETRY_DELAY_SEC"),
        ("app_settings", "STEAL_ALLOW_TU_REFRESH"),
        ("app_settings", "STEAL_TU_MAX_AGE_SEC"),
        ("app_settings", "TU_INFO_REUSE_MAX_AGE_SEC"),
        ("app_settings", "WISHLIST_CACHE_TTL_SEC"),
        ("app_settings", "NO_RESET_RETRY_JITTER_PCT"),
        ("app_settings", "LATENCY_PROFILE_DEFAULT"),
        ("app_settings", "LATENCY_FORCE_PROFILE"),
        ("app_settings", "LATENCY_AUTO_DEGRADE"),
        ("app_settings", "LATENCY_METRICS_ENABLED"),
        ("app_settings", "ROLLS_PER_RESET"),
    ],
    "roll_claim_react": [
        ("app_settings", "minKakeratoclaim"),
        ("app_settings", "EMOJI_CLAIM_REACT"),
        ("app_settings", "EMOJI_STATUS_CLAIMED"),
        ("app_settings", "EMOJI_STATUS_UNCLAIMED"),
        ("app_settings", "EMOJI_KAKERA"),
        ("app_settings", "KAKERA_REACTION_PRIORITY"),
        ("app_settings", "Kakera_Give"),
        ("app_settings", "Sphere_Give"),
        ("app_settings", "steal_claim_whitelist"),
        ("app_settings", "steal_react_whitelist"),
        ("app_settings", "WISHLIST_NORMALIZE_TEXT"),
        ("app_settings", "ROLL_COORDINATION_ENABLED"),
        ("app_settings", "ROLL_LEASE_TTL_SEC"),
        ("app_settings", "ROLL_LEASE_HEARTBEAT_SEC"),
        ("app_settings", "ROLL_LEASE_WAIT_SEC"),
        ("app_settings", "ROLL_STALL_REFRESH_THRESHOLD"),
        ("app_settings", "ROLL_STALL_ABORT_THRESHOLD"),
    ],
    "ouro": [
        ("app_settings", "AUTO_OURO_AFTER_ROLL"),
        ("app_settings", "AUTO_OH"),
        ("app_settings", "AUTO_OC"),
        ("app_settings", "AUTO_OQ"),
        ("app_settings", "OQ_RAM_CACHE_MB"),
        ("app_settings", "OQ_BEAM_K"),
        ("app_settings", "OQ_CACHE_MAX_GB"),
        ("app_settings", "OQ_AUTO_LEARN_HIGHER_EMOJIS"),
        ("app_settings", "OQ_HIGHER_THAN_RED_EMOJIS"),
        ("app_settings", "OQ_RED_EMOJI_ALIASES"),
    ],
    "dashboard_logging": [
        ("app_settings", "DASHBOARD_LIVE_REDRAW"),
        ("app_settings", "DASHBOARD_STATUS_LOG_SEC"),
        ("app_settings", "DASHBOARD_FORCE_CLEAR"),
        ("app_settings", "DASHBOARD_RENDERER_MODE"),
        ("app_settings", "DASHBOARD_NO_SCROLL"),
        ("app_settings", "DASHBOARD_WIDECHAR_AWARE"),
        ("app_settings", "DASHBOARD_RENDER_SAFETY_COLS"),
        ("app_settings", "DASHBOARD_RENDER_SAFETY_ROWS"),
        ("app_settings", "DASHBOARD_AUTO_FIT"),
        ("app_settings", "DASHBOARD_MIN_WIDTH"),
        ("app_settings", "DASHBOARD_MAX_WIDTH"),
        ("app_settings", "LOG_LEVEL_DEFAULT"),
        ("app_settings", "LOG_USE_EMOJI"),
        ("app_settings", "LOG_EMOJI"),
        ("app_settings", "ALT_C_DEBOUNCE_MS"),
        ("app_settings", "ALT_C_INPUT_GUARD_MS"),
    ],
    "advanced_integration": [
        ("app_settings", "channelId"),
        ("app_settings", "serverId"),
        ("app_settings", "DISCORD_API_BASE"),
        ("app_settings", "DISCORD_API_VERSION_MESSAGES"),
        ("app_settings", "DISCORD_API_VERSION_USERS"),
        ("app_settings", "MUDAE_BOT_ID"),
        ("app_settings", "LAST_SEEN_PATH"),
        ("app_settings", "LAST_SEEN_FLUSH_SEC"),
        ("app_settings", "LATENCY_METRICS_PATH"),
    ],
}

FIELD_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "theme": {
        "label": "Theme Mode",
        "editor": "select",
        "options": [
            {"value": "system", "label": "System"},
            {"value": "light", "label": "Light"},
            {"value": "dark", "label": "Dark"},
        ],
        "apply_scope": "Immediate",
        "description": "Controls whether the WebUI follows the OS theme or forces a local override.",
    },
    "bind_host": {"label": "Bind Host", "apply_scope": "Daemon restart"},
    "bind_port": {"label": "Bind Port", "apply_scope": "Daemon restart", "validation": {"min": 1, "max": 65535}},
    "retention_days": {"label": "Retention Days", "apply_scope": "Immediate", "validation": {"min": 1, "max": 3650}},
    "auto_open_browser": {"label": "Auto Open Browser", "apply_scope": "Daemon restart"},
    "rollCommand": {
        "label": "Roll Command",
        "editor": "select",
        "options": [{"value": "wa", "label": "$wa"}, {"value": "wx", "label": "$wx"}],
    },
    "LATENCY_PROFILE_DEFAULT": {
        "label": "Latency Profile",
        "editor": "select",
        "options": [
            {"value": "aggressive_auto", "label": "Aggressive Auto"},
            {"value": "aggressive", "label": "Aggressive"},
            {"value": "balanced", "label": "Balanced"},
            {"value": "legacy", "label": "Legacy"},
        ],
    },
    "LATENCY_FORCE_PROFILE": {
        "label": "Forced Latency Profile",
        "editor": "select",
        "options": [
            {"value": "", "label": "Auto / None"},
            {"value": "aggressive", "label": "Aggressive"},
            {"value": "balanced", "label": "Balanced"},
            {"value": "legacy", "label": "Legacy"},
        ],
        "validation": {"allow_blank": True},
    },
    "LOG_LEVEL_DEFAULT": {
        "label": "Default Log Level",
        "editor": "select",
        "options": [
            {"value": "DEBUG", "label": "DEBUG"},
            {"value": "INFO", "label": "INFO"},
            {"value": "SUCCESS", "label": "SUCCESS"},
            {"value": "WARN", "label": "WARN"},
            {"value": "ERROR", "label": "ERROR"},
        ],
    },
    "DASHBOARD_RENDERER_MODE": {
        "label": "Dashboard Renderer",
        "editor": "select",
        "options": [
            {"value": "auto", "label": "Auto"},
            {"value": "ansi_full", "label": "ANSI Full"},
            {"value": "win32", "label": "Win32"},
            {"value": "legacy_clear", "label": "Legacy Clear"},
            {"value": "status_line", "label": "Status Line"},
        ],
    },
    "KAKERA_REACTION_PRIORITY": {
        "label": "Kakera Reaction Priority",
        "editor": "key_value",
        "value_type": "nullable_int_map",
        "validation": {
            "value_kind": "nullable_int",
            "allowed_keys": ["kakerap", "kakerac", "kakeral", "kakeraw", "kakerar", "kakerao", "kakerad", "kakeray", "kakerag", "kakerat", "kakerab", "kakera"],
        },
    },
    "LOG_EMOJI": {
        "label": "Log Emoji Map",
        "editor": "key_value",
        "value_type": "string_map",
        "validation": {"value_kind": "string", "allowed_keys": ["DEBUG", "INFO", "SUCCESS", "WARN", "ERROR"]},
    },
    "Kakera_Give": {
        "label": "Kakera Give Rules",
        "editor": "pair_list",
        "value_type": "int_pair_list",
        "validation": {"pair_labels": ["Target ID", "Amount"], "min": 1},
    },
    "Sphere_Give": {
        "label": "Sphere Give Rules",
        "editor": "pair_list",
        "value_type": "int_pair_list",
        "validation": {"pair_labels": ["Target ID", "Amount"], "min": 1},
    },
    "steal_claim_whitelist": {"label": "Steal Claim Whitelist", "editor": "tag_list", "value_type": "string_list"},
    "steal_react_whitelist": {"label": "Steal React Whitelist", "editor": "tag_list", "value_type": "string_list"},
    "OQ_HIGHER_THAN_RED_EMOJIS": {"label": "OQ Higher-Than-Red Emojis", "editor": "ordered_list", "value_type": "string_list"},
    "OQ_RED_EMOJI_ALIASES": {"label": "OQ Red Emoji Aliases", "editor": "ordered_list", "value_type": "string_list"},
    "DISCORD_API_BASE": {"label": "Discord API Base", "dangerous": True, "apply_scope": "Daemon restart"},
    "DISCORD_API_VERSION_MESSAGES": {"label": "Message API Version", "dangerous": True, "apply_scope": "Daemon restart"},
    "DISCORD_API_VERSION_USERS": {"label": "User API Version", "dangerous": True, "apply_scope": "Daemon restart"},
    "MUDAE_BOT_ID": {"label": "Mudae Bot ID", "dangerous": True, "apply_scope": "Daemon restart"},
    "channelId": {"label": "Channel ID", "dangerous": True, "apply_scope": "Daemon restart"},
    "serverId": {"label": "Server ID", "dangerous": True, "apply_scope": "Daemon restart"},
    "LAST_SEEN_PATH": {"label": "Last-Seen Cache Path", "dangerous": True, "apply_scope": "Daemon restart"},
    "LATENCY_METRICS_PATH": {"label": "Latency Metrics Path", "dangerous": True, "apply_scope": "Daemon restart"},
}

SOURCE_LABELS = {"app_settings": "App Settings", "ui_settings": "UI Settings"}


def build_settings_schema(
    app_settings: Optional[Dict[str, Any]] = None,
    ui_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    default_app = collect_mudae_settings()
    current_app = {**default_app, **dict(app_settings or {})}
    current_ui = normalize_ui_settings(ui_settings or DEFAULT_UI_SETTINGS)
    sections: List[Dict[str, Any]] = []
    known_app_keys: set[str] = set()

    for section in SETTINGS_SECTIONS:
        fields: List[Dict[str, Any]] = []
        for source, key in SECTION_FIELDS.get(section["id"], []):
            default_value = copy.deepcopy(default_app.get(key) if source == "app_settings" else DEFAULT_UI_SETTINGS.get(key))
            current_value = copy.deepcopy(current_app.get(key) if source == "app_settings" else current_ui.get(key))
            fields.append(_build_field_schema(section["id"], source, key, default_value, current_value))
            if source == "app_settings":
                known_app_keys.add(key)
        sections.append({**section, "fields": fields})

    unknown_app_settings = [
        {"source": "app_settings", "key": key, "label": _humanize_setting_key(key), "value": copy.deepcopy(value)}
        for key, value in sorted(dict(app_settings or {}).items())
        if key not in known_app_keys
    ]

    return {
        "sections": sections,
        "unknown_app_settings": unknown_app_settings,
        "sources": copy.deepcopy(SOURCE_LABELS),
    }


def apply_settings_patch(
    *,
    current_app_settings: Dict[str, Any],
    current_ui_settings: Dict[str, Any],
    app_patch: Optional[Dict[str, Any]] = None,
    ui_patch: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]:
    default_app = collect_mudae_settings()
    metadata = _field_metadata_map(default_app)
    next_app = copy.deepcopy(current_app_settings)
    next_ui = normalize_ui_settings(current_ui_settings)
    field_errors: List[Dict[str, str]] = []

    for source, patch in (("app_settings", dict(app_patch or {})), ("ui_settings", dict(ui_patch or {}))):
        for key, raw_value in patch.items():
            field_meta = metadata.get((source, key))
            if field_meta is None:
                field_errors.append({"source": source, "key": key, "message": "Unsupported setting key."})
                continue
            coerced, error = _coerce_field_value(field_meta, raw_value)
            if error:
                field_errors.append({"source": source, "key": key, "message": error})
                continue
            if source == "app_settings":
                next_app[key] = coerced
            else:
                next_ui[key] = coerced

    if field_errors:
        return copy.deepcopy(current_app_settings), normalize_ui_settings(current_ui_settings), field_errors

    return next_app, normalize_ui_settings(next_ui), []


def _field_metadata_map(default_app: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    mapping: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for section in SETTINGS_SECTIONS:
        for source, key in SECTION_FIELDS.get(section["id"], []):
            default_value = copy.deepcopy(default_app.get(key) if source == "app_settings" else DEFAULT_UI_SETTINGS.get(key))
            mapping[(source, key)] = _build_field_schema(section["id"], source, key, default_value, default_value)
    return mapping


def _build_field_schema(section_id: str, source: str, key: str, default_value: Any, current_value: Any) -> Dict[str, Any]:
    override = FIELD_OVERRIDES.get(key, {})
    validation = copy.deepcopy(override.get("validation") or {})
    return {
        "key": key,
        "source": source,
        "section": section_id,
        "label": override.get("label") or _humanize_setting_key(key),
        "description": override.get("description") or "",
        "editor": override.get("editor") or _infer_editor(default_value),
        "value_type": override.get("value_type") or _infer_value_type(default_value),
        "default": copy.deepcopy(default_value),
        "value": copy.deepcopy(current_value),
        "options": copy.deepcopy(override.get("options") or []),
        "validation": validation,
        "apply_scope": override.get("apply_scope") or _default_apply_scope(section_id, key),
        "dangerous": bool(override.get("dangerous", False)),
        "editable": bool(override.get("editable", True)),
    }


def _infer_editor(default_value: Any) -> str:
    if isinstance(default_value, bool):
        return "toggle"
    if isinstance(default_value, (int, float)):
        return "number"
    if isinstance(default_value, list):
        return "tag_list"
    if isinstance(default_value, dict):
        return "key_value"
    return "text"


def _infer_value_type(default_value: Any) -> str:
    if isinstance(default_value, bool):
        return "bool"
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return "int"
    if isinstance(default_value, float):
        return "float"
    if isinstance(default_value, list):
        return "string_list"
    if isinstance(default_value, dict):
        return "string_map"
    return "string"


def _default_apply_scope(section_id: str, key: str) -> str:
    if key == "theme":
        return "Immediate"
    if section_id in {"webui_runtime", "advanced_integration"}:
        return "Daemon restart"
    return "Next session"


def _humanize_setting_key(key: str) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", key)
    text = text.replace("_", " ")
    parts = [part for part in text.split() if part]
    return " ".join(part.upper() if part.isupper() or part.lower() in {"id", "api", "oq", "oh", "oc", "ui"} else part.capitalize() for part in parts)


def _coerce_field_value(field_meta: Dict[str, Any], raw_value: Any) -> Tuple[Any, Optional[str]]:
    value_type = str(field_meta.get("value_type") or "string")
    validation = dict(field_meta.get("validation") or {})
    options = field_meta.get("options") or []

    if options:
        allowed = {str(item.get("value")) for item in options if isinstance(item, dict)}
        candidate = str(raw_value if raw_value is not None else "")
        if candidate not in allowed:
            return None, "Choose one of the supported values."
        return candidate, None

    if value_type == "bool":
        return _coerce_bool(raw_value), None
    if value_type == "int":
        return _coerce_number(raw_value, validation, integer=True)
    if value_type == "float":
        return _coerce_number(raw_value, validation, integer=False)
    if value_type == "string":
        text = str(raw_value or "").strip()
        if not text and not validation.get("allow_blank", False):
            return None, "This field cannot be blank."
        return text, None
    if value_type == "string_list":
        if not isinstance(raw_value, list):
            return None, "Expected a list of strings."
        items: List[str] = []
        seen: set[str] = set()
        for item in raw_value:
            text = str(item or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            items.append(text)
        return items, None
    if value_type == "int_pair_list":
        if not isinstance(raw_value, list):
            return None, "Expected a list of numeric pairs."
        normalized_pairs: List[List[int]] = []
        for pair in raw_value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return None, "Each row must contain exactly two numeric values."
            left, left_error = _coerce_number(pair[0], validation, integer=True)
            right, right_error = _coerce_number(pair[1], validation, integer=True)
            if left_error or right_error:
                return None, left_error or right_error
            normalized_pairs.append([left, right])
        return normalized_pairs, None
    if value_type == "string_map":
        if not isinstance(raw_value, dict):
            return None, "Expected a key-value object."
        return _coerce_map(raw_value, validation, value_kind="string")
    if value_type == "nullable_int_map":
        if not isinstance(raw_value, dict):
            return None, "Expected a key-value object."
        return _coerce_map(raw_value, validation, value_kind="nullable_int")
    return copy.deepcopy(raw_value), None


def _coerce_map(raw_value: Dict[str, Any], validation: Dict[str, Any], *, value_kind: str) -> Tuple[Any, Optional[str]]:
    allowed_keys = [str(key) for key in validation.get("allowed_keys") or []]
    output: Dict[str, Any] = {}
    source = dict(raw_value)
    keys = allowed_keys or [str(key) for key in source.keys()]
    for key in keys:
        if key not in source:
            output[key] = None if value_kind == "nullable_int" else ""
            continue
        value = source.get(key)
        if value_kind == "string":
            output[key] = str(value or "")
            continue
        if value in {None, ""}:
            output[key] = None
            continue
        coerced, error = _coerce_number(value, validation, integer=True)
        if error:
            return None, error
        output[key] = coerced
    extra_keys = [key for key in source.keys() if allowed_keys and key not in allowed_keys]
    if extra_keys:
        return None, f"Unsupported keys: {', '.join(sorted(extra_keys))}"
    return output, None


def _coerce_number(raw_value: Any, validation: Dict[str, Any], *, integer: bool) -> Tuple[Any, Optional[str]]:
    try:
        number = int(raw_value) if integer else float(raw_value)
    except (TypeError, ValueError):
        return None, "Enter a valid number."
    minimum = validation.get("min")
    maximum = validation.get("max")
    if minimum is not None and number < minimum:
        return None, f"Value must be at least {minimum}."
    if maximum is not None and number > maximum:
        return None, f"Value must be at most {maximum}."
    return number, None


def _coerce_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        lowered = raw_value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(raw_value)

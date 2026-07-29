from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from mudae.web.config import DEFAULT_UI_SETTINGS, collect_mudae_settings, normalize_ui_settings


SETTINGS_SECTIONS: List[Dict[str, Any]] = [
    {
        "id": "appearance",
        "title": "Appearance",
        "description": "Theme and presentation preferences for the WebUI.",
        "section_apply_scope": "Immediate",
        "groups": [
            {
                "id": "appearance_theme",
                "title": "Theme",
                "description": "Choose how the WebUI resolves light and dark mode.",
                "layout_hint": "rows",
                "fields": [("ui_settings", "theme")],
            }
        ],
    },
    {
        "id": "webui_runtime",
        "title": "WebUI Runtime",
        "description": "Local daemon binding, retention, and launch behavior.",
        "section_apply_scope": "Daemon restart",
        "groups": [
            {
                "id": "webui_network",
                "title": "Networking & Startup",
                "description": "Controls how the local daemon binds, retains history, and opens the browser.",
                "layout_hint": "rows",
                "fields": [
                    ("ui_settings", "bind_host"),
                    ("ui_settings", "bind_port"),
                    ("ui_settings", "retention_days"),
                    ("ui_settings", "auto_open_browser"),
                ],
            }
        ],
    },
    {
        "id": "core_runtime_timing",
        "title": "Core Runtime & Timing",
        "description": "Command pacing, cache reuse, and latency strategy for the main runtime.",
        "groups": [
            {
                "id": "command_pacing",
                "title": "Command Pacing",
                "description": "Low-level send, click, and reaction timings used during the roll loop.",
                "layout_hint": "rows",
                "fields": [
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
                ],
            },
            {
                "id": "cache_refresh",
                "title": "Cache & Refresh",
                "description": "Controls retry windows and cache freshness before the runtime re-syncs with Discord.",
                "layout_hint": "rows",
                "fields": [
                    ("app_settings", "WISH_CLAIM_RETRY_COUNT"),
                    ("app_settings", "WISH_CLAIM_RETRY_DELAY_SEC"),
                    ("app_settings", "STEAL_ALLOW_TU_REFRESH"),
                    ("app_settings", "STEAL_TU_MAX_AGE_SEC"),
                    ("app_settings", "TU_INFO_REUSE_MAX_AGE_SEC"),
                    ("app_settings", "WISHLIST_CACHE_TTL_SEC"),
                ],
            },
            {
                "id": "latency_retry",
                "title": "Latency & Retry",
                "description": "Shapes retry jitter, latency profile selection, and reset pacing.",
                "layout_hint": "rows",
                "fields": [
                    ("app_settings", "NO_RESET_RETRY_JITTER_PCT"),
                    ("app_settings", "LATENCY_PROFILE_DEFAULT"),
                    ("app_settings", "LATENCY_FORCE_PROFILE"),
                    ("app_settings", "LATENCY_AUTO_DEGRADE"),
                    ("app_settings", "LATENCY_METRICS_ENABLED"),
                    ("app_settings", "ROLLS_PER_RESET"),
                ],
            },
        ],
    },
    {
        "id": "roll_claim_react",
        "title": "Roll / Claim / React",
        "description": "Claim policy, wishlist rules, transfer behavior, and same-account coordination.",
        "groups": [
            {
                "id": "claim_policy",
                "title": "Claim Policy",
                "description": "Defines the minimum kakera threshold and the reaction/status emoji used during roll evaluation.",
                "layout_hint": "rows",
                "fields": [
                    ("app_settings", "minKakeratoclaim"),
                    ("app_settings", "EMOJI_CLAIM_REACT"),
                    ("app_settings", "EMOJI_STATUS_CLAIMED"),
                    ("app_settings", "EMOJI_STATUS_UNCLAIMED"),
                    ("app_settings", "EMOJI_KAKERA"),
                    ("app_settings", "WISHLIST_NORMALIZE_TEXT"),
                ],
            },
            {
                "id": "reaction_transfer_rules",
                "title": "Reaction & Transfer Rules",
                "description": "Priority order for kakera reactions and hourly resource transfer mappings.",
                "layout_hint": "cards",
                "fields": [
                    ("app_settings", "KAKERA_REACTION_PRIORITY"),
                    ("app_settings", "Kakera_Give"),
                    ("app_settings", "Sphere_Give"),
                ],
            },
            {
                "id": "steal_behavior",
                "title": "Steal Behavior",
                "description": "Controls who the bot may steal claims or reactions from in shared channels.",
                "layout_hint": "cards",
                "fields": [
                    ("app_settings", "steal_claim_whitelist"),
                    ("app_settings", "steal_react_whitelist"),
                ],
            },
            {
                "id": "coordination",
                "title": "Coordination",
                "description": "Same-account lease timings and stall thresholds used to prevent duplicate roll sessions.",
                "layout_hint": "rows",
                "fields": [
                    ("app_settings", "ROLL_COORDINATION_ENABLED"),
                    ("app_settings", "ROLL_LEASE_TTL_SEC"),
                    ("app_settings", "ROLL_LEASE_HEARTBEAT_SEC"),
                    ("app_settings", "ROLL_LEASE_WAIT_SEC"),
                    ("app_settings", "ROLL_STALL_REFRESH_THRESHOLD"),
                    ("app_settings", "ROLL_STALL_ABORT_THRESHOLD"),
                ],
            },
        ],
    },
    {
        "id": "ouro",
        "title": "Ouro",
        "description": "Auto-Ouro switches, OQ resource limits, and emoji-learning behavior.",
        "groups": [
            {
                "id": "ouro_automation",
                "title": "Automation",
                "description": "Choose which Ouro commands may run automatically after a roll session.",
                "layout_hint": "rows",
                "fields": [
                    ("app_settings", "AUTO_OURO_AFTER_ROLL"),
                    ("app_settings", "AUTO_OH"),
                    ("app_settings", "AUTO_OC"),
                    ("app_settings", "AUTO_OQ"),
                    ("app_settings", "OQ_AUTO_LEARN_HIGHER_EMOJIS"),
                ],
            },
            {
                "id": "oq_tuning",
                "title": "OQ Tuning",
                "description": "Memory, beam search, and emoji-alias tuning for the OQ solver.",
                "layout_hint": "cards",
                "fields": [
                    ("app_settings", "OQ_RAM_CACHE_MB"),
                    ("app_settings", "OQ_BEAM_K"),
                    ("app_settings", "OQ_CACHE_MAX_GB"),
                    ("app_settings", "OQ_HIGHER_THAN_RED_EMOJIS"),
                    ("app_settings", "OQ_RED_EMOJI_ALIASES"),
                ],
            },
        ],
    },
    {
        "id": "dashboard_logging",
        "title": "Dashboard & Logging",
        "description": "Terminal redraw behavior, viewport guards, and local logging defaults.",
        "groups": [
            {
                "id": "dashboard_rendering",
                "title": "Dashboard Rendering",
                "description": "Controls terminal redraw strategy, sizing guards, and wide-character handling.",
                "layout_hint": "rows",
                "fields": [
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
                ],
            },
            {
                "id": "logging_behavior",
                "title": "Logging Behavior",
                "description": "Choose default verbosity, emoji prefixes, and Alt+C terminal safety timings.",
                "layout_hint": "cards",
                "fields": [
                    ("app_settings", "LOG_LEVEL_DEFAULT"),
                    ("app_settings", "LOG_USE_EMOJI"),
                    ("app_settings", "LOG_EMOJI"),
                    ("app_settings", "ALT_C_DEBOUNCE_MS"),
                    ("app_settings", "ALT_C_INPUT_GUARD_MS"),
                ],
            },
        ],
    },
    {
        "id": "advanced_integration",
        "title": "Advanced Integration",
        "description": "Discord/API identity, channel targeting, and runtime file paths. These settings are advanced and easier to misconfigure.",
        "dangerous": True,
        "section_apply_scope": "Daemon restart",
        "groups": [
            {
                "id": "targeting",
                "title": "Targeting",
                "description": "The server, channel, and Mudae bot identity the runtime should talk to.",
                "layout_hint": "rows",
                "default_collapsed": True,
                "dangerous": True,
                "fields": [
                    ("app_settings", "channelId"),
                    ("app_settings", "serverId"),
                    ("app_settings", "MUDAE_BOT_ID"),
                ],
            },
            {
                "id": "discord_api",
                "title": "Discord API",
                "description": "Low-level API base and version settings used by the HTTP layer.",
                "layout_hint": "rows",
                "default_collapsed": True,
                "dangerous": True,
                "fields": [
                    ("app_settings", "DISCORD_API_BASE"),
                    ("app_settings", "DISCORD_API_VERSION_MESSAGES"),
                    ("app_settings", "DISCORD_API_VERSION_USERS"),
                ],
            },
            {
                "id": "runtime_paths",
                "title": "Runtime Paths",
                "description": "Disk locations and flush intervals for caches and metrics files.",
                "layout_hint": "rows",
                "default_collapsed": True,
                "dangerous": True,
                "fields": [
                    ("app_settings", "LAST_SEEN_PATH"),
                    ("app_settings", "LAST_SEEN_FLUSH_SEC"),
                    ("app_settings", "LATENCY_METRICS_PATH"),
                ],
            },
        ],
    },
]

FIELD_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "theme": {
        "label": "Theme Mode",
        "short_label": "Theme",
        "editor": "select",
        "options": [
            {"value": "system", "label": "System"},
            {"value": "light", "label": "Light"},
            {"value": "dark", "label": "Dark"},
        ],
        "apply_scope": "Immediate",
        "description": "Controls whether the WebUI follows the OS theme or forces a local override.",
        "control_width": "md",
    },
    "bind_host": {
        "label": "Bind Host",
        "description": "Local interface the WebUI daemon should bind to.",
        "apply_scope": "Daemon restart",
        "placeholder": "127.0.0.1",
        "control_width": "md",
    },
    "bind_port": {
        "label": "Bind Port",
        "apply_scope": "Daemon restart",
        "validation": {"min": 1, "max": 65535},
        "control_width": "xs",
    },
    "retention_days": {
        "label": "Retention Days",
        "description": "How many days of history the WebUI should retain before pruning.",
        "apply_scope": "Immediate",
        "validation": {"min": 1, "max": 3650},
        "unit": "days",
        "control_width": "xs",
    },
    "auto_open_browser": {
        "label": "Auto Open Browser",
        "description": "Open the local WebUI automatically when the daemon starts.",
        "apply_scope": "Daemon restart",
    },
    "rollCommand": {
        "label": "Roll Command",
        "editor": "select",
        "options": [{"value": "wa", "label": "$wa"}, {"value": "wx", "label": "$wx"}],
        "control_width": "sm",
    },
    "pokeRoll": {"label": "Poke Roll", "description": "Use the poke-roll path when supported by the current setup."},
    "ENABLE_INTERACTION_ID_CORRELATION": {
        "label": "Interaction ID Correlation",
        "description": "Use Discord interaction IDs when matching command responses in busy channels.",
    },
    "SLEEP_SHORT_SEC": {"label": "Short Delay", "unit": "sec", "control_width": "xs"},
    "SLEEP_MED_SEC": {"label": "Medium Delay", "unit": "sec", "control_width": "xs"},
    "SLEEP_LONG_SEC": {"label": "Long Delay", "unit": "sec", "control_width": "xs"},
    "ROLL_TRIGGER_DELAY_SEC": {"label": "Roll Trigger Delay", "unit": "sec", "control_width": "xs"},
    "KAKERA_REACT_DELAY_SEC": {"label": "Kakera React Delay", "unit": "sec", "control_width": "xs"},
    "STEAL_REACT_DELAY_SEC": {"label": "Steal React Delay", "unit": "sec", "control_width": "xs"},
    "REACT_CLICK_WAIT_SEC": {"label": "React Click Wait", "unit": "sec", "control_width": "xs"},
    "STEAL_REACT_CLICK_WAIT_SEC": {"label": "Steal React Click Wait", "unit": "sec", "control_width": "xs"},
    "WISH_CLAIM_RETRY_COUNT": {"label": "Wish Claim Retry Count", "unit": "tries", "control_width": "xs"},
    "WISH_CLAIM_RETRY_DELAY_SEC": {"label": "Wish Claim Retry Delay", "unit": "sec", "control_width": "xs"},
    "STEAL_ALLOW_TU_REFRESH": {
        "label": "Allow /tu Refresh During Steal",
        "description": "Permit a steal flow to refresh /tu if its cached status is too old.",
    },
    "STEAL_TU_MAX_AGE_SEC": {"label": "Steal /tu Max Age", "unit": "sec", "control_width": "xs"},
    "TU_INFO_REUSE_MAX_AGE_SEC": {"label": "/tu Cache Reuse Window", "unit": "sec", "control_width": "xs"},
    "WISHLIST_CACHE_TTL_SEC": {"label": "Wishlist Cache TTL", "unit": "sec", "control_width": "xs"},
    "NO_RESET_RETRY_JITTER_PCT": {"label": "No-Reset Retry Jitter", "unit": "%", "control_width": "xs"},
    "LATENCY_PROFILE_DEFAULT": {
        "label": "Latency Profile",
        "editor": "select",
        "options": [
            {"value": "aggressive_auto", "label": "Aggressive Auto"},
            {"value": "aggressive", "label": "Aggressive"},
            {"value": "balanced", "label": "Balanced"},
            {"value": "legacy", "label": "Legacy"},
        ],
        "control_width": "md",
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
        "control_width": "md",
    },
    "LATENCY_AUTO_DEGRADE": {
        "label": "Latency Auto-Degrade",
        "description": "Step down to a safer latency profile when metrics indicate the aggressive profile is risky.",
    },
    "LATENCY_METRICS_ENABLED": {
        "label": "Latency Metrics",
        "description": "Collect local latency metrics for profile tuning and debugging.",
    },
    "ROLLS_PER_RESET": {"label": "Rolls Per Reset", "unit": "rolls", "control_width": "xs"},
    "minKakeratoclaim": {"label": "Minimum Kakera to Claim", "control_width": "sm"},
    "EMOJI_CLAIM_REACT": {"label": "Claim Reaction Emoji", "control_width": "sm"},
    "EMOJI_STATUS_CLAIMED": {"label": "Claimed Status Emoji", "control_width": "sm"},
    "EMOJI_STATUS_UNCLAIMED": {"label": "Unclaimed Status Emoji", "control_width": "sm"},
    "EMOJI_KAKERA": {"label": "Kakera Emoji", "control_width": "sm"},
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
        "control_width": "sm",
    },
    "DASHBOARD_RENDERER_MODE": {
        "label": "Renderer Mode",
        "editor": "select",
        "options": [
            {"value": "auto", "label": "Auto"},
            {"value": "ansi_full", "label": "ANSI Full"},
            {"value": "win32", "label": "Win32"},
            {"value": "legacy_clear", "label": "Legacy Clear"},
            {"value": "status_line", "label": "Status Line"},
        ],
        "control_width": "md",
    },
    "KAKERA_REACTION_PRIORITY": {
        "label": "Kakera Reaction Priority",
        "description": "Priority weights for each kakera reaction button.",
        "editor": "key_value",
        "value_type": "nullable_int_map",
        "layout_hint": "panel",
        "validation": {
            "value_kind": "nullable_int",
            "allowed_keys": ["kakerap", "kakerac", "kakeral", "kakeraw", "kakerar", "kakerao", "kakerad", "kakeray", "kakerag", "kakerat", "kakerab", "kakera"],
        },
    },
    "LOG_EMOJI": {
        "label": "Log Emoji Map",
        "editor": "key_value",
        "value_type": "string_map",
        "layout_hint": "panel",
        "validation": {"value_kind": "string", "allowed_keys": ["DEBUG", "INFO", "SUCCESS", "WARN", "ERROR"]},
    },
    "Kakera_Give": {
        "label": "Kakera Give Rules",
        "description": "Transfer kakera from one account ID to another during the hourly give window.",
        "editor": "pair_list",
        "value_type": "int_pair_list",
        "layout_hint": "panel",
        "validation": {"pair_labels": ["From Account ID", "To Account ID"], "min": 1},
    },
    "Sphere_Give": {
        "label": "Sphere Give Rules",
        "description": "Transfer spheres from one account ID to another during the hourly give window.",
        "editor": "pair_list",
        "value_type": "int_pair_list",
        "layout_hint": "panel",
        "validation": {"pair_labels": ["From Account ID", "To Account ID"], "min": 1},
    },
    "steal_claim_whitelist": {
        "label": "Steal Claim Whitelist",
        "description": "Usernames or IDs the bot may steal claims from.",
        "editor": "tag_list",
        "value_type": "string_list",
        "layout_hint": "panel",
    },
    "steal_react_whitelist": {
        "label": "Steal React Whitelist",
        "description": "Usernames or IDs the bot may react-steal from.",
        "editor": "tag_list",
        "value_type": "string_list",
        "layout_hint": "panel",
    },
    "WISHLIST_NORMALIZE_TEXT": {
        "label": "Normalize Wishlist Text",
        "description": "Normalize wishlist text before matching against rolls and imported lists.",
    },
    "ROLL_COORDINATION_ENABLED": {"label": "Roll Coordination", "description": "Use a same-account lease to prevent duplicate roll sessions."},
    "ROLL_LEASE_TTL_SEC": {"label": "Roll Lease TTL", "unit": "sec", "control_width": "xs"},
    "ROLL_LEASE_HEARTBEAT_SEC": {"label": "Roll Lease Heartbeat", "unit": "sec", "control_width": "xs"},
    "ROLL_LEASE_WAIT_SEC": {"label": "Roll Lease Wait Timeout", "unit": "sec", "control_width": "xs"},
    "ROLL_STALL_REFRESH_THRESHOLD": {"label": "Roll Stall Refresh Threshold", "control_width": "xs"},
    "ROLL_STALL_ABORT_THRESHOLD": {"label": "Roll Stall Abort Threshold", "control_width": "xs"},
    "AUTO_OURO_AFTER_ROLL": {"label": "Auto-Ouro After Roll"},
    "AUTO_OH": {"label": "Auto $oh"},
    "AUTO_OC": {"label": "Auto $oc"},
    "AUTO_OQ": {"label": "Auto $oq"},
    "OQ_RAM_CACHE_MB": {"label": "OQ RAM Cache", "unit": "MB", "control_width": "sm"},
    "OQ_BEAM_K": {"label": "OQ Beam Width", "control_width": "xs"},
    "OQ_CACHE_MAX_GB": {"label": "OQ Cache Limit", "unit": "GB", "control_width": "sm"},
    "OQ_AUTO_LEARN_HIGHER_EMOJIS": {"label": "Auto-Learn Higher Emojis"},
    "OQ_HIGHER_THAN_RED_EMOJIS": {
        "label": "Higher-Than-Red Emojis",
        "editor": "ordered_list",
        "value_type": "string_list",
        "layout_hint": "panel",
    },
    "OQ_RED_EMOJI_ALIASES": {
        "label": "Red Emoji Aliases",
        "editor": "ordered_list",
        "value_type": "string_list",
        "layout_hint": "panel",
    },
    "DASHBOARD_LIVE_REDRAW": {"label": "Live Redraw"},
    "DASHBOARD_STATUS_LOG_SEC": {"label": "Status Log Interval", "unit": "sec", "control_width": "xs"},
    "DASHBOARD_FORCE_CLEAR": {"label": "Force Screen Clear"},
    "DASHBOARD_NO_SCROLL": {"label": "No-Scroll Guard"},
    "DASHBOARD_WIDECHAR_AWARE": {"label": "Wide-Character Aware"},
    "DASHBOARD_RENDER_SAFETY_COLS": {"label": "Render Safety Columns", "control_width": "xs"},
    "DASHBOARD_RENDER_SAFETY_ROWS": {"label": "Render Safety Rows", "control_width": "xs"},
    "DASHBOARD_AUTO_FIT": {"label": "Auto-Fit Dashboard"},
    "DASHBOARD_MIN_WIDTH": {"label": "Minimum Width", "control_width": "xs"},
    "DASHBOARD_MAX_WIDTH": {"label": "Maximum Width", "control_width": "xs"},
    "LOG_USE_EMOJI": {"label": "Emoji Log Prefixes"},
    "ALT_C_DEBOUNCE_MS": {"label": "Alt+C Debounce", "unit": "ms", "control_width": "xs"},
    "ALT_C_INPUT_GUARD_MS": {"label": "Alt+C Input Guard", "unit": "ms", "control_width": "xs"},
    "channelId": {"label": "Channel ID", "dangerous": True, "apply_scope": "Daemon restart", "control_width": "md"},
    "serverId": {"label": "Server ID", "dangerous": True, "apply_scope": "Daemon restart", "control_width": "md"},
    "DISCORD_API_BASE": {
        "label": "Discord API Base",
        "dangerous": True,
        "apply_scope": "Daemon restart",
        "placeholder": "https://discord.com/api",
        "control_width": "full",
    },
    "DISCORD_API_VERSION_MESSAGES": {"label": "Message API Version", "dangerous": True, "apply_scope": "Daemon restart", "control_width": "xs"},
    "DISCORD_API_VERSION_USERS": {"label": "User API Version", "dangerous": True, "apply_scope": "Daemon restart", "control_width": "xs"},
    "MUDAE_BOT_ID": {"label": "Mudae Bot ID", "dangerous": True, "apply_scope": "Daemon restart", "control_width": "md"},
    "LAST_SEEN_PATH": {
        "label": "Last-Seen Cache Path",
        "dangerous": True,
        "apply_scope": "Daemon restart",
        "control_width": "full",
    },
    "LAST_SEEN_FLUSH_SEC": {"label": "Last-Seen Flush Interval", "dangerous": True, "apply_scope": "Daemon restart", "unit": "sec", "control_width": "xs"},
    "LATENCY_METRICS_PATH": {
        "label": "Latency Metrics Path",
        "dangerous": True,
        "apply_scope": "Daemon restart",
        "control_width": "full",
    },
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
        section_fields: List[Dict[str, Any]] = []
        groups: List[Dict[str, Any]] = []
        for group in section.get("groups", []):
            group_fields: List[Dict[str, Any]] = []
            for source, key in group.get("fields", []):
                default_value = copy.deepcopy(default_app.get(key) if source == "app_settings" else DEFAULT_UI_SETTINGS.get(key))
                current_value = copy.deepcopy(current_app.get(key) if source == "app_settings" else current_ui.get(key))
                field_schema = _build_field_schema(
                    section_id=section["id"],
                    group_id=group["id"],
                    source=source,
                    key=key,
                    default_value=default_value,
                    current_value=current_value,
                )
                group_fields.append(field_schema)
                section_fields.append(field_schema)
                if source == "app_settings":
                    known_app_keys.add(key)
            groups.append(
                {
                    **group,
                    "apply_scope": _coalesce_scope(group_fields),
                    "fields": group_fields,
                }
            )
        sections.append(
            {
                **section,
                "section_apply_scope": section.get("section_apply_scope") or _coalesce_scope(section_fields),
                "groups": groups,
                "fields": section_fields,
            }
        )

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
        for group in section.get("groups", []):
            for source, key in group.get("fields", []):
                default_value = copy.deepcopy(default_app.get(key) if source == "app_settings" else DEFAULT_UI_SETTINGS.get(key))
                mapping[(source, key)] = _build_field_schema(
                    section_id=section["id"],
                    group_id=group["id"],
                    source=source,
                    key=key,
                    default_value=default_value,
                    current_value=default_value,
                )
    return mapping


def _build_field_schema(
    section_id: str,
    group_id: str,
    source: str,
    key: str,
    default_value: Any,
    current_value: Any,
) -> Dict[str, Any]:
    override = FIELD_OVERRIDES.get(key, {})
    validation = copy.deepcopy(override.get("validation") or {})
    return {
        "key": key,
        "source": source,
        "section": section_id,
        "group": group_id,
        "label": override.get("label") or _humanize_setting_key(key),
        "short_label": override.get("short_label") or override.get("label") or _humanize_setting_key(key),
        "description": override.get("description") or "",
        "help_text": override.get("help_text") or override.get("description") or "",
        "editor": override.get("editor") or _infer_editor(default_value),
        "value_type": override.get("value_type") or _infer_value_type(default_value),
        "default": copy.deepcopy(default_value),
        "value": copy.deepcopy(current_value),
        "options": copy.deepcopy(override.get("options") or []),
        "validation": validation,
        "apply_scope": override.get("apply_scope") or _default_apply_scope(section_id, key),
        "dangerous": bool(override.get("dangerous", False)),
        "editable": bool(override.get("editable", True)),
        "unit": override.get("unit"),
        "placeholder": override.get("placeholder"),
        "control_width": override.get("control_width"),
        "layout_hint": override.get("layout_hint"),
        "show_apply_scope": bool(override.get("show_apply_scope", False)),
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


def _coalesce_scope(fields: List[Dict[str, Any]]) -> str:
    scopes = {str(field.get("apply_scope") or "").strip() for field in fields if str(field.get("apply_scope") or "").strip()}
    if len(scopes) == 1:
        return next(iter(scopes))
    if not scopes:
        return ""
    return "Mixed"


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

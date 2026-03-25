from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

from mudae.config import vars as Vars
from mudae.paths import CACHE_DIR, CONFIG_DIR, ensure_runtime_dirs


WEB_CACHE_DIR = CACHE_DIR / "webui"
WEB_RUNTIME_DIR = WEB_CACHE_DIR / "runtime"
WEB_DB_PATH = WEB_CACHE_DIR / "mudae_webui.db"

DEFAULT_UI_SETTINGS: Dict[str, Any] = {
    "bind_host": "127.0.0.1",
    "bind_port": 8765,
    "retention_days": 30,
    "auto_open_browser": True,
}


def ensure_web_dirs() -> None:
    ensure_runtime_dirs()
    WEB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    WEB_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _serializable(value: Any) -> bool:
    return isinstance(value, (bool, int, float, str, list, dict))


def collect_mudae_settings() -> Dict[str, Any]:
    settings: Dict[str, Any] = {}
    for name, value in Vars.__dict__.items():
        if name.startswith("_"):
            continue
        if name in {"tokens", "wishlist"}:
            continue
        if not _serializable(value):
            continue
        settings[name] = copy.deepcopy(value)
    return settings


def normalize_account_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _normalize_int(record.get("id")),
        "name": str(record.get("name") or "").strip(),
        "discord_user_id": _normalize_str(record.get("discord_user_id")),
        "discordusername": _normalize_str(record.get("discordusername")),
        "token": str(record.get("token") or "").strip(),
        "max_power": int(record.get("max_power") or 110),
    }


def normalize_wishlist_items(items: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("value") or "").strip()
            if not name:
                continue
            priority = _normalize_int(item.get("priority")) or 2
            is_star = bool(item.get("is_star") or item.get("star") or priority >= 3)
            normalized.append(
                {
                    "name": name,
                    "priority": 3 if is_star else max(1, priority),
                    "is_star": is_star,
                }
            )
        else:
            name = str(item or "").strip()
            if not name:
                continue
            normalized.append({"name": name, "priority": 2, "is_star": False})
    return normalized


def build_initial_import_bundle() -> Dict[str, Any]:
    ensure_web_dirs()
    global_items = normalize_wishlist_items(getattr(Vars, "wishlist", []))
    accounts = [normalize_account_record(record) for record in getattr(Vars, "tokens", [])]
    return {
        "app_settings": collect_mudae_settings(),
        "ui_settings": copy.deepcopy(DEFAULT_UI_SETTINGS),
        "accounts": accounts,
        "wishlists": {
            "global": global_items,
            "accounts": {},
        },
        "artifacts": {
            "config_dir": str(CONFIG_DIR),
        },
    }


def apply_runtime_configuration(
    *,
    app_settings: Dict[str, Any],
    accounts: List[Dict[str, Any]],
    global_wishlist: List[Dict[str, Any]],
    account_wishlist: List[Dict[str, Any]],
) -> None:
    for key, value in app_settings.items():
        setattr(Vars, key, copy.deepcopy(value))

    normalized_accounts = [normalize_account_record(record) for record in accounts]
    Vars.tokens = normalized_accounts
    Vars.wishlist = normalize_wishlist_items([*global_wishlist, *account_wishlist])


def worker_paths(worker_id: str) -> Dict[str, Any]:
    ensure_web_dirs()
    return {
        "config": WEB_RUNTIME_DIR / f"{worker_id}.config.json",
        "control": WEB_RUNTIME_DIR / f"{worker_id}.control.json",
    }


def _normalize_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None

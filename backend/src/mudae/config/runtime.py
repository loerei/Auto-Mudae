from __future__ import annotations

from typing import Any, Dict

from . import vars as Vars

_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n"}


def get_bool(name: str, default: bool) -> bool:
    value = getattr(Vars, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return default


def get_int(name: str, default: int) -> int:
    value = getattr(Vars, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(name: str, default: float) -> float:
    value = getattr(Vars, name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_str(name: str, default: str) -> str:
    value = getattr(Vars, name, default)
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        return default
    return text if text else default


def get_dict(name: str, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if default is None:
        default = {}
    value = getattr(Vars, name, default)
    if isinstance(value, dict):
        return value
    return dict(default)


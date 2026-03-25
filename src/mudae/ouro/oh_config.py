import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from mudae.paths import CONFIG_DIR, ensure_runtime_dirs


ensure_runtime_dirs()

def _repo_root() -> str:
    return os.fspath(CONFIG_DIR.parent)


def default_config_path() -> str:
    return os.fspath(CONFIG_DIR / "oh_config.json")


def default_stats_path() -> str:
    return os.fspath(CONFIG_DIR / "oh_stats.json")


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class OhConfig:
    emoji_map: Dict[str, str]
    values: Dict[str, float]
    expected_values: Dict[str, float]
    reveal_counts: Dict[str, int]
    priors: Dict[str, float]
    monte_carlo_samples: int
    time_limit_sec: int


def _normalize_color_key(key: str) -> str:
    return str(key).strip().upper()


def _normalize_map(raw: Dict[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for k, v in raw.items():
        if not k:
            continue
        color = _normalize_color_key(v)
        if color:
            normalized[str(k)] = color
    return normalized


def _merge_priors(
    base_weights: Dict[str, float],
    stats_counts: Dict[str, Any],
    all_colors: Dict[str, None],
) -> Dict[str, float]:
    merged: Dict[str, float] = {}
    for color in all_colors:
        merged[color] = max(0.0, _as_float(base_weights.get(color, 0.0), 0.0))
    for color_raw, count in stats_counts.items():
        color = _normalize_color_key(color_raw)
        if not color:
            continue
        merged[color] = merged.get(color, 0.0) + max(0.0, _as_float(count, 0.0))
    total = sum(merged.values())
    if total <= 0:
        # fall back to uniform over known colors
        uniform = 1.0 / max(1, len(merged))
        return {c: uniform for c in merged}
    return {c: w / total for c, w in merged.items()}


def load_oh_config(
    config_path: Optional[str] = None,
    stats_path: Optional[str] = None
) -> OhConfig:
    cfg_path = config_path or default_config_path()
    stats_path = stats_path or default_stats_path()

    raw_cfg = _load_json(cfg_path, {})
    raw_stats = _load_json(stats_path, {})

    emoji_map = _normalize_map(raw_cfg.get("emoji_map", {}))

    values_raw = raw_cfg.get("values", {})
    values: Dict[str, float] = {}
    for k, v in values_raw.items():
        key = _normalize_color_key(k)
        if key:
            values[key] = _as_float(v, 0.0)

    expected_raw = raw_cfg.get("expected_values", {})
    expected_overrides: Dict[str, Optional[float]] = {}
    for k, v in expected_raw.items():
        key = _normalize_color_key(k)
        if not key:
            continue
        if v is None:
            expected_overrides[key] = None
        else:
            val = _as_float(v, -1.0)
            expected_overrides[key] = val if val >= 0 else None

    reveal_raw = raw_cfg.get("reveal_counts", {})
    reveal_counts: Dict[str, int] = {}
    for k, v in reveal_raw.items():
        key = _normalize_color_key(k)
        if key:
            reveal_counts[key] = max(0, _as_int(v, 0))

    prior_raw = raw_cfg.get("prior_weights", {})
    base_priors: Dict[str, float] = {}
    for k, v in prior_raw.items():
        key = _normalize_color_key(k)
        if key:
            base_priors[key] = max(0.0, _as_float(v, 0.0))

    all_colors: Dict[str, None] = {}
    for color in values:
        all_colors[color] = None
    for color in expected_overrides:
        all_colors[color] = None
    for color in base_priors:
        all_colors[color] = None
    for color in emoji_map.values():
        all_colors[color] = None

    # compute fallback expected value from base values (excluding HIDDEN if present)
    fallback_values = [v for c, v in values.items() if c != "HIDDEN"]
    fallback_expected = sum(fallback_values) / len(fallback_values) if fallback_values else 0.0

    expected_values: Dict[str, float] = {}
    for color in all_colors:
        override = expected_overrides.get(color, None)
        if override is not None:
            expected_values[color] = override
            continue
        if color in values:
            expected_values[color] = values[color]
        else:
            expected_values[color] = fallback_expected

    stats_counts = raw_stats.get("color_counts", {}) if isinstance(raw_stats, dict) else {}
    priors = _merge_priors(base_priors, stats_counts, all_colors)

    monte_carlo_samples = max(1, _as_int(raw_cfg.get("monte_carlo_samples", 64), 64))
    time_limit_sec = max(30, _as_int(raw_cfg.get("time_limit_sec", 120), 120))

    return OhConfig(
        emoji_map=emoji_map,
        values=values,
        expected_values=expected_values,
        reveal_counts=reveal_counts,
        priors=priors,
        monte_carlo_samples=monte_carlo_samples,
        time_limit_sec=time_limit_sec,
    )


def update_stats(
    stats_path: Optional[str],
    color_counts_delta: Dict[str, int]
) -> None:
    path = stats_path or default_stats_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stats = _load_json(path, {})
    if not isinstance(stats, dict):
        stats = {}
    counts = stats.get("color_counts")
    if not isinstance(counts, dict):
        counts = {}

    for color, delta in color_counts_delta.items():
        if not color:
            continue
        key = _normalize_color_key(color)
        if not key:
            continue
        prev = _as_int(counts.get(key, 0), 0)
        new_val = prev + int(delta)
        counts[key] = max(0, new_val)

    stats["color_counts"] = counts
    stats["total_observed"] = max(0, sum(_as_int(v, 0) for v in counts.values()))
    stats["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

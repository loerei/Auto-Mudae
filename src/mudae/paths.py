from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
LOGS_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = PROJECT_ROOT / "cache"
OURO_CACHE_DIR = CACHE_DIR / "ouro"
ARCHIVE_DIR = PROJECT_ROOT / "archive"


def ensure_runtime_dirs() -> None:
    for directory in (CONFIG_DIR, LOGS_DIR, CACHE_DIR, OURO_CACHE_DIR, ARCHIVE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


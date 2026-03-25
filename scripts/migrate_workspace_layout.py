#!/usr/bin/env python3
"""
One-shot helper for workspace structure migration.

This script is idempotent and intentionally conservative: it only ensures
required directories exist and reports key paths used by the refactored layout.
"""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = [
        root / "archive" / "legacy",
        root / "cache" / "ouro",
        root / "logs",
        root / "config",
        root / "src" / "mudae",
        root / "tests" / "unit",
    ]
    for path in expected:
        path.mkdir(parents=True, exist_ok=True)

    print("Workspace migration directories are present:")
    for path in expected:
        print(f"- {path}")


if __name__ == "__main__":
    main()


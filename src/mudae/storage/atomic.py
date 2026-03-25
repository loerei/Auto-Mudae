from __future__ import annotations

import json
import os
import pickle
import time
import uuid
from pathlib import Path
from typing import Any


def _coerce_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.fspath(path))


def _temp_path(path: Path) -> Path:
    suffix = f".{os.getpid()}.{time.time_ns()}.{uuid.uuid4().hex}.tmp"
    return path.with_name(f"{path.name}{suffix}")


def atomic_write_bytes(path: str | os.PathLike[str], payload: bytes) -> None:
    target = _coerce_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _temp_path(target)
    with open(tmp_path, "wb") as handle:
        handle.write(payload)
    os.replace(tmp_path, target)


def atomic_write_text(
    path: str | os.PathLike[str],
    payload: str,
    *,
    encoding: str = "utf-8",
) -> None:
    atomic_write_bytes(path, payload.encode(encoding))


def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = None,
    sort_keys: bool = False,
) -> None:
    text = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
    )
    atomic_write_text(path, text, encoding=encoding)


def atomic_write_pickle(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    protocol: int = pickle.HIGHEST_PROTOCOL,
) -> None:
    atomic_write_bytes(path, pickle.dumps(payload, protocol=protocol))

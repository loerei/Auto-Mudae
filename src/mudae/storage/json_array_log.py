import json
import os
import threading
from typing import Any, Dict, Iterator, Optional


def ensure_json_array_file(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(b"[]\n")


def _find_last_non_ws_byte(f) -> Optional[tuple[int, int]]:
    f.seek(0, os.SEEK_END)
    size = f.tell()
    if size <= 0:
        return None
    pos = size
    while pos > 0:
        step = min(4096, pos)
        pos -= step
        f.seek(pos, os.SEEK_SET)
        buf = f.read(step)
        for i in range(len(buf) - 1, -1, -1):
            b = buf[i]
            if b not in (9, 10, 13, 32):  # \t \n \r space
                return (pos + i, b)
    return None


def _find_prev_non_ws_byte(f, before_pos: int) -> Optional[tuple[int, int]]:
    if before_pos <= 0:
        return None
    pos = before_pos
    while pos > 0:
        step = min(4096, pos)
        pos -= step
        f.seek(pos, os.SEEK_SET)
        buf = f.read(step)
        # Only consider bytes strictly before before_pos
        max_i = min(len(buf) - 1, before_pos - pos - 1)
        for i in range(max_i, -1, -1):
            b = buf[i]
            if b not in (9, 10, 13, 32):
                return (pos + i, b)
    return None


def append_json_array(path: str, obj: Dict[str, Any], lock: Optional[threading.Lock] = None) -> None:
    """
    Append an object to a JSON array file without rewriting the whole file.
    The file is kept as a valid JSON array at all times.
    """
    if lock is None:
        _lock = threading.Lock()
        lock = _lock

    with lock:
        ensure_json_array_file(path)
        encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        with open(path, "r+b") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"[]\n")
                f.flush()

            last = _find_last_non_ws_byte(f)
            if last is None:
                f.seek(0, os.SEEK_SET)
                f.truncate(0)
                f.write(b"[]\n")
                f.flush()
                last = _find_last_non_ws_byte(f)
                if last is None:
                    raise ValueError("Failed to initialize JSON array file")

            last_pos, last_byte = last
            if last_byte != ord("]"):
                raise ValueError(f"Invalid JSON array file (missing closing ']'): {path}")

            prev = _find_prev_non_ws_byte(f, last_pos)
            if prev is None:
                raise ValueError(f"Invalid JSON array file (no '[' found): {path}")
            _prev_pos, prev_byte = prev
            is_empty = prev_byte == ord("[")

            # Remove the closing bracket and append the next element, then close again.
            f.seek(last_pos, os.SEEK_SET)
            f.truncate()
            if is_empty:
                f.write(b"\n")
                f.write(encoded)
                f.write(b"\n]")
            else:
                f.write(b",\n")
                f.write(encoded)
                f.write(b"\n]")
            f.write(b"\n")
            f.flush()


def iter_json_array(path: str) -> Iterator[Any]:
    """
    Stream items from a JSON array on disk without loading the whole file.
    Assumes the file is a valid JSON array.
    """
    decoder = json.JSONDecoder()
    with open(path, "r", encoding="utf-8") as f:
        buf = ""

        def _read_more() -> bool:
            nonlocal buf
            chunk = f.read(65536)
            if not chunk:
                return False
            buf += chunk
            return True

        # Read until we see '['
        while True:
            if not buf and not _read_more():
                return
            stripped = buf.lstrip()
            if stripped:
                if stripped[0] != "[":
                    raise ValueError(f"Expected JSON array '[' in {path}")
                # Drop up to and including '['
                drop = buf.find("[") + 1
                buf = buf[drop:]
                break
            buf = ""

        while True:
            # Skip whitespace and commas
            while True:
                if not buf and not _read_more():
                    return
                s = buf.lstrip()
                if not s:
                    buf = ""
                    continue
                if s[0] == ",":
                    # drop through comma
                    comma_idx = buf.find(",")
                    buf = buf[comma_idx + 1 :]
                    continue
                if s[0] == "]":
                    return
                # Align buf to first non-ws char
                buf = s
                break

            while True:
                try:
                    obj, end = decoder.raw_decode(buf)
                    yield obj
                    buf = buf[end:]
                    break
                except json.JSONDecodeError:
                    if not _read_more():
                        raise


"""
Oq_solver.py - Beam runtime solver for Ouro Quest ($oq).
Runtime policy uses greedy-filtered beam search (k=3 by default) for faster decisions.
An exact reference evaluator is retained for internal tests and benchmarking only.
"""

from __future__ import annotations

import argparse
import atexit
import itertools
import math
import os
import pickle
import sqlite3
import sys
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from mudae.paths import OURO_CACHE_DIR, ensure_runtime_dirs

GRID_SIZE = 5
TOTAL_CELLS = GRID_SIZE * GRID_SIZE
TOTAL_PURPLES = 4
TARGET_PURPLES = 3
MAX_CLICKS = 7
OQ_DEFAULT_RAM_CACHE_MB = 512
OQ_DEFAULT_CACHE_MAX_GB = 10.0
OQ_BEAM_K_DEFAULT = 3
OQ_POLICY_MODE_BEAM = "beam_k"
OQ_POLICY_MODE_EXACT_REF = "exact_ref"
OQ_BEAM_P_PURPLE_WEIGHT = 1.0
OQ_BEAM_ENTROPY_WEIGHT = 0.35
OQ_BEAM_CENTER_WEIGHT = 0.05
OQ_CACHE_VERSION_DEFAULT = "beam3_v1"
OQ_LEGACY_CACHE_VERSION = "exact_v1"
OQ_STATE_CACHE_FILENAME_TEMPLATE = "oq_success_prob_{version}.sqlite3"
OQ_FIRST_SUGGESTION_FILENAME_TEMPLATE = "oq_first_suggestion_{version}.pkl"
OQ_LEGACY_CACHE_FILENAME = f"oq_success_prob_{OQ_LEGACY_CACHE_VERSION}.pkl"
OQ_SQLITE_WRITE_BATCH_SIZE = 512
OQ_SQLITE_CACHE_MB_MIN = 64
OQ_SQLITE_CACHE_MB_MAX = 1024
OQ_SQLITE_MMAP_MB_MIN = 128
OQ_SQLITE_MMAP_MB_MAX = 2048

OBS_BLUE = 0
OBS_TEAL = 1
OBS_GREEN = 2
OBS_YELLOW = 3
OBS_ORANGE = 4
OBS_PURPLE = 5
OBS_MAX = OBS_PURPLE

OBS_NAME = {
    OBS_BLUE: "BLUE",
    OBS_TEAL: "TEAL",
    OBS_GREEN: "GREEN",
    OBS_YELLOW: "YELLOW",
    OBS_ORANGE: "ORANGE",
    OBS_PURPLE: "PURPLE",
}

OBS_SHORT = {
    OBS_BLUE: "B",
    OBS_TEAL: "T",
    OBS_GREEN: "G",
    OBS_YELLOW: "Y",
    OBS_ORANGE: "O",
    OBS_PURPLE: "P",
}

POSITIONS = list(range(TOTAL_CELLS))
StateKey = Tuple[int, int, int, int]
BuildProgressCallback = Callable[[Dict[str, Any]], None]
StateProgressCallback = Callable[[], None]


def idx_to_rc(idx: int) -> Tuple[int, int]:
    return divmod(idx, GRID_SIZE)


def rc_to_idx(r: int, c: int) -> int:
    return r * GRID_SIZE + c


def build_neighbors() -> List[List[int]]:
    neighbors: List[List[int]] = []
    for idx in POSITIONS:
        r, c = idx_to_rc(idx)
        adj = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE:
                    adj.append(rc_to_idx(nr, nc))
        neighbors.append(adj)
    return neighbors


NEIGHBORS = build_neighbors()


def build_configs() -> List[Tuple[int, ...]]:
    return list(itertools.combinations(POSITIONS, TOTAL_PURPLES))


CONFIGS = build_configs()
TOTAL_CONFIGS = len(CONFIGS)
ALL_CONFIG_MASK = (1 << TOTAL_CONFIGS) - 1


def _build_obs_masks() -> List[Tuple[int, ...]]:
    obs_masks: List[List[int]] = [[0 for _ in range(OBS_MAX + 1)] for _ in range(TOTAL_CELLS)]
    for config_idx, config in enumerate(CONFIGS):
        config_set = set(config)
        for pos in POSITIONS:
            if pos in config_set:
                obs = OBS_PURPLE
            else:
                count = 0
                for n in NEIGHBORS[pos]:
                    if n in config_set:
                        count += 1
                obs = count
            obs_masks[pos][obs] |= (1 << config_idx)
    return [tuple(row) for row in obs_masks]


OBS_MASKS = _build_obs_masks()
OBS_ENTROPY_MAX = math.log2(float(OBS_MAX + 1))


def _build_center_bias() -> Tuple[float, ...]:
    center = GRID_SIZE // 2
    max_dist = max(1, (GRID_SIZE - 1) + (GRID_SIZE - 1))
    values: List[float] = []
    for pos in POSITIONS:
        r, c = idx_to_rc(pos)
        dist = abs(r - center) + abs(c - center)
        bias = 1.0 - (float(dist) / float(max_dist))
        values.append(max(0.0, min(1.0, bias)))
    return tuple(values)


CENTER_BIAS = _build_center_bias()


def _policy_signature(policy_mode: str, beam_k: int) -> str:
    if policy_mode == OQ_POLICY_MODE_BEAM:
        return (
            f"beam_k={int(beam_k)}"
            f";w_p={OQ_BEAM_P_PURPLE_WEIGHT:.3f}"
            f";w_e={OQ_BEAM_ENTROPY_WEIGHT:.3f}"
            f";w_c={OQ_BEAM_CENTER_WEIGHT:.3f}"
        )
    if policy_mode == OQ_POLICY_MODE_EXACT_REF:
        return "exact_ref_v1"
    raise ValueError(f"Unknown policy mode: {policy_mode}")


def _state_cache_path(cache_version: str) -> Path:
    return OURO_CACHE_DIR / OQ_STATE_CACHE_FILENAME_TEMPLATE.format(version=cache_version)


def _first_suggestion_cache_path(cache_version: str) -> Path:
    return OURO_CACHE_DIR / OQ_FIRST_SUGGESTION_FILENAME_TEMPLATE.format(version=cache_version)


def _legacy_cache_path() -> Path:
    return OURO_CACHE_DIR / OQ_LEGACY_CACHE_FILENAME


def _int_to_blob(value: int) -> bytes:
    if value <= 0:
        return b"\x00"
    return value.to_bytes((value.bit_length() + 7) // 8, byteorder="big", signed=False)


def _blob_to_int(blob: bytes) -> int:
    if not blob:
        return 0
    return int.from_bytes(blob, byteorder="big", signed=False)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        value = int(raw.strip())
        return value if value > 0 else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        value = float(raw.strip())
        return value if value > 0 else float(default)
    except Exception:
        return float(default)


def _recommended_write_batch_size(cache_ram_mb: int) -> int:
    if cache_ram_mb >= 4096:
        return 8192
    if cache_ram_mb >= 2048:
        return 4096
    if cache_ram_mb >= 1024:
        return 2048
    if cache_ram_mb >= 512:
        return 1024
    return OQ_SQLITE_WRITE_BATCH_SIZE


def _recommended_sqlite_cache_mb(cache_ram_mb: int) -> int:
    # Reserve a quarter of RAM budget for SQLite page cache, capped for stability.
    return max(
        OQ_SQLITE_CACHE_MB_MIN,
        min(OQ_SQLITE_CACHE_MB_MAX, int(cache_ram_mb) // 4),
    )


def _recommended_sqlite_mmap_mb(cache_ram_mb: int) -> int:
    # mmap speeds random lookups; keep it bounded to avoid address-space pressure.
    return max(
        OQ_SQLITE_MMAP_MB_MIN,
        min(OQ_SQLITE_MMAP_MB_MAX, int(cache_ram_mb) // 2),
    )


class OqStateCache:
    def __init__(
        self,
        cache_path: Path,
        *,
        cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
        version: str = OQ_CACHE_VERSION_DEFAULT,
        policy_signature: str = "",
        max_db_bytes: Optional[int] = None,
        enable_legacy_migration: bool = False,
    ) -> None:
        self.cache_path = cache_path
        self.version = version
        self.policy_signature = policy_signature
        self.cache_ram_mb = max(1, int(cache_ram_mb))
        self.memory_limit_bytes = max(1, self.cache_ram_mb * 1024 * 1024)
        self.write_batch_size = _recommended_write_batch_size(self.cache_ram_mb)
        self.sqlite_cache_mb = _recommended_sqlite_cache_mb(self.cache_ram_mb)
        self.sqlite_mmap_mb = _recommended_sqlite_mmap_mb(self.cache_ram_mb)
        self.max_db_bytes = int(max_db_bytes) if max_db_bytes and max_db_bytes > 0 else None
        self.enable_legacy_migration = bool(enable_legacy_migration)

        self._memory: OrderedDict[StateKey, float] = OrderedDict()
        self._memory_sizes: Dict[StateKey, int] = {}
        self._memory_bytes = 0
        self._largest_entry_bytes = 0

        self._conn: Optional[sqlite3.Connection] = None
        self._db_available = False
        self._pending_rows: List[Tuple[bytes, int, int, int, float]] = []

        self._hits_memory = 0
        self._hits_disk = 0
        self._misses = 0
        self._writes = 0
        self._evictions = 0
        self._migrated_entries = 0
        self._db_writes_disabled = False
        self._db_cap_warned = False
        self._db_size_bytes_last = 0

        self._open_db()

    def _open_db(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(os.fspath(self.cache_path), timeout=30.0)

            def _safe_pragma(statement: str) -> None:
                try:
                    conn.execute(statement)
                except Exception:
                    pass

            _safe_pragma("PRAGMA busy_timeout=60000;")
            _safe_pragma("PRAGMA journal_mode=WAL;")
            _safe_pragma("PRAGMA synchronous=NORMAL;")
            _safe_pragma("PRAGMA temp_store=MEMORY;")
            _safe_pragma(f"PRAGMA cache_size=-{self.sqlite_cache_mb * 1024};")
            _safe_pragma(f"PRAGMA mmap_size={self.sqlite_mmap_mb * 1024 * 1024};")
            _safe_pragma("PRAGMA wal_autocheckpoint=20000;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS states (
                    possible_mask BLOB NOT NULL,
                    revealed_mask INTEGER NOT NULL,
                    found_purples INTEGER NOT NULL,
                    clicks_left INTEGER NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (possible_mask, revealed_mask, found_purples, clicks_left)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn = conn
            self._db_available = True

            existing = self._meta_get("version")
            existing_policy = self._meta_get("policy_signature")
            should_clear = False
            if existing and existing != self.version:
                should_clear = True
            if existing_policy != self.policy_signature:
                should_clear = True
            if should_clear:
                conn.execute("DELETE FROM states")
                conn.commit()
            self._meta_set("version", self.version)
            self._meta_set("policy_signature", self.policy_signature)
            if self.enable_legacy_migration:
                self._maybe_migrate_legacy_cache()
        except Exception as exc:
            self._conn = None
            self._db_available = False
            print(f"[OQ] Cache DB unavailable ({exc}). Continuing with RAM cache only.")

    def _meta_get(self, key: str) -> Optional[str]:
        if not self._db_available or self._conn is None:
            return None
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row[0])

    def _meta_set(self, key: str, value: str) -> None:
        if not self._db_available or self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def _db_row_count(self) -> int:
        if not self._db_available or self._conn is None:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM states").fetchone()
        return int(row[0] if row else 0)

    def _current_db_disk_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            path = os.fspath(self.cache_path) + suffix
            try:
                total += int(os.path.getsize(path))
            except Exception:
                continue
        self._db_size_bytes_last = total
        return total

    def _enforce_db_size_cap(self) -> bool:
        if self.max_db_bytes is None:
            return False
        size_now = self._current_db_disk_bytes()
        if size_now < self.max_db_bytes:
            return False
        self._db_writes_disabled = True
        if not self._db_cap_warned:
            cap_gb = float(self.max_db_bytes) / (1024.0 * 1024.0 * 1024.0)
            now_gb = float(size_now) / (1024.0 * 1024.0 * 1024.0)
            print(
                f"[OQ] Cache DB write cap reached ({now_gb:.2f}GB >= {cap_gb:.2f}GB). "
                "Switching to RAM-only writes for this run."
            )
            self._db_cap_warned = True
        return True

    def _maybe_migrate_legacy_cache(self) -> None:
        if not self._db_available or self._conn is None:
            return
        if self._db_row_count() > 0:
            return
        migrated_flag = self._meta_get("legacy_migration_complete")
        if migrated_flag == self.version:
            return

        legacy_path = _legacy_cache_path()
        if not legacy_path.exists():
            self._meta_set("legacy_migration_complete", self.version)
            return

        try:
            with open(legacy_path, "rb") as f:
                payload = pickle.load(f)
            if not isinstance(payload, dict) or payload.get("version") != OQ_LEGACY_CACHE_VERSION:
                self._meta_set("legacy_migration_complete", self.version)
                return
            legacy_cache = payload.get("cache")
            if not isinstance(legacy_cache, dict):
                self._meta_set("legacy_migration_complete", self.version)
                return

            batch: List[Tuple[bytes, int, int, int, float]] = []
            for key, value in legacy_cache.items():
                if not isinstance(key, tuple) or len(key) != 4:
                    continue
                possible_mask, revealed_mask, found_purples, clicks_left = key
                row = (
                    _int_to_blob(int(possible_mask)),
                    int(revealed_mask),
                    int(found_purples),
                    int(clicks_left),
                    float(value),
                )
                batch.append(row)
                if len(batch) >= self.write_batch_size:
                    self._conn.executemany(
                        """
                        INSERT OR REPLACE INTO states
                        (possible_mask, revealed_mask, found_purples, clicks_left, value)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        batch,
                    )
                    self._migrated_entries += len(batch)
                    batch.clear()
            if batch:
                self._conn.executemany(
                    """
                    INSERT OR REPLACE INTO states
                    (possible_mask, revealed_mask, found_purples, clicks_left, value)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                self._migrated_entries += len(batch)
            self._conn.commit()
            self._meta_set("legacy_migration_complete", self.version)
            if self._migrated_entries:
                print(f"[OQ] Migrated {self._migrated_entries} states from {legacy_path.name}")
        except Exception as exc:
            print(f"[OQ] Legacy cache migration skipped ({exc}).")
            self._meta_set("legacy_migration_complete", self.version)

    def _estimate_entry_bytes(self, key: StateKey, value: float) -> int:
        possible_mask, revealed_mask, found_purples, clicks_left = key
        return (
            64
            + sys.getsizeof(key)
            + sys.getsizeof(possible_mask)
            + sys.getsizeof(revealed_mask)
            + sys.getsizeof(found_purples)
            + sys.getsizeof(clicks_left)
            + sys.getsizeof(value)
        )

    def _remember(self, key: StateKey, value: float) -> None:
        if key in self._memory:
            self._memory[key] = value
            self._memory.move_to_end(key)
            return

        entry_bytes = self._estimate_entry_bytes(key, value)
        self._largest_entry_bytes = max(self._largest_entry_bytes, entry_bytes)
        self._memory[key] = value
        self._memory_sizes[key] = entry_bytes
        self._memory_bytes += entry_bytes

        while len(self._memory) > 1 and self._memory_bytes > self.memory_limit_bytes:
            old_key, _ = self._memory.popitem(last=False)
            self._memory_bytes -= self._memory_sizes.pop(old_key, 0)
            self._evictions += 1

    def get(self, key: StateKey) -> Optional[float]:
        in_mem = self._memory.get(key)
        if in_mem is not None:
            self._hits_memory += 1
            self._memory.move_to_end(key)
            return in_mem

        if self._db_available and self._conn is not None:
            possible_mask, revealed_mask, found_purples, clicks_left = key
            row = self._conn.execute(
                """
                SELECT value FROM states
                WHERE possible_mask = ?
                AND revealed_mask = ?
                AND found_purples = ?
                AND clicks_left = ?
                """,
                (_int_to_blob(possible_mask), revealed_mask, found_purples, clicks_left),
            ).fetchone()
            if row is not None:
                value = float(row[0])
                self._hits_disk += 1
                self._remember(key, value)
                return value

        self._misses += 1
        return None

    def set(self, key: StateKey, value: float) -> None:
        self._remember(key, value)
        self._writes += 1

        if self._db_available and self._conn is not None and not self._db_writes_disabled:
            possible_mask, revealed_mask, found_purples, clicks_left = key
            self._pending_rows.append(
                (
                    _int_to_blob(possible_mask),
                    revealed_mask,
                    found_purples,
                    clicks_left,
                    float(value),
                )
            )
            if len(self._pending_rows) >= self.write_batch_size:
                self.flush()

    def flush(self) -> None:
        if not self._pending_rows:
            return
        if not self._db_available or self._conn is None:
            self._pending_rows.clear()
            return
        if self._db_writes_disabled or self._enforce_db_size_cap():
            self._pending_rows.clear()
            return
        try:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO states
                (possible_mask, revealed_mask, found_purples, clicks_left, value)
                VALUES (?, ?, ?, ?, ?)
                """,
                self._pending_rows,
            )
            self._conn.commit()
            self._enforce_db_size_cap()
        finally:
            self._pending_rows.clear()

    def close(self) -> None:
        self.flush()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._db_available = False

    def stats(self, include_db: bool = True) -> Dict[str, int]:
        stats = {
            "memory_entries": len(self._memory),
            "memory_bytes": self._memory_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "largest_entry_bytes": self._largest_entry_bytes,
            "pending_rows": len(self._pending_rows),
            "hits_memory": self._hits_memory,
            "hits_disk": self._hits_disk,
            "misses": self._misses,
            "writes": self._writes,
            "evictions": self._evictions,
            "migrated_entries": self._migrated_entries,
            "write_batch_size": self.write_batch_size,
            "sqlite_cache_mb": self.sqlite_cache_mb,
            "sqlite_mmap_mb": self.sqlite_mmap_mb,
            "db_writes_disabled": int(self._db_writes_disabled),
            "max_db_bytes": int(self.max_db_bytes or 0),
            "db_size_bytes": int(self._current_db_disk_bytes() if self._db_available else 0),
        }
        if include_db:
            self.flush()
            stats["db_entries"] = self._db_row_count()
        return stats


_GLOBAL_STATE_CACHE: Optional[OqStateCache] = None
_GLOBAL_STATE_CACHE_KEY: Optional[Tuple[str, int, str, str, int]] = None


def reset_global_state_cache() -> None:
    global _GLOBAL_STATE_CACHE, _GLOBAL_STATE_CACHE_KEY
    if _GLOBAL_STATE_CACHE is not None:
        _GLOBAL_STATE_CACHE.close()
    _GLOBAL_STATE_CACHE = None
    _GLOBAL_STATE_CACHE_KEY = None


def _get_state_cache(
    *,
    cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    policy_signature: str,
    max_cache_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
    enable_legacy_migration: bool = False,
    force_rebuild: bool = False,
) -> OqStateCache:
    global _GLOBAL_STATE_CACHE, _GLOBAL_STATE_CACHE_KEY
    ensure_runtime_dirs()
    cache_path = _state_cache_path(cache_version)
    max_db_bytes = int(max(0.0, float(max_cache_gb)) * 1024 * 1024 * 1024)
    key = (
        os.fspath(cache_path),
        int(cache_ram_mb),
        cache_version,
        policy_signature,
        max_db_bytes,
    )

    if force_rebuild:
        reset_global_state_cache()
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _first_suggestion_cache_path(cache_version).unlink(missing_ok=True)
        except Exception:
            pass

    if _GLOBAL_STATE_CACHE is None or _GLOBAL_STATE_CACHE_KEY != key:
        reset_global_state_cache()
        _GLOBAL_STATE_CACHE = OqStateCache(
            cache_path=cache_path,
            cache_ram_mb=cache_ram_mb,
            version=cache_version,
            policy_signature=policy_signature,
            max_db_bytes=max_db_bytes,
            enable_legacy_migration=enable_legacy_migration,
        )
        _GLOBAL_STATE_CACHE_KEY = key
    return _GLOBAL_STATE_CACHE


atexit.register(reset_global_state_cache)


def _first_suggestion_cache_key(
    max_clicks: int,
    cache_version: str,
    policy_signature: str,
) -> Tuple[Any, ...]:
    return (
        cache_version,
        policy_signature,
        GRID_SIZE,
        TOTAL_PURPLES,
        TARGET_PURPLES,
        max_clicks,
    )


def _load_first_suggestion(max_clicks: int, cache_version: str, policy_signature: str) -> Optional[int]:
    path = _first_suggestion_cache_path(cache_version)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if (
            isinstance(payload, dict)
            and payload.get("key") == _first_suggestion_cache_key(max_clicks, cache_version, policy_signature)
            and isinstance(payload.get("pos"), int)
        ):
            return int(payload["pos"])
    except Exception:
        return None
    return None


def _save_first_suggestion(max_clicks: int, cache_version: str, policy_signature: str, pos: int) -> None:
    path = _first_suggestion_cache_path(cache_version)
    try:
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "key": _first_suggestion_cache_key(max_clicks, cache_version, policy_signature),
                    "pos": int(pos),
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except Exception:
        pass


def _best_success_prob_exact(
    cache: OqStateCache,
    possible_mask: int,
    revealed_mask: int,
    found_purples: int,
    clicks_left: int,
    state_progress_callback: Optional[StateProgressCallback] = None,
) -> float:
    key: StateKey = (possible_mask, revealed_mask, found_purples, clicks_left)
    cached = cache.get(key)
    if cached is not None:
        return cached

    if found_purples >= TARGET_PURPLES:
        cache.set(key, 1.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 1.0
    if clicks_left <= 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0
    if possible_mask == 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0

    total = possible_mask.bit_count()
    if total == 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0

    best_val = 0.0
    for pos in POSITIONS:
        if revealed_mask & (1 << pos):
            continue
        expected = 0.0
        masks_for_pos = OBS_MASKS[pos]
        for obs_code, obs_mask in enumerate(masks_for_pos):
            subset = possible_mask & obs_mask
            if subset == 0:
                continue
            prob = subset.bit_count() / total
            if obs_code == OBS_PURPLE:
                val = _best_success_prob_exact(
                    cache,
                    subset,
                    revealed_mask | (1 << pos),
                    found_purples + 1,
                    clicks_left,
                    state_progress_callback=state_progress_callback,
                )
            else:
                val = _best_success_prob_exact(
                    cache,
                    subset,
                    revealed_mask | (1 << pos),
                    found_purples,
                    clicks_left - 1,
                    state_progress_callback=state_progress_callback,
                )
            expected += prob * val
        if expected > best_val:
            best_val = expected
            if best_val >= 1.0:
                break
    cache.set(key, best_val)
    if state_progress_callback is not None:
        state_progress_callback()
    return best_val


def _beam_candidate_positions(
    possible_mask: int,
    revealed_mask: int,
    *,
    beam_k: int,
) -> List[int]:
    if beam_k <= 0:
        beam_k = OQ_BEAM_K_DEFAULT
    total = possible_mask.bit_count()
    if total <= 0:
        return []

    scored: List[Tuple[float, int]] = []
    for pos in POSITIONS:
        if revealed_mask & (1 << pos):
            continue
        masks_for_pos = OBS_MASKS[pos]
        purple_count = (possible_mask & masks_for_pos[OBS_PURPLE]).bit_count()
        p_purple = purple_count / total

        entropy = 0.0
        for obs_mask in masks_for_pos:
            subset_count = (possible_mask & obs_mask).bit_count()
            if subset_count <= 0:
                continue
            prob = subset_count / total
            entropy -= prob * math.log2(prob)
        entropy_norm = entropy / OBS_ENTROPY_MAX if OBS_ENTROPY_MAX > 0 else 0.0
        score = (
            (OQ_BEAM_P_PURPLE_WEIGHT * p_purple)
            + (OQ_BEAM_ENTROPY_WEIGHT * entropy_norm)
            + (OQ_BEAM_CENTER_WEIGHT * CENTER_BIAS[pos])
        )
        scored.append((score, pos))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [pos for _, pos in scored[: max(1, beam_k)]]


def _best_success_prob_beam(
    cache: OqStateCache,
    possible_mask: int,
    revealed_mask: int,
    found_purples: int,
    clicks_left: int,
    *,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    state_progress_callback: Optional[StateProgressCallback] = None,
) -> float:
    key: StateKey = (possible_mask, revealed_mask, found_purples, clicks_left)
    cached = cache.get(key)
    if cached is not None:
        return cached

    if found_purples >= TARGET_PURPLES:
        cache.set(key, 1.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 1.0
    if clicks_left <= 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0
    if possible_mask == 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0

    total = possible_mask.bit_count()
    if total == 0:
        cache.set(key, 0.0)
        if state_progress_callback is not None:
            state_progress_callback()
        return 0.0

    candidate_positions = _beam_candidate_positions(
        possible_mask,
        revealed_mask,
        beam_k=beam_k,
    )
    best_val = 0.0
    for pos in candidate_positions:
        expected = 0.0
        for obs_code, obs_mask in enumerate(OBS_MASKS[pos]):
            subset = possible_mask & obs_mask
            if subset == 0:
                continue
            prob = subset.bit_count() / total
            if obs_code == OBS_PURPLE:
                val = _best_success_prob_beam(
                    cache,
                    subset,
                    revealed_mask | (1 << pos),
                    found_purples + 1,
                    clicks_left,
                    beam_k=beam_k,
                    state_progress_callback=state_progress_callback,
                )
            else:
                val = _best_success_prob_beam(
                    cache,
                    subset,
                    revealed_mask | (1 << pos),
                    found_purples,
                    clicks_left - 1,
                    beam_k=beam_k,
                    state_progress_callback=state_progress_callback,
                )
            expected += prob * val
        if expected > best_val:
            best_val = expected
            if best_val >= 1.0:
                break

    cache.set(key, best_val)
    if state_progress_callback is not None:
        state_progress_callback()
    return best_val


class OqSolver:
    def __init__(
        self,
        max_clicks: int = MAX_CLICKS,
        cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
        cache_version: str = OQ_CACHE_VERSION_DEFAULT,
        policy_mode: str = OQ_POLICY_MODE_BEAM,
        beam_k: int = OQ_BEAM_K_DEFAULT,
        cache_max_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
    ):
        self.max_clicks = max_clicks
        self.cache_ram_mb = int(cache_ram_mb)
        self.cache_version = cache_version
        if policy_mode not in {OQ_POLICY_MODE_BEAM, OQ_POLICY_MODE_EXACT_REF}:
            raise ValueError(f"Unknown policy mode: {policy_mode}")
        self.policy_mode = policy_mode
        self.beam_k = max(1, int(beam_k))
        self.cache_max_gb = float(cache_max_gb)
        self.policy_signature = _policy_signature(self.policy_mode, self.beam_k)
        self._cache = _get_state_cache(
            cache_ram_mb=self.cache_ram_mb,
            cache_version=self.cache_version,
            policy_signature=self.policy_signature,
            max_cache_gb=self.cache_max_gb,
            enable_legacy_migration=(self.policy_mode == OQ_POLICY_MODE_EXACT_REF),
        )

        self.possible_mask = ALL_CONFIG_MASK
        self.revealed_mask = 0
        self.found_purples = 0
        self.clicks_left = max_clicks
        self.known_purples: Set[int] = set()
        self.red_pos: Optional[int] = None

    @property
    def clicks_used(self) -> int:
        return self.max_clicks - self.clicks_left

    def reset_possible(self) -> None:
        self.possible_mask = ALL_CONFIG_MASK

    def is_revealed(self, pos_idx: int) -> bool:
        return bool(self.revealed_mask & (1 << pos_idx))

    def apply_observation(self, pos_idx: int, obs_code: int) -> None:
        if self.is_revealed(pos_idx):
            return
        self.possible_mask &= OBS_MASKS[pos_idx][obs_code]
        self.revealed_mask |= (1 << pos_idx)
        if obs_code == OBS_PURPLE:
            self.found_purples += 1
            self.known_purples.add(pos_idx)
        else:
            if self.clicks_left > 0:
                self.clicks_left -= 1
        if self.possible_mask == 0:
            self.reset_possible()

    def note_red(self, pos_idx: int) -> None:
        self.red_pos = pos_idx
        if not self.is_revealed(pos_idx):
            self.apply_observation(pos_idx, OBS_PURPLE)
        else:
            self.known_purples.add(pos_idx)

    def consume_click(self, amount: int = 1) -> None:
        if amount <= 0:
            return
        self.clicks_left = max(0, self.clicks_left - amount)

    def current_success_prob(self) -> float:
        if self.policy_mode == OQ_POLICY_MODE_EXACT_REF:
            return _best_success_prob_exact(
                self._cache,
                self.possible_mask,
                self.revealed_mask,
                self.found_purples,
                self.clicks_left,
            )
        return _best_success_prob_beam(
            self._cache,
            self.possible_mask,
            self.revealed_mask,
            self.found_purples,
            self.clicks_left,
            beam_k=self.beam_k,
        )

    def cache_stats(self, include_db: bool = True) -> Dict[str, int]:
        return self._cache.stats(include_db=include_db)

    def _is_initial_state(self) -> bool:
        return (
            self.possible_mask == ALL_CONFIG_MASK
            and self.revealed_mask == 0
            and self.found_purples == 0
            and self.clicks_left == self.max_clicks
        )

    def pick_next_click(
        self,
        progress_callback: Optional[BuildProgressCallback] = None,
    ) -> Optional[int]:
        if self.found_purples >= TARGET_PURPLES:
            return None
        if self.clicks_left <= 0:
            return None
        initial_state = self._is_initial_state()
        if initial_state:
            initial = _load_first_suggestion(self.max_clicks, self.cache_version, self.policy_signature)
            if initial is not None and not self.is_revealed(initial):
                return initial
        if self.possible_mask == 0:
            return self._fallback_next()

        total = self.possible_mask.bit_count()
        if total == 0:
            return self._fallback_next()

        if self.policy_mode == OQ_POLICY_MODE_EXACT_REF:
            candidate_positions = [pos for pos in POSITIONS if not self.is_revealed(pos)]
        else:
            candidate_positions = _beam_candidate_positions(
                self.possible_mask,
                self.revealed_mask,
                beam_k=self.beam_k,
            )
        if not candidate_positions:
            return self._fallback_next()

        progress_enabled = initial_state and progress_callback is not None
        progress_start = time.perf_counter()
        progress_last_emit = 0.0
        progress_positions: List[int] = list(candidate_positions)
        progress_total_positions = 0
        progress_total_branches = 0
        progress_completed_positions = 0
        progress_completed_branches = 0
        progress_completed_states = 0
        progress_states_since_emit = 0
        progress_active_pos: Optional[int] = None
        progress_active_position_index = 0
        progress_active_branch_index = 0

        if progress_enabled:
            for pos in progress_positions:
                progress_total_positions += 1
                for obs_mask in OBS_MASKS[pos]:
                    if self.possible_mask & obs_mask:
                        progress_total_branches += 1

        if progress_positions:
            progress_active_pos = progress_positions[0]
            progress_active_position_index = 1

        def emit_progress(force: bool = False, current_pos: Optional[int] = None) -> None:
            nonlocal progress_last_emit
            if not progress_enabled:
                return
            now = time.perf_counter()
            if not force and (now - progress_last_emit) < 0.2:
                return
            payload = {
                "phase": "initial_cache_build",
                "current_pos": current_pos,
                "completed_branches": progress_completed_branches,
                "total_branches": progress_total_branches,
                "active_branch_index": progress_active_branch_index,
                "completed_positions": progress_completed_positions,
                "total_positions": progress_total_positions,
                "active_position_index": progress_active_position_index,
                "completed_states": progress_completed_states,
                "elapsed_sec": now - progress_start,
                "cache_stats": self.cache_stats(include_db=False),
                "finished": (
                    progress_completed_branches >= progress_total_branches
                    and progress_completed_positions >= progress_total_positions
                ),
            }
            try:
                progress_callback(payload)
            except Exception:
                pass
            progress_last_emit = now

        def on_state_resolved() -> None:
            nonlocal progress_completed_states, progress_states_since_emit
            progress_completed_states += 1
            progress_states_since_emit += 1
            if progress_states_since_emit >= 512:
                progress_states_since_emit = 0
                emit_progress(current_pos=progress_active_pos)

        emit_progress(force=True)

        best_pos = None
        best_val = -1.0
        if progress_enabled:
            # For progress visibility, solve lighter root positions first.
            # Tie-break by position index to preserve deterministic policy ties.
            root_pos_meta: List[Tuple[int, int]] = []
            for pos in progress_positions:
                est_cost = 0
                for obs_mask in OBS_MASKS[pos]:
                    subset = self.possible_mask & obs_mask
                    if subset:
                        est_cost += subset.bit_count()
                root_pos_meta.append((est_cost, pos))
            root_pos_meta.sort(key=lambda x: (x[0], x[1]))
            position_order = [pos for _, pos in root_pos_meta]
        else:
            position_order = progress_positions

        for active_index, pos in enumerate(position_order, start=1):
            progress_active_pos = pos
            progress_active_position_index = active_index
            emit_progress(current_pos=pos)
            expected = 0.0
            if progress_enabled:
                obs_items: List[Tuple[int, int, int]] = []
                for obs_code, obs_mask in enumerate(OBS_MASKS[pos]):
                    subset = self.possible_mask & obs_mask
                    if subset:
                        obs_items.append((subset.bit_count(), obs_code, obs_mask))
                # Solve smaller subsets first so completed-branch progress updates earlier.
                obs_items.sort(key=lambda x: (x[0], x[1]))
                obs_iter = [(obs_code, obs_mask) for _, obs_code, obs_mask in obs_items]
            else:
                obs_iter = list(enumerate(OBS_MASKS[pos]))

            for obs_code, obs_mask in obs_iter:
                subset = self.possible_mask & obs_mask
                if subset == 0:
                    continue
                prob = subset.bit_count() / total
                progress_active_branch_index = progress_completed_branches + 1
                next_revealed = self.revealed_mask | (1 << pos)
                next_found = self.found_purples + 1 if obs_code == OBS_PURPLE else self.found_purples
                next_clicks = self.clicks_left if obs_code == OBS_PURPLE else self.clicks_left - 1
                if self.policy_mode == OQ_POLICY_MODE_EXACT_REF:
                    val = _best_success_prob_exact(
                        self._cache,
                        subset,
                        next_revealed,
                        next_found,
                        next_clicks,
                        state_progress_callback=(on_state_resolved if progress_enabled else None),
                    )
                else:
                    val = _best_success_prob_beam(
                        self._cache,
                        subset,
                        next_revealed,
                        next_found,
                        next_clicks,
                        beam_k=self.beam_k,
                        state_progress_callback=(on_state_resolved if progress_enabled else None),
                    )
                expected += prob * val
                progress_completed_branches += 1
                emit_progress(current_pos=pos)
            progress_completed_positions += 1
            emit_progress(current_pos=pos)
            if expected > best_val:
                best_val = expected
                best_pos = pos
            elif best_pos is not None and abs(expected - best_val) <= 1e-15 and pos < best_pos:
                # Keep deterministic tie-break independent of evaluation order.
                best_pos = pos

        emit_progress(force=True, current_pos=best_pos)

        if best_pos is not None and initial_state:
            _save_first_suggestion(self.max_clicks, self.cache_version, self.policy_signature, best_pos)
        return best_pos if best_pos is not None else self._fallback_next()

    def purple_probability(self, pos_idx: int) -> float:
        if self.is_revealed(pos_idx):
            return 0.0
        total = self.possible_mask.bit_count()
        if total == 0:
            return 0.0
        mask = OBS_MASKS[pos_idx][OBS_PURPLE]
        return (self.possible_mask & mask).bit_count() / total

    def top_purple_candidates(self, k: int = 5) -> List[Tuple[int, float]]:
        total = self.possible_mask.bit_count()
        if total == 0:
            return []
        items: List[Tuple[int, float]] = []
        for pos in POSITIONS:
            if self.is_revealed(pos):
                continue
            mask = OBS_MASKS[pos][OBS_PURPLE]
            prob = (self.possible_mask & mask).bit_count() / total
            items.append((pos, prob))
        items.sort(key=lambda x: (-x[1], x[0]))
        return items[:k]

    def post_red_click_order(self) -> List[int]:
        if len(self.known_purples) < TOTAL_PURPLES:
            return []
        items: List[Tuple[int, int, int, int]] = []
        for pos in POSITIONS:
            if self.is_revealed(pos):
                continue
            count = 0
            for n in NEIGHBORS[pos]:
                if n in self.known_purples:
                    count += 1
            r, c = idx_to_rc(pos)
            items.append((-count, r, c, pos))
        items.sort()
        return [pos for _, _, _, pos in items]

    def _fallback_next(self) -> Optional[int]:
        for pos in POSITIONS:
            if not self.is_revealed(pos):
                return pos
        return None


def _simulate_once(
    rng,
    max_clicks: int,
    cache_ram_mb: int,
    cache_version: str,
    *,
    beam_k: int,
    cache_max_gb: float,
) -> bool:
    config = rng.sample(POSITIONS, TOTAL_PURPLES)
    config_set = set(config)
    solver = OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        policy_mode=OQ_POLICY_MODE_BEAM,
        beam_k=beam_k,
        cache_max_gb=cache_max_gb,
    )

    while solver.clicks_left > 0 and solver.found_purples < TARGET_PURPLES:
        pos = solver.pick_next_click()
        if pos is None:
            break
        if pos in config_set:
            obs = OBS_PURPLE
        else:
            count = 0
            for n in NEIGHBORS[pos]:
                if n in config_set:
                    count += 1
            obs = count
        solver.apply_observation(pos, obs)

    return solver.found_purples >= TARGET_PURPLES


def run_simulation(
    num_games: int = 1000,
    seed: Optional[int] = None,
    max_clicks: int = MAX_CLICKS,
    cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    cache_max_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
) -> None:
    import random
    rng = random.Random(seed)
    successes = 0
    for _ in range(num_games):
        if _simulate_once(
            rng,
            max_clicks=max_clicks,
            cache_ram_mb=cache_ram_mb,
            cache_version=cache_version,
            beam_k=beam_k,
            cache_max_gb=cache_max_gb,
        ):
            successes += 1
    rate = (successes / num_games * 100.0) if num_games > 0 else 0.0
    print(f"Simulated {num_games} games. Success rate: {successes}/{num_games} ({rate:.2f}%)")


def build_cache_for_initial_state(
    *,
    max_clicks: int = MAX_CLICKS,
    cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    cache_max_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
    rebuild: bool = False,
    progress_callback: Optional[BuildProgressCallback] = None,
) -> Dict[str, Any]:
    policy_signature = _policy_signature(OQ_POLICY_MODE_BEAM, beam_k)
    cache = _get_state_cache(
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        policy_signature=policy_signature,
        max_cache_gb=cache_max_gb,
        enable_legacy_migration=False,
        force_rebuild=rebuild,
    )
    solver = OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        policy_mode=OQ_POLICY_MODE_BEAM,
        beam_k=beam_k,
        cache_max_gb=cache_max_gb,
    )
    start = time.perf_counter()
    best_pos = solver.pick_next_click(progress_callback=progress_callback)
    success_prob = solver.current_success_prob()
    elapsed = time.perf_counter() - start
    cache.flush()
    stats = cache.stats(include_db=True)
    return {
        "best_pos": best_pos,
        "success_prob": success_prob,
        "elapsed_sec": elapsed,
        "beam_k": beam_k,
        "policy_signature": policy_signature,
        "cache_stats": stats,
        "cache_path": os.fspath(_state_cache_path(cache_version)),
    }


def get_cache_stats(
    *,
    cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    cache_max_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
) -> Dict[str, Any]:
    policy_signature = _policy_signature(OQ_POLICY_MODE_BEAM, beam_k)
    cache = _get_state_cache(
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        policy_signature=policy_signature,
        max_cache_gb=cache_max_gb,
        enable_legacy_migration=False,
    )
    stats = cache.stats(include_db=True)
    stats["cache_path"] = os.fspath(_state_cache_path(cache_version))
    stats["policy_signature"] = policy_signature
    return stats


def _state_is_terminal(state: StateKey) -> bool:
    possible_mask, _, found_purples, clicks_left = state
    if found_purples >= TARGET_PURPLES:
        return True
    if clicks_left <= 0:
        return True
    if possible_mask == 0:
        return True
    if possible_mask.bit_count() == 0:
        return True
    return False


def _iter_action_children(state: StateKey, pos: int) -> List[Tuple[int, StateKey]]:
    possible_mask, revealed_mask, found_purples, clicks_left = state
    if revealed_mask & (1 << pos):
        return []

    children: List[Tuple[int, StateKey]] = []
    next_revealed_mask = revealed_mask | (1 << pos)
    for obs_code, obs_mask in enumerate(OBS_MASKS[pos]):
        subset = possible_mask & obs_mask
        if subset == 0:
            continue
        if obs_code == OBS_PURPLE:
            child = (subset, next_revealed_mask, found_purples + 1, clicks_left)
        else:
            child = (subset, next_revealed_mask, found_purples, clicks_left - 1)
        children.append((subset.bit_count(), child))
    return children


def _read_state_value_from_db(
    conn: sqlite3.Connection,
    key: StateKey,
    value_cache: OrderedDict[StateKey, float],
    *,
    max_cached_values: int = 200000,
) -> float:
    cached = value_cache.get(key)
    if cached is not None:
        value_cache.move_to_end(key)
        return cached

    possible_mask, revealed_mask, found_purples, clicks_left = key
    row = conn.execute(
        """
        SELECT value FROM states
        WHERE possible_mask = ?
          AND revealed_mask = ?
          AND found_purples = ?
          AND clicks_left = ?
        """,
        (_int_to_blob(possible_mask), revealed_mask, found_purples, clicks_left),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "Trim aborted: cache is missing required state "
            f"(possible_mask_bits={possible_mask.bit_length()}, "
            f"revealed_mask={revealed_mask}, found_purples={found_purples}, clicks_left={clicks_left})."
        )

    value = float(row[0])
    value_cache[key] = value
    value_cache.move_to_end(key)
    while len(value_cache) > max_cached_values:
        value_cache.popitem(last=False)
    return value


def _assert_cache_policy_signature(conn: sqlite3.Connection, expected_policy_signature: str) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'policy_signature'").fetchone()
    existing = str(row[0]) if row is not None else ""
    if existing != expected_policy_signature:
        raise RuntimeError(
            "Cache policy signature mismatch. "
            f"expected='{expected_policy_signature}', actual='{existing or '<missing>'}'."
        )


def _pick_policy_action_from_db(
    conn: sqlite3.Connection,
    state: StateKey,
    value_cache: OrderedDict[StateKey, float],
    *,
    beam_k: int,
) -> Optional[int]:
    if _state_is_terminal(state):
        return None

    possible_mask, revealed_mask, _, _ = state
    total = possible_mask.bit_count()
    if total == 0:
        return None

    candidate_positions = _beam_candidate_positions(
        possible_mask,
        revealed_mask,
        beam_k=beam_k,
    )
    best_pos: Optional[int] = None
    best_val = -1.0
    for pos in candidate_positions:
        children = _iter_action_children(state, pos)
        if not children:
            continue

        expected = 0.0
        for subset_count, child in children:
            child_val = _read_state_value_from_db(conn, child, value_cache)
            expected += (subset_count / total) * child_val

        if expected > best_val:
            best_val = expected
            best_pos = pos
    return best_pos


def _trim_cache_first_bit(
    conn: sqlite3.Connection,
    *,
    max_clicks: int,
    first_pos: int,
) -> int:
    first_bit = 1 << first_pos
    root_blob = _int_to_blob(ALL_CONFIG_MASK)

    changes_before = int(conn.total_changes)
    conn.execute(
        """
        DELETE FROM states
        WHERE (revealed_mask & ?) = 0
          AND NOT (
            possible_mask = ?
            AND revealed_mask = 0
            AND found_purples = 0
            AND clicks_left = ?
          )
        """,
        (first_bit, root_blob, max_clicks),
    )
    conn.commit()
    return int(conn.total_changes) - changes_before


def _trim_cache_policy_eval(
    conn: sqlite3.Connection,
    *,
    max_clicks: int,
    first_pos: int,
    beam_k: int,
) -> Dict[str, int]:
    conn.execute(
        """
        CREATE TEMP TABLE keep_states (
            possible_mask BLOB NOT NULL,
            revealed_mask INTEGER NOT NULL,
            found_purples INTEGER NOT NULL,
            clicks_left INTEGER NOT NULL,
            PRIMARY KEY (possible_mask, revealed_mask, found_purples, clicks_left)
        )
        """
    )
    conn.execute(
        """
        CREATE TEMP TABLE seen_policy_states (
            possible_mask BLOB NOT NULL,
            revealed_mask INTEGER NOT NULL,
            found_purples INTEGER NOT NULL,
            clicks_left INTEGER NOT NULL,
            PRIMARY KEY (possible_mask, revealed_mask, found_purples, clicks_left)
        )
        """
    )

    keep_rows_batch: List[Tuple[bytes, int, int, int]] = []

    def flush_keep_rows() -> None:
        if not keep_rows_batch:
            return
        conn.executemany(
            """
            INSERT OR IGNORE INTO keep_states
            (possible_mask, revealed_mask, found_purples, clicks_left)
            VALUES (?, ?, ?, ?)
            """,
            keep_rows_batch,
        )
        keep_rows_batch.clear()

    def add_keep_state(state: StateKey) -> None:
        possible_mask, revealed_mask, found_purples, clicks_left = state
        keep_rows_batch.append(
            (
                _int_to_blob(possible_mask),
                revealed_mask,
                found_purples,
                clicks_left,
            )
        )
        if len(keep_rows_batch) >= OQ_SQLITE_WRITE_BATCH_SIZE:
            flush_keep_rows()

    policy_queue: deque[StateKey] = deque()

    def enqueue_policy_state(state: StateKey) -> None:
        possible_mask, revealed_mask, found_purples, clicks_left = state
        row = (
            _int_to_blob(possible_mask),
            revealed_mask,
            found_purples,
            clicks_left,
        )
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO seen_policy_states
            (possible_mask, revealed_mask, found_purples, clicks_left)
            VALUES (?, ?, ?, ?)
            """,
            row,
        )
        if cur.rowcount > 0:
            policy_queue.append(state)

    root_state: StateKey = (ALL_CONFIG_MASK, 0, 0, max_clicks)
    enqueue_policy_state(root_state)
    add_keep_state(root_state)

    value_cache: OrderedDict[StateKey, float] = OrderedDict()
    policy_states_processed = 0
    policy_edges_followed = 0
    kept_eval_children = 0

    while policy_queue:
        state = policy_queue.popleft()
        policy_states_processed += 1
        add_keep_state(state)

        if _state_is_terminal(state):
            continue

        if state == root_state:
            chosen_pos = first_pos
        else:
            chosen_pos = _pick_policy_action_from_db(conn, state, value_cache, beam_k=beam_k)
            if chosen_pos is None:
                continue

        possible_mask, revealed_mask, _, _ = state
        if possible_mask == 0:
            continue

        candidate_positions = _beam_candidate_positions(possible_mask, revealed_mask, beam_k=beam_k)
        if state == root_state and first_pos not in candidate_positions:
            candidate_positions = [first_pos] + candidate_positions
        for pos in candidate_positions:
            for _, child in _iter_action_children(state, pos):
                add_keep_state(child)
                kept_eval_children += 1
                if pos == chosen_pos and not _state_is_terminal(child):
                    enqueue_policy_state(child)
                    policy_edges_followed += 1

    flush_keep_rows()

    keep_rows = int(conn.execute("SELECT COUNT(*) FROM keep_states").fetchone()[0])
    changes_before_delete = int(conn.total_changes)
    conn.execute(
        """
        DELETE FROM states
        WHERE NOT EXISTS (
            SELECT 1
            FROM keep_states
            WHERE keep_states.possible_mask = states.possible_mask
              AND keep_states.revealed_mask = states.revealed_mask
              AND keep_states.found_purples = states.found_purples
              AND keep_states.clicks_left = states.clicks_left
        )
        """
    )
    conn.commit()
    trimmed_rows = int(conn.total_changes) - changes_before_delete

    return {
        "trimmed_rows": trimmed_rows,
        "keep_rows": keep_rows,
        "policy_states_processed": policy_states_processed,
        "policy_edges_followed": policy_edges_followed,
        "kept_eval_children": kept_eval_children,
    }


def trim_cache_to_first_branch(
    *,
    max_clicks: int = MAX_CLICKS,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    vacuum: bool = True,
    mode: str = "policy_eval",
) -> Dict[str, Any]:
    """
    Trim cache after first move is known.

    mode="policy_eval":
      Keep only states needed when following the cached policy branch,
      including one-ply evaluation children to keep next suggestions fast.

    mode="first_bit":
      Keep root + every state containing the first-click bit.
    """
    ensure_runtime_dirs()
    reset_global_state_cache()

    cache_path = _state_cache_path(cache_version)
    if not cache_path.exists():
        raise FileNotFoundError(f"Cache DB not found: {cache_path}")

    policy_signature = _policy_signature(OQ_POLICY_MODE_BEAM, beam_k)
    first_pos = _load_first_suggestion(max_clicks, cache_version, policy_signature)

    conn = sqlite3.connect(os.fspath(cache_path), timeout=120.0)
    try:
        _assert_cache_policy_signature(conn, policy_signature)
        if first_pos is None:
            root_state: StateKey = (ALL_CONFIG_MASK, 0, 0, max_clicks)
            first_pos = _pick_policy_action_from_db(conn, root_state, OrderedDict(), beam_k=beam_k)
            if first_pos is None:
                raise RuntimeError(
                    f"First suggestion cache missing for version={cache_version}, clicks={max_clicks} "
                    "and could not infer from current DB."
                )
            _save_first_suggestion(max_clicks, cache_version, policy_signature, first_pos)

        before_rows = int(conn.execute("SELECT COUNT(*) FROM states").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        before_pages = int(conn.execute("PRAGMA page_count").fetchone()[0])
        before_bytes = page_size * before_pages

        trim_details: Dict[str, Any]
        if mode == "first_bit":
            trimmed_rows = _trim_cache_first_bit(conn, max_clicks=max_clicks, first_pos=first_pos)
            trim_details = {"trimmed_rows": trimmed_rows}
        elif mode == "policy_eval":
            trim_details = _trim_cache_policy_eval(
                conn,
                max_clicks=max_clicks,
                first_pos=first_pos,
                beam_k=beam_k,
            )
            trimmed_rows = int(trim_details.get("trimmed_rows", 0))
        else:
            raise ValueError(f"Unknown trim mode: {mode}. Use 'policy_eval' or 'first_bit'.")

        after_rows = int(conn.execute("SELECT COUNT(*) FROM states").fetchone()[0])

        if vacuum:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.commit()

        after_pages = int(conn.execute("PRAGMA page_count").fetchone()[0])
        after_bytes = page_size * after_pages

        return {
            "cache_path": os.fspath(cache_path),
            "first_pos": first_pos,
            "beam_k": beam_k,
            "policy_signature": policy_signature,
            "mode": mode,
            "before_rows": before_rows,
            "after_rows": after_rows,
            "trimmed_rows": trimmed_rows,
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "vacuum": bool(vacuum),
            **trim_details,
        }
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ouro Quest ($oq) beam solver tools")
    default_cache_ram_mb = _env_int("OQ_CACHE_RAM_MB", OQ_DEFAULT_RAM_CACHE_MB)
    default_beam_k = _env_int("OQ_BEAM_K", OQ_BEAM_K_DEFAULT)
    default_cache_max_gb = _env_float("OQ_CACHE_MAX_GB", OQ_DEFAULT_CACHE_MAX_GB)
    parser.add_argument("--simulate", type=int, default=0, help="Run N simulated games")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for simulation")
    parser.add_argument("--clicks", type=int, default=MAX_CLICKS, help="Max non-purple clicks")
    parser.add_argument(
        "--cache-ram-mb",
        type=int,
        default=default_cache_ram_mb,
        help="RAM budget for in-process cache (or env OQ_CACHE_RAM_MB)",
    )
    parser.add_argument(
        "--beam-k",
        type=int,
        default=default_beam_k,
        help="Beam width for runtime policy (or env OQ_BEAM_K)",
    )
    parser.add_argument(
        "--cache-max-gb",
        type=float,
        default=default_cache_max_gb,
        help="Persistent cache cap in GB (or env OQ_CACHE_MAX_GB)",
    )
    parser.add_argument(
        "--cache-version",
        type=str,
        default=OQ_CACHE_VERSION_DEFAULT,
        help="Cache version key",
    )
    parser.add_argument(
        "--build-cache",
        action="store_true",
        help="Build runtime beam cache for the initial state and exit",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Rebuild runtime beam cache for the initial state from scratch and exit",
    )
    parser.add_argument(
        "--cache-stats",
        action="store_true",
        help="Print cache stats and exit",
    )
    parser.add_argument(
        "--trim-to-first-branch",
        action="store_true",
        help="Trim DB to first-branch states after first suggestion is known",
    )
    parser.add_argument(
        "--trim-mode",
        type=str,
        choices=("policy_eval", "first_bit"),
        default="policy_eval",
        help="Trim strategy: policy_eval (smaller, keeps next-turn eval states) or first_bit (fast coarse trim)",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        help="Skip VACUUM after trim (faster, but file may not shrink immediately)",
    )
    args = parser.parse_args(argv)

    if args.build_cache or args.rebuild_cache:
        info = build_cache_for_initial_state(
            max_clicks=args.clicks,
            cache_ram_mb=args.cache_ram_mb,
            cache_version=args.cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.cache_max_gb,
            rebuild=args.rebuild_cache,
        )
        print(
            f"Built beam cache in {info['elapsed_sec']:.2f}s, "
            f"best_pos={info['best_pos']}, success_prob={info['success_prob']:.6f}"
        )
        print(f"Cache path: {info['cache_path']}")
        print(f"Cache stats: {info['cache_stats']}")
        return

    if args.cache_stats:
        print(
            get_cache_stats(
                cache_ram_mb=args.cache_ram_mb,
                cache_version=args.cache_version,
                beam_k=args.beam_k,
                cache_max_gb=args.cache_max_gb,
            )
        )
        return

    if args.trim_to_first_branch:
        info = trim_cache_to_first_branch(
            max_clicks=args.clicks,
            cache_version=args.cache_version,
            beam_k=args.beam_k,
            vacuum=(not args.no_vacuum),
            mode=args.trim_mode,
        )
        print(info)
        return

    if args.simulate > 0:
        run_simulation(
            args.simulate,
            seed=args.seed,
            max_clicks=args.clicks,
            cache_ram_mb=args.cache_ram_mb,
            cache_version=args.cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.cache_max_gb,
        )
    else:
        print("Oq_solver loaded. Use --simulate N, --build-cache, or --cache-stats.")


if __name__ == "__main__":
    main()

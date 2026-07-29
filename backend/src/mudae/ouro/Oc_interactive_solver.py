"""
oc_interactive_suggester.py
Interactive suggestion program for Ouro Chest ($oc) sphere-finding game.

Flow:
 - Build exact likelihoods P(obs_color | red_pos, click_pos) once (cached to disk).
 - Keep a Bayesian prior over RED positions (never center).
 - In each step (max 5 clicks, and 2 minutes total), suggest a best next click
   using a one-step EV lookahead like the original solver.
 - The program then waits for the user to enter which cell they actually clicked
   and the observed color from the *real* game. It updates beliefs and suggests the
   next cell. The board printed shows coordinates and highlights the suggestion.

Usage: python oc_interactive_suggester.py

Notes:
 - Color short codes: R (Red), O (Orange), Y (Yellow), G (Green), T (Teal), B (Blue)
 - RED never spawns at center (row 3 col 3 -> index (2,2)).
 - If you accept the suggestion and clicked it on the real game, you can just
   type the color (e.g. "B" or "blue"). If you clicked a different cell,
   type the coordinates first (e.g. "1 5") and then the color.

"""

import time
import os
import pickle
import argparse
from typing import Dict, Tuple, List, Optional
from enum import Enum
from itertools import combinations
from multiprocessing import Pool, cpu_count
import atexit
from mudae.paths import OURO_CACHE_DIR, ensure_runtime_dirs
from mudae.storage.atomic import atomic_write_pickle, atomic_write_text
from mudae.storage.coordination import acquire_lease, build_path_scope

MAX_CLICKS = 5
TIME_LIMIT_SECONDS = 9999
SEARCH_DEPTH = 5  # full-depth search for max accuracy
PARALLEL_EVAL = True  # evaluate root actions in parallel for better CPU use
PARALLEL_WORKERS = 0  # 0 = cpu_count()
PARALLEL_MIN_ACTIONS = 6  # avoid overhead when few choices remain


class SphereColor(Enum):
    RED = 'R'
    ORANGE = 'O'
    YELLOW = 'Y'
    GREEN = 'G'
    TEAL = 'T'
    BLUE = 'B'

SPHERE_SCORES = {
    SphereColor.RED: 150,
    SphereColor.ORANGE: 90,
    SphereColor.YELLOW: 35,
    SphereColor.GREEN: 35,
    SphereColor.TEAL: 35,
    SphereColor.BLUE: 10,
}


ALL_POSITIONS = [(r, c) for r in range(5) for c in range(5)]
VALID_POSITIONS = [(r, c) for r in range(5) for c in range(5) if not (r == 2 and c == 2)]

LIKELIHOOD_CACHE_VERSION = "exact_v1"
OBJECTIVE_VERSION = "lex_red_then_score_v1"
LIKELIHOOD_CACHE_FILENAME = f"oc_likelihoods_{LIKELIHOOD_CACHE_VERSION}.pkl"
LOAD_LOG_FILENAME = "oc_load.log"
FIRST_SUGGESTION_CACHE_FILENAME = f"oc_first_suggestion_{LIKELIHOOD_CACHE_VERSION}_{OBJECTIVE_VERSION}.pkl"
POLICY_CACHE_FILENAME = f"oc_policy_cache_{LIKELIHOOD_CACHE_VERSION}_{OBJECTIVE_VERSION}.pkl"

ensure_runtime_dirs()


def _likelihood_cache_path() -> str:
    return os.fspath(OURO_CACHE_DIR / LIKELIHOOD_CACHE_FILENAME)


def _load_log_path() -> str:
    return os.fspath(OURO_CACHE_DIR / LOAD_LOG_FILENAME)


def _first_suggestion_cache_path() -> str:
    return os.fspath(OURO_CACHE_DIR / FIRST_SUGGESTION_CACHE_FILENAME)


def _policy_cache_path() -> str:
    return os.fspath(OURO_CACHE_DIR / POLICY_CACHE_FILENAME)


def _load_pickle_payload(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def _atomic_append_line(path: str, line: str) -> None:
    with acquire_lease(
        build_path_scope("text-file", path),
        f"oc-load-log@pid{os.getpid()}",
        ttl_sec=10.0,
        heartbeat_sec=5.0,
        wait_timeout_sec=5.0,
    ) as lease:
        if not lease.acquired:
            raise TimeoutError(f"Timed out acquiring OC load log lease for {path}")
        existing = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read()
            except Exception:
                existing = ""
        atomic_write_text(path, existing + line)


def _log_load_timing(label: str, elapsed_s: float):
    try:
        _atomic_append_line(
            _load_log_path(),
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {label}: {elapsed_s:.4f}s\n",
        )
    except Exception:
        pass


def _log_load_info(label: str, message: str):
    try:
        _atomic_append_line(
            _load_log_path(),
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {label}: {message}\n",
        )
    except Exception:
        pass


def _build_likelihoods_exact():
    """Build exact P(obs_color | red_pos, click_pos) by enumeration."""
    start = time.perf_counter()
    likelihood = {click: {red: {col: 0 for col in SphereColor} for red in VALID_POSITIONS} for click in ALL_POSITIONS}

    def get_adjacent(r0, c0):
        adj = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r0 + dr, c0 + dc
            if 0 <= nr < 5 and 0 <= nc < 5:
                adj.append((nr, nc))
        return adj

    def get_diagonal(r0, c0):
        diag = []
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = r0 + dr, c0 + dc
            while 0 <= nr < 5 and 0 <= nc < 5:
                diag.append((nr, nc))
                nr += dr
                nc += dc
        return diag

    def get_row_col(r0, c0):
        positions = []
        for i in range(5):
            if i != c0:
                positions.append((r0, i))
            if i != r0:
                positions.append((i, c0))
        return positions

    boards_per_red = {red: 0 for red in VALID_POSITIONS}

    for red in VALID_POSITIONS:
        r, c = red
        orange_positions = get_adjacent(r, c)
        yellow_positions = get_diagonal(r, c)
        row_col_positions = get_row_col(r, c)
        row_col_set = set(row_col_positions)
        diag_set = set(yellow_positions)

        for oranges in combinations(orange_positions, 2):
            orange_set = set(oranges)
            for yellows in combinations(yellow_positions, 3):
                yellow_set = set(yellows)
                green_candidates = [pos for pos in row_col_positions if pos not in orange_set]
                for greens in combinations(green_candidates, 4):
                    green_set = set(greens)
                    teal_set = (row_col_set | diag_set) - orange_set - yellow_set - green_set - {red}

                    boards_per_red[red] += 1
                    for click_pos in ALL_POSITIONS:
                        if click_pos == red:
                            color = SphereColor.RED
                        elif click_pos in orange_set:
                            color = SphereColor.ORANGE
                        elif click_pos in yellow_set:
                            color = SphereColor.YELLOW
                        elif click_pos in green_set:
                            color = SphereColor.GREEN
                        elif click_pos in teal_set:
                            color = SphereColor.TEAL
                        else:
                            color = SphereColor.BLUE
                        likelihood[click_pos][red][color] += 1

    for click in ALL_POSITIONS:
        for red in VALID_POSITIONS:
            total = boards_per_red[red]
            if total == 0:
                continue
            counts = likelihood[click][red]
            for col in counts:
                counts[col] = counts[col] / total

    elapsed = time.perf_counter() - start
    print(f"[INFO] Built exact likelihoods in {elapsed:.1f}s")
    _log_load_timing("build_likelihoods_exact", elapsed)
    return likelihood


def _load_or_build_likelihoods():
    load_start = time.perf_counter()
    cache_path = _likelihood_cache_path()
    if os.path.exists(cache_path):
        try:
            t0 = time.perf_counter()
            payload = _load_pickle_payload(cache_path)
            _log_load_timing("cache_read", time.perf_counter() - t0)
            if isinstance(payload, dict) and payload.get("version") == LIKELIHOOD_CACHE_VERSION:
                print(f"[INFO] Loaded cached likelihoods from {os.path.basename(cache_path)}")
                _log_load_timing("load_or_build_total", time.perf_counter() - load_start)
                return payload["likelihoods"]
        except Exception:
            pass

    try:
        with acquire_lease(
            build_path_scope("pickle-file", cache_path),
            f"oc-likelihood@pid{os.getpid()}",
            ttl_sec=600.0,
            heartbeat_sec=30.0,
            wait_timeout_sec=300.0,
        ) as lease:
            if not lease.acquired:
                raise TimeoutError(f"Timed out acquiring OC likelihood cache lease for {cache_path}")
            if os.path.exists(cache_path):
                try:
                    t0 = time.perf_counter()
                    payload = _load_pickle_payload(cache_path)
                    _log_load_timing("cache_read", time.perf_counter() - t0)
                    if isinstance(payload, dict) and payload.get("version") == LIKELIHOOD_CACHE_VERSION:
                        print(f"[INFO] Loaded cached likelihoods from {os.path.basename(cache_path)}")
                        _log_load_timing("load_or_build_total", time.perf_counter() - load_start)
                        return payload["likelihoods"]
                except Exception:
                    pass
            likelihood = _build_likelihoods_exact()
            t0 = time.perf_counter()
            atomic_write_pickle(
                cache_path,
                {"version": LIKELIHOOD_CACHE_VERSION, "likelihoods": likelihood},
            )
            _log_load_timing("cache_write", time.perf_counter() - t0)
            _log_load_timing("load_or_build_total", time.perf_counter() - load_start)
            return likelihood
    except Exception:
        pass
    likelihood = _build_likelihoods_exact()
    _log_load_timing("load_or_build_total", time.perf_counter() - load_start)
    return likelihood


_LIKELIHOODS = None


def _build_cache_file(force_rebuild: bool = False) -> str:
    """Build likelihoods and write cache file. Returns cache path."""
    cache_path = _likelihood_cache_path()
    try:
        with acquire_lease(
            build_path_scope("pickle-file", cache_path),
            f"oc-build-cache@pid{os.getpid()}",
            ttl_sec=600.0,
            heartbeat_sec=30.0,
            wait_timeout_sec=300.0,
        ) as lease:
            if not lease.acquired:
                raise TimeoutError(f"Timed out acquiring OC build cache lease for {cache_path}")
            if os.path.exists(cache_path) and not force_rebuild:
                print(f"[INFO] Cache already exists: {os.path.basename(cache_path)}")
                _log_load_timing("build_cache_skipped_exists", 0.0)
                return cache_path

            t0 = time.perf_counter()
            likelihood = _build_likelihoods_exact()
            try:
                t1 = time.perf_counter()
                atomic_write_pickle(
                    cache_path,
                    {"version": LIKELIHOOD_CACHE_VERSION, "likelihoods": likelihood},
                )
                print(f"[INFO] Wrote cache to {os.path.basename(cache_path)}")
                _log_load_timing("cache_write", time.perf_counter() - t1)
            except Exception:
                print("[WARN] Failed to write cache file.")
            _log_load_timing("build_cache_total", time.perf_counter() - t0)
            return cache_path
    except Exception:
        pass
    return cache_path


def _get_likelihoods():
    global _LIKELIHOODS
    if _LIKELIHOODS is None:
        t0 = time.perf_counter()
        _LIKELIHOODS = _load_or_build_likelihoods()
        _log_load_timing("get_likelihoods_total", time.perf_counter() - t0)
    return _LIKELIHOODS


def _first_suggestion_cache_key() -> Tuple:
    return (
        LIKELIHOOD_CACHE_VERSION,
        OBJECTIVE_VERSION,
        SEARCH_DEPTH,
        MAX_CLICKS,
    )


def _policy_cache_key() -> Tuple:
    return (
        LIKELIHOOD_CACHE_VERSION,
        OBJECTIVE_VERSION,
        SEARCH_DEPTH,
        MAX_CLICKS,
    )


def _load_first_suggestion() -> Optional[Tuple[int, int]]:
    cache_path = _first_suggestion_cache_path()
    if not os.path.exists(cache_path):
        _log_load_timing("first_suggestion_cache_miss", 0.0)
        return None
    try:
        payload = _load_pickle_payload(cache_path)
        if isinstance(payload, dict) and payload.get("key") == _first_suggestion_cache_key():
            _log_load_timing("first_suggestion_cache_hit", 0.0)
            return payload.get("pos")
    except Exception:
        _log_load_timing("first_suggestion_cache_error", 0.0)
        return None
    _log_load_timing("first_suggestion_cache_miss", 0.0)
    return None


def _save_first_suggestion(pos: Tuple[int, int]):
    cache_path = _first_suggestion_cache_path()
    try:
        with acquire_lease(
            build_path_scope("pickle-file", cache_path),
            f"oc-first-suggestion@pid{os.getpid()}",
            ttl_sec=30.0,
            heartbeat_sec=10.0,
            wait_timeout_sec=5.0,
        ) as lease:
            if not lease.acquired:
                raise TimeoutError(f"Timed out acquiring OC first suggestion lease for {cache_path}")
            atomic_write_pickle(
                cache_path,
                {"key": _first_suggestion_cache_key(), "pos": pos},
            )
    except Exception:
        pass


def _load_policy_cache() -> Dict:
    cache_path = _policy_cache_path()
    if not os.path.exists(cache_path):
        _log_load_timing("policy_cache_miss", 0.0)
        return {}
    try:
        t0 = time.perf_counter()
        payload = _load_pickle_payload(cache_path)
        _log_load_timing("policy_cache_read", time.perf_counter() - t0)
        if isinstance(payload, dict) and payload.get("key") == _policy_cache_key():
            data = payload.get("cache") or {}
            _log_load_timing("policy_cache_hit", 0.0)
            return data
    except Exception:
        _log_load_timing("policy_cache_error", 0.0)
        return {}
    _log_load_timing("policy_cache_miss", 0.0)
    return {}


def _save_policy_cache(cache: Dict):
    if not cache:
        return
    cache_path = _policy_cache_path()
    try:
        with acquire_lease(
            build_path_scope("pickle-file", cache_path),
            f"oc-policy-cache@pid{os.getpid()}",
            ttl_sec=30.0,
            heartbeat_sec=10.0,
            wait_timeout_sec=5.0,
        ) as lease:
            if not lease.acquired:
                raise TimeoutError(f"Timed out acquiring OC policy cache lease for {cache_path}")
            merged_cache = dict(cache)
            if os.path.exists(cache_path):
                try:
                    payload = _load_pickle_payload(cache_path)
                    if isinstance(payload, dict) and payload.get("key") == _policy_cache_key():
                        existing = payload.get("cache")
                        if isinstance(existing, dict):
                            merged_cache = dict(existing)
                            merged_cache.update(cache)
                except Exception:
                    pass
            t0 = time.perf_counter()
            atomic_write_pickle(
                cache_path,
                {"key": _policy_cache_key(), "cache": merged_cache},
            )
            _log_load_timing("policy_cache_write", time.perf_counter() - t0)
    except Exception:
        pass


_WORKER_LIKELIHOODS = None
_PAIR_EPS = 1e-12


def _better_pair(p_a: float, s_a: float, p_b: float, s_b: float) -> bool:
    if p_a > p_b + _PAIR_EPS:
        return True
    if p_b > p_a + _PAIR_EPS:
        return False
    return s_a > s_b + _PAIR_EPS


def _worker_init(likelihoods):
    global _WORKER_LIKELIHOODS
    _WORKER_LIKELIHOODS = likelihoods


def _worker_belief_key(prior: Dict[Tuple[int, int], float]) -> Tuple[float, ...]:
    return tuple(round(prior.get(pos, 0.0), 6) for pos in VALID_POSITIONS)


def _worker_candidate_actions(revealed_mask: int):
    return [(idx, pos) for idx, pos in enumerate(ALL_POSITIONS) if not (revealed_mask & (1 << idx))]


def _worker_expected_score_at_pos(prior: Dict[Tuple[int, int], float], pos: Tuple[int, int]) -> float:
    score = 0.0
    for rp, p in prior.items():
        for col, prob in _WORKER_LIKELIHOODS[pos][rp].items():
            score += p * prob * SPHERE_SCORES[col]
    return score


def _worker_score_search_best_action(
    prior: Dict[Tuple[int, int], float],
    revealed_mask: int,
    clicks_left: int,
    cache: Dict,
) -> Tuple[Optional[Tuple[int, int]], float]:
    if clicks_left <= 0 or not prior:
        return None, 0.0

    key = (clicks_left, revealed_mask, _worker_belief_key(prior))
    cached = cache.get(key)
    if cached is not None:
        return cached

    actions = _worker_candidate_actions(revealed_mask)
    if not actions:
        return None, 0.0

    best_pos = None
    best_score = -1.0
    for pos_idx, pos in actions:
        score = _worker_score_expected_value_for_action(prior, pos, pos_idx, revealed_mask, clicks_left, cache)
        if score > best_score:
            best_score = score
            best_pos = pos

    result = (best_pos, best_score)
    cache[key] = result
    return result


def _worker_score_expected_value_for_action(
    prior: Dict[Tuple[int, int], float],
    pos: Tuple[int, int],
    pos_idx: int,
    revealed_mask: int,
    clicks_left: int,
    cache: Dict,
) -> float:
    immediate_score = _worker_expected_score_at_pos(prior, pos)
    if clicks_left <= 1:
        return immediate_score

    obs_probs = {col: 0.0 for col in SphereColor}
    for rp, p in prior.items():
        for col, prob in _WORKER_LIKELIHOODS[pos][rp].items():
            obs_probs[col] += p * prob

    expected_score = immediate_score
    next_mask = revealed_mask | (1 << pos_idx)
    for o, P_o in obs_probs.items():
        if P_o == 0:
            continue

        posterior_o = {}
        s = 0.0
        for rp, p in prior.items():
            like = _WORKER_LIKELIHOODS[pos][rp].get(o, 0.0)
            if like > 0:
                posterior_o[rp] = p * like
                s += posterior_o[rp]

        if s == 0:
            continue

        for rp in posterior_o:
            posterior_o[rp] /= s

        _, best_score = _worker_score_search_best_action(posterior_o, next_mask, clicks_left - 1, cache)
        expected_score += P_o * best_score

    return expected_score


def _worker_search_best_action(
    prior: Dict[Tuple[int, int], float],
    revealed_mask: int,
    clicks_left: int,
    cache: Dict,
    score_cache: Dict,
) -> Tuple[Optional[Tuple[int, int]], float, float]:
    if clicks_left <= 0 or not prior:
        return None, 0.0, 0.0

    key = (clicks_left, revealed_mask, _worker_belief_key(prior))
    cached = cache.get(key)
    if cached is not None:
        return cached

    actions = _worker_candidate_actions(revealed_mask)
    if not actions:
        return None, 0.0, 0.0

    best_pos = None
    best_p = -1.0
    best_score = -1.0
    for pos_idx, pos in actions:
        p_red, score = _worker_expected_metrics_for_action(
            prior, pos, pos_idx, revealed_mask, clicks_left, cache, score_cache
        )
        if _better_pair(p_red, score, best_p, best_score):
            best_p = p_red
            best_score = score
            best_pos = pos

    result = (best_pos, best_p, best_score)
    cache[key] = result
    return result


def _worker_expected_metrics_for_action(
    prior: Dict[Tuple[int, int], float],
    pos: Tuple[int, int],
    pos_idx: int,
    revealed_mask: int,
    clicks_left: int,
    cache: Dict,
    score_cache: Dict,
) -> Tuple[float, float]:
    immediate_p = prior.get(pos, 0.0)
    immediate_score = _worker_expected_score_at_pos(prior, pos)
    if clicks_left <= 1:
        return immediate_p, immediate_score

    obs_probs = {col: 0.0 for col in SphereColor}
    for rp, p in prior.items():
        for col, prob in _WORKER_LIKELIHOODS[pos][rp].items():
            obs_probs[col] += p * prob

    total_p = immediate_p
    total_score = immediate_score
    next_mask = revealed_mask | (1 << pos_idx)
    for o, P_o in obs_probs.items():
        if P_o == 0:
            continue
        if o == SphereColor.RED:
            if clicks_left > 1:
                _, best_score = _worker_score_search_best_action({pos: 1.0}, next_mask, clicks_left - 1, score_cache)
                total_score += P_o * best_score
            continue

        posterior_o = {}
        s = 0.0
        for rp, p in prior.items():
            like = _WORKER_LIKELIHOODS[pos][rp].get(o, 0.0)
            if like > 0:
                posterior_o[rp] = p * like
                s += posterior_o[rp]

        if s == 0:
            continue

        for rp in posterior_o:
            posterior_o[rp] /= s

        _, best_p, best_score = _worker_search_best_action(posterior_o, next_mask, clicks_left - 1, cache, score_cache)
        total_p += P_o * best_p
        total_score += P_o * best_score

    return total_p, total_score


def _worker_expected_value_task(args):
    prior, pos, pos_idx, revealed_mask, clicks_left = args
    cache = {}
    score_cache = {}
    return _worker_expected_metrics_for_action(
        prior, pos, pos_idx, revealed_mask, clicks_left, cache, score_cache
    )


class InteractiveSolver:
    def __init__(self):
        # Lazy-load likelihoods to avoid heavy import-time cost.
        self.likelihoods = _get_likelihoods()
        self.prior: Dict[Tuple[int, int], float] = {rp: 1.0 / len(VALID_POSITIONS) for rp in VALID_POSITIONS}
        self.clicks_used = 0
        self.max_clicks = MAX_CLICKS
        self.revealed = [[False for _ in range(5)] for _ in range(5)]
        self.observed = {}  # (r,c) -> SphereColor
        self.threshold = 0.35
        self.red_found = False
        self.red_pos: Optional[Tuple[int, int]] = None
        self.search_depth = min(SEARCH_DEPTH, self.max_clicks)
        self._policy_cache: Dict = _load_policy_cache()
        self._score_cache: Dict = {}
        self.last_metrics: Optional[Tuple[float, float]] = None
        self._pool: Optional[Pool] = None
        atexit.register(self.close_pool)
        atexit.register(self.save_policy_cache)

    def _belief_key(self, prior: Dict[Tuple[int, int], float]) -> Tuple[float, ...]:
        return tuple(round(prior.get(pos, 0.0), 6) for pos in VALID_POSITIONS)

    def _is_initial_state(self) -> bool:
        if self.clicks_used != 0 or self.red_found:
            return False
        if any(self.revealed[r][c] for r in range(5) for c in range(5)):
            return False
        if len(self.prior) != len(VALID_POSITIONS):
            return False
        expected = 1.0 / len(VALID_POSITIONS)
        for rp in VALID_POSITIONS:
            if abs(self.prior.get(rp, 0.0) - expected) > 1e-9:
                return False
        return True

    def _mask_from_revealed(self) -> int:
        mask = 0
        for idx, (r, c) in enumerate(ALL_POSITIONS):
            if self.revealed[r][c]:
                mask |= 1 << idx
        return mask

    def _action_metrics(self, pos: Tuple[int, int], revealed_mask: int, clicks_left: int) -> Tuple[float, float]:
        return self._expected_metrics_for_action(self.prior, pos, ALL_POSITIONS.index(pos), revealed_mask, clicks_left)

    def _set_last_metrics(self, p_red: float, exp_score: float):
        self.last_metrics = (p_red, exp_score)
        _log_load_info("suggestion_metrics", f"P_red={p_red:.6f} expected_score={exp_score:.3f}")

    def _candidate_actions(self, prior: Dict[Tuple[int, int], float], revealed_mask: int):
        actions = []
        for idx, pos in enumerate(ALL_POSITIONS):
            if not (revealed_mask & (1 << idx)):
                actions.append((idx, pos))
        return actions

    def _get_pool(self) -> Optional[Pool]:
        if not PARALLEL_EVAL:
            return None
        if self._pool is None:
            workers = PARALLEL_WORKERS if PARALLEL_WORKERS > 0 else cpu_count()
            self._pool = Pool(processes=workers, initializer=_worker_init, initargs=(self.likelihoods,))
        return self._pool

    def close_pool(self):
        if self._pool is not None:
            self._pool.close()
            self._pool.join()
            self._pool = None

    def save_policy_cache(self):
        _save_policy_cache(self._policy_cache)

    def _expected_score_at_pos(self, prior: Dict[Tuple[int, int], float], pos: Tuple[int, int]) -> float:
        score = 0.0
        for rp, p in prior.items():
            for col, prob in self.likelihoods[pos][rp].items():
                score += p * prob * SPHERE_SCORES[col]
        return score

    def _score_search_best_action(
        self,
        prior: Dict[Tuple[int, int], float],
        revealed_mask: int,
        clicks_left: int,
    ) -> Tuple[Optional[Tuple[int, int]], float]:
        if clicks_left <= 0 or not prior:
            return None, 0.0

        key = (clicks_left, revealed_mask, self._belief_key(prior))
        cached = self._score_cache.get(key)
        if cached is not None:
            return cached

        actions = self._candidate_actions(prior, revealed_mask)
        if not actions:
            return None, 0.0

        best_pos = None
        best_score = -1.0
        for pos_idx, pos in actions:
            score = self._score_expected_value_for_action(prior, pos, pos_idx, revealed_mask, clicks_left)
            if score > best_score:
                best_score = score
                best_pos = pos

        result = (best_pos, best_score)
        self._score_cache[key] = result
        return result

    def _score_expected_value_for_action(
        self,
        prior: Dict[Tuple[int, int], float],
        pos: Tuple[int, int],
        pos_idx: int,
        revealed_mask: int,
        clicks_left: int,
    ) -> float:
        immediate_score = self._expected_score_at_pos(prior, pos)
        if clicks_left <= 1:
            return immediate_score

        obs_probs = {col: 0.0 for col in SphereColor}
        for rp, p in prior.items():
            for col, prob in self.likelihoods[pos][rp].items():
                obs_probs[col] += p * prob

        expected_score = immediate_score
        next_mask = revealed_mask | (1 << pos_idx)
        for o, P_o in obs_probs.items():
            if P_o == 0:
                continue

            posterior_o = {}
            s = 0.0
            for rp, p in prior.items():
                like = self.likelihoods[pos][rp].get(o, 0.0)
                if like > 0:
                    posterior_o[rp] = p * like
                    s += posterior_o[rp]

            if s == 0:
                continue

            for rp in posterior_o:
                posterior_o[rp] /= s

            _, best_score = self._score_search_best_action(posterior_o, next_mask, clicks_left - 1)
            expected_score += P_o * best_score

        return expected_score

    def _search_best_action(
        self,
        prior: Dict[Tuple[int, int], float],
        revealed_mask: int,
        clicks_left: int,
    ) -> Tuple[Optional[Tuple[int, int]], float, float]:
        if clicks_left <= 0 or not prior:
            return None, 0.0, 0.0

        key = (clicks_left, revealed_mask, self._belief_key(prior))
        cached = self._policy_cache.get(key)
        if cached is not None:
            return cached

        actions = self._candidate_actions(prior, revealed_mask)
        if not actions:
            return None, 0.0, 0.0

        best_pos = None
        best_p = -1.0
        best_score = -1.0
        for pos_idx, pos in actions:
            p_red, score = self._expected_metrics_for_action(prior, pos, pos_idx, revealed_mask, clicks_left)
            if _better_pair(p_red, score, best_p, best_score):
                best_p = p_red
                best_score = score
                best_pos = pos

        result = (best_pos, best_p, best_score)
        self._policy_cache[key] = result
        return result

    def _expected_metrics_for_action(
        self,
        prior: Dict[Tuple[int, int], float],
        pos: Tuple[int, int],
        pos_idx: int,
        revealed_mask: int,
        clicks_left: int,
    ) -> Tuple[float, float]:
        immediate_p = prior.get(pos, 0.0)
        immediate_score = self._expected_score_at_pos(prior, pos)
        if clicks_left <= 1:
            return immediate_p, immediate_score

        obs_probs = {col: 0.0 for col in SphereColor}
        for rp, p in prior.items():
            for col, prob in self.likelihoods[pos][rp].items():
                obs_probs[col] += p * prob

        total_p = immediate_p
        total_score = immediate_score
        next_mask = revealed_mask | (1 << pos_idx)
        for o, P_o in obs_probs.items():
            if P_o == 0:
                continue
            if o == SphereColor.RED:
                if clicks_left > 1:
                    _, best_score = self._score_search_best_action({pos: 1.0}, next_mask, clicks_left - 1)
                    total_score += P_o * best_score
                continue

            posterior_o = {}
            s = 0.0
            for rp, p in prior.items():
                like = self.likelihoods[pos][rp].get(o, 0.0)
                if like > 0:
                    posterior_o[rp] = p * like
                    s += posterior_o[rp]

            if s == 0:
                continue

            for rp in posterior_o:
                posterior_o[rp] /= s

            _, best_p, best_score = self._search_best_action(posterior_o, next_mask, clicks_left - 1)
            total_p += P_o * best_p
            total_score += P_o * best_score

        return total_p, total_score

    def display_grid(self, suggestion: Optional[Tuple[int, int]] = None):
        """Print a 5x5 grid with coordinates and highlight the suggestion."""
        print("\n    1   2   3   4   5")
        print("  +---+---+---+---+---+")
        for r in range(5):
            row = f"{r+1} |"
            for c in range(5):
                if self.revealed[r][c]:
                    ch = self.observed[(r, c)].value
                    cell = f" {ch} "
                else:
                    cell = f"{r+1},{c+1}" if (r+1)+ (c+1) < 10 else f"{r+1},{c+1}"  # keep coord text
                if suggestion == (r, c) and not self.revealed[r][c]:
                    # mark suggestion with brackets
                    cell = f"[{cell}]"
                else:
                    cell = f" {cell} "
                row += cell + "|"
            print(row)
            print("  +---+---+---+---+---+")
        print()

    def bayes_update(self, click_pos: Tuple[int, int], observed_color: SphereColor):
        new_prior = {}
        total = 0.0
        for rp, p in self.prior.items():
            like = self.likelihoods[click_pos][rp].get(observed_color, 0.0)
            val = p * like
            if val > 0:
                new_prior[rp] = val
                total += val
        if total == 0:
            # Observation impossible under our model — fall back to uniform over VALID_POSITIONS
            print("[WARN] Quan sát không hợp lệ theo mô hình. Giữ prior đồng đều trên tất cả vị trí hợp lệ.")
            self.prior = {rp: 1.0 / len(VALID_POSITIONS) for rp in VALID_POSITIONS}
            return
        for rp in new_prior:
            new_prior[rp] /= total
        self.prior = new_prior

    def pick_click_ev(self) -> Tuple[int, int]:
        initial = self._is_initial_state()
        if initial:
            cached = _load_first_suggestion()
            if cached is not None:
                revealed_mask = self._mask_from_revealed()
                remaining = self.max_clicks - self.clicks_used
                depth = min(self.search_depth, remaining)
                p_red, exp_score = self._action_metrics(cached, revealed_mask, depth)
                self._set_last_metrics(p_red, exp_score)
                return cached

        def _ret(pos: Tuple[int, int], p_red: Optional[float] = None, exp_score: Optional[float] = None) -> Tuple[int, int]:
            if initial:
                _save_first_suggestion(pos)
            if p_red is None or exp_score is None:
                revealed_mask = self._mask_from_revealed()
                remaining = self.max_clicks - self.clicks_used
                depth = min(self.search_depth, remaining)
                p_red, exp_score = self._action_metrics(pos, revealed_mask, depth)
            self._set_last_metrics(p_red, exp_score)
            return pos

        if self.red_found:
            return _ret(self.pick_click_best_score())

        if self.prior:
            best_rp, best_p = max(self.prior.items(), key=lambda kv: kv[1])
            if best_p >= 0.999 and not self.revealed[best_rp[0]][best_rp[1]]:
                return _ret(best_rp)

        remaining = self.max_clicks - self.clicks_used
        depth = min(self.search_depth, remaining)
        if depth > 1:
            revealed_mask = self._mask_from_revealed()
            actions = self._candidate_actions(self.prior, revealed_mask)
            if PARALLEL_EVAL and len(actions) >= PARALLEL_MIN_ACTIONS:
                pool = self._get_pool()
                if pool is not None:
                    tasks = [(self.prior, pos, pos_idx, revealed_mask, depth) for pos_idx, pos in actions]
                    try:
                        values = pool.map(_worker_expected_value_task, tasks)
                        best_idx = 0
                        best_p, best_score = values[0]
                        for i in range(1, len(values)):
                            p_red, score = values[i]
                            if _better_pair(p_red, score, best_p, best_score):
                                best_p = p_red
                                best_score = score
                                best_idx = i
                        return _ret(actions[best_idx][1], best_p, best_score)
                    except Exception:
                        pass
            best_pos, best_p, best_score = self._search_best_action(self.prior, revealed_mask, depth)
            if best_pos is not None:
                return _ret(best_pos, best_p, best_score)

        if not self.prior:
            # fallback: first unrevealed
            for pos in ALL_POSITIONS:
                if not self.revealed[pos[0]][pos[1]]:
                    return _ret(pos)
            return _ret(ALL_POSITIONS[0])

        # If any prior >= threshold, click it
        best_rp, best_p = max(self.prior.items(), key=lambda kv: kv[1])
        if best_p >= self.threshold and not self.revealed[best_rp[0]][best_rp[1]]:
            return _ret(best_rp)

        unrevealed = [(r, c) for r, c in ALL_POSITIONS if not self.revealed[r][c]]
        best_ev = -1.0
        best_pos = None

        for pos in unrevealed:
            immediate = self.prior.get(pos, 0.0)

            # compute observation distribution at pos
            obs_probs = {col: 0.0 for col in SphereColor}
            for rp, p in self.prior.items():
                for col, prob in self.likelihoods[pos][rp].items():
                    obs_probs[col] += p * prob

            expected_next = 0.0
            for o, P_o in obs_probs.items():
                if P_o == 0 or o == SphereColor.RED:
                    continue
                # posterior given o
                posterior_o = {}
                s = 0.0
                for rp, p in self.prior.items():
                    like = self.likelihoods[pos][rp].get(o, 0.0)
                    if like > 0:
                        posterior_o[rp] = p * like
                        s += posterior_o[rp]
                if s == 0:
                    continue
                for rp in posterior_o:
                    posterior_o[rp] /= s
                best_next = max(posterior_o.values()) if posterior_o else 0.0
                expected_next += P_o * best_next

            ev = immediate + expected_next
            if ev > best_ev:
                best_ev = ev
                best_pos = pos

        if best_pos is None:
            # fallback choose unrevealed with highest prior
            cand = sorted(self.prior.items(), key=lambda kv: -kv[1])
            for rp, _ in cand:
                if not self.revealed[rp[0]][rp[1]]:
                    return _ret(rp)
            return _ret(unrevealed[0])

        return _ret(best_pos)

    def expected_score(self, pos: Tuple[int, int]) -> float:
        expected = 0.0
        for rp, p in self.prior.items():
            for col, prob in self.likelihoods[pos][rp].items():
                expected += p * prob * SPHERE_SCORES[col]
        return expected

    def pick_click_best_score(self) -> Tuple[int, int]:
        remaining = self.max_clicks - self.clicks_used
        depth = min(self.search_depth, remaining)
        if depth > 1 and self.prior:
            revealed_mask = self._mask_from_revealed()
            best_pos, _ = self._score_search_best_action(self.prior, revealed_mask, depth)
            if best_pos is not None:
                return best_pos

        unrevealed = [(r, c) for r, c in ALL_POSITIONS if not self.revealed[r][c]]
        best_score = -1.0
        best_pos = None
        for pos in unrevealed:
            score = self.expected_score(pos)
            if score > best_score:
                best_score = score
                best_pos = pos
        return best_pos if best_pos is not None else unrevealed[0]

    def top_candidates(self, k: int = 5):
        items = sorted(self.prior.items(), key=lambda kv: -kv[1])[:k]
        return items


def parse_color(s: str) -> Optional[SphereColor]:
    if not s:
        return None
    s = s.strip().upper()
    mapping = {
        'R': SphereColor.RED, 'RED': SphereColor.RED,
        'O': SphereColor.ORANGE, 'ORANGE': SphereColor.ORANGE,
        'Y': SphereColor.YELLOW, 'YELLOW': SphereColor.YELLOW,
        'G': SphereColor.GREEN, 'GREEN': SphereColor.GREEN,
        'T': SphereColor.TEAL, 'TEAL': SphereColor.TEAL,
        'B': SphereColor.BLUE, 'BLUE': SphereColor.BLUE,
    }
    return mapping.get(s, None)


def interactive_loop():
    solver = InteractiveSolver()
    start_time = time.time()

    print("\n=== OC Interactive Suggester ===")
    print(f"Bạn có thể click tối đa {MAX_CLICKS} lần. Thời gian tổng tối đa: {TIME_LIMIT_SECONDS} giây.")
    print("Nhập màu bằng mã: R,O,Y,G,T,B (ví dụ: B hoặc blue). Nếu bạn click ô khác ô gợi ý, trước hết nhập 'r c' rồi nhập màu.")

    while solver.clicks_used < solver.max_clicks:
        elapsed = time.time() - start_time
        if elapsed > TIME_LIMIT_SECONDS:
            print("[TIMEOUT] Hết 2 phút. Kết thúc phiên tương tác.")
            break

        t_suggest = time.perf_counter()
        suggestion = solver.pick_click_ev()
        _log_load_timing(
            "first_suggestion" if solver.clicks_used == 0 else "next_suggestion",
            time.perf_counter() - t_suggest,
        )
        msg = f"\nGợi ý ô tiếp theo (click #{solver.clicks_used + 1}): ({suggestion[0]+1},{suggestion[1]+1})"
        if solver.last_metrics is not None and solver.last_metrics[0] >= 1.0 - _PAIR_EPS:
            msg += " [Guaranteed RED]"
        print(msg)
        solver.display_grid(suggestion)

        raw = input("Nhập kết quả (hoặc 'q' để thoát): ").strip()
        if not raw:
            print("Hãy nhập màu hoặc tọa độ + màu.")
            continue
        if raw.lower() == 'q':
            print("Thoát tương tác.")
            break

        parts = raw.split()
        if len(parts) == 1:
            # assume this is a color for the suggested pos
            coord = suggestion
            color = parse_color(parts[0])
        else:
            # expect: r c color  OR r c
            try:
                r = int(parts[0]) - 1
                c = int(parts[1]) - 1
                coord = (r, c)
                if len(parts) >= 3:
                    color = parse_color(parts[2])
                else:
                    color_raw = input(f"Nhập màu tại ({r+1},{c+1}): ").strip()
                    color = parse_color(color_raw)
            except Exception:
                print("Đầu vào không đúng. Ví dụ hợp lệ: 'B' hoặc '1 5 B' hoặc '2 3'.")
                continue

        if color is None:
            print("Mã màu không hợp lệ. Dùng một trong: R O Y G T B")
            continue

        r, c = coord
        if not (0 <= r < 5 and 0 <= c < 5):
            print("Tọa độ ngoài phạm vi 5x5.")
            continue

        if solver.revealed[r][c]:
            print("Ô này đã được nhập trước đó.")
            continue

        solver.revealed[r][c] = True
        solver.observed[(r, c)] = color
        solver.clicks_used += 1

        print(f"Kết quả ô ({r+1},{c+1}) = {color.name}")

        if color == SphereColor.RED:
            solver.red_found = True
            solver.red_pos = (r, c)
            print("\nTim thay RED! Tiep tuc goi y toi uu diem cho cac luot con lai.")
            solver.display_grid(None)
        # Update beliefs with Bayes
        solver.bayes_update((r, c), color)

        # Show top candidatesr
        tops = solver.top_candidates(5)
        print("Ứng viên hàng đầu (vị trí : xác suất):")
        for pos, p in tops:
            print(f"  ({pos[0]+1},{pos[1]+1}) : {p:.2%}")

    print("\nKết thúc: không còn lượt hoặc đã dừng. Nếu chưa tìm red, hãy kiểm tra lại trò chơi thực tế.")


    solver.save_policy_cache()


def main():
    parser = argparse.ArgumentParser(description="OC Interactive Suggester")
    parser.add_argument(
        "--build-cache",
        action="store_true",
        help="Precompute and write likelihood cache, then exit.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Force rebuild of the likelihood cache, then exit.",
    )
    args = parser.parse_args()

    if args.build_cache or args.rebuild_cache:
        _build_cache_file(force_rebuild=args.rebuild_cache)
        return

    while True:
        interactive_loop()
        choice = input("\nRestart? (y/n): ").strip().lower()
        if choice not in ("y", "yes"):
            break


if __name__ == '__main__':
    main()

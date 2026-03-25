from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path
from typing import Tuple

import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mudae.ouro.Oq_solver import (  # noqa: E402
    MAX_CLICKS,
    NEIGHBORS,
    OBS_PURPLE,
    OQ_BEAM_K_DEFAULT,
    OQ_POLICY_MODE_BEAM,
    OQ_POLICY_MODE_EXACT_REF,
    POSITIONS,
    TOTAL_PURPLES,
    OqSolver,
)


def _simulate_single_game(
    config_set: set[int],
    *,
    policy_mode: str,
    max_clicks: int,
    cache_ram_mb: int,
    cache_version: str,
    beam_k: int,
    cache_max_gb: float,
) -> bool:
    solver = OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        policy_mode=policy_mode,
        beam_k=beam_k,
        cache_max_gb=cache_max_gb,
    )

    while solver.clicks_left > 0 and solver.found_purples < 3:
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

    return solver.found_purples >= 3


def _wald_95_ci(successes: int, total: int) -> Tuple[float, float, float]:
    if total <= 0:
        return 0.0, 0.0, 0.0
    p = successes / total
    se = math.sqrt(max(0.0, p * (1.0 - p) / total))
    delta = 1.96 * se
    return p, max(0.0, p - delta), min(1.0, p + delta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark OQ beam runtime vs exact reference")
    parser.add_argument("--games", type=int, default=300, help="Number of games to simulate")
    parser.add_argument("--seed", type=int, default=12345, help="PRNG seed")
    parser.add_argument("--clicks", type=int, default=MAX_CLICKS, help="Max non-purple clicks")
    parser.add_argument("--beam-k", type=int, default=OQ_BEAM_K_DEFAULT, help="Beam width for runtime policy")
    parser.add_argument("--beam-cache-ram-mb", type=int, default=1024, help="Beam policy RAM cache")
    parser.add_argument("--exact-cache-ram-mb", type=int, default=2048, help="Exact reference RAM cache")
    parser.add_argument("--beam-cache-max-gb", type=float, default=10.0, help="Beam persistent cache cap")
    parser.add_argument("--exact-cache-max-gb", type=float, default=1000.0, help="Exact reference cache cap")
    parser.add_argument(
        "--accuracy-floor",
        type=float,
        default=0.97,
        help="Required beam/exact success ratio floor",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    run_tag = int(time.time())
    beam_cache_version = f"bench_beam_k{args.beam_k}_{run_tag}"
    exact_cache_version = f"bench_exact_ref_{run_tag}"

    beam_success = 0
    exact_success = 0
    started = time.perf_counter()
    for _ in range(max(0, args.games)):
        config = rng.sample(POSITIONS, TOTAL_PURPLES)
        config_set = set(config)
        if _simulate_single_game(
            config_set,
            policy_mode=OQ_POLICY_MODE_BEAM,
            max_clicks=args.clicks,
            cache_ram_mb=args.beam_cache_ram_mb,
            cache_version=beam_cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.beam_cache_max_gb,
        ):
            beam_success += 1
        if _simulate_single_game(
            config_set,
            policy_mode=OQ_POLICY_MODE_EXACT_REF,
            max_clicks=args.clicks,
            cache_ram_mb=args.exact_cache_ram_mb,
            cache_version=exact_cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.exact_cache_max_gb,
        ):
            exact_success += 1

    elapsed = time.perf_counter() - started
    beam_p, beam_lo, beam_hi = _wald_95_ci(beam_success, args.games)
    exact_p, exact_lo, exact_hi = _wald_95_ci(exact_success, args.games)
    rel = (beam_p / exact_p) if exact_p > 0 else 0.0

    print(f"Games: {args.games} seed={args.seed} clicks={args.clicks} beam_k={args.beam_k}")
    print(f"Elapsed: {elapsed:.2f}s")
    print(
        f"Beam  : {beam_success}/{args.games} = {beam_p:.4%} "
        f"(95% CI: {beam_lo:.4%} .. {beam_hi:.4%})"
    )
    print(
        f"Exact : {exact_success}/{args.games} = {exact_p:.4%} "
        f"(95% CI: {exact_lo:.4%} .. {exact_hi:.4%})"
    )
    print(f"Relative beam/exact success ratio: {rel:.4%}")

    if rel >= args.accuracy_floor:
        print(f"PASS: ratio {rel:.4%} >= floor {args.accuracy_floor:.4%}")
    else:
        print(f"FAIL: ratio {rel:.4%} < floor {args.accuracy_floor:.4%}")


if __name__ == "__main__":
    main()

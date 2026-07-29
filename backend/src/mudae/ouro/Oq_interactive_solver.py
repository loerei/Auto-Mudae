"""
oq_interactive_solver.py
Interactive suggestion program for Ouro Quest ($oq) purple-finding game.
Uses the beam runtime solver and cache from Oq_solver.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from typing import Any, Dict, Optional, Tuple

from mudae.ouro.Oq_solver import (
    GRID_SIZE,
    MAX_CLICKS,
    OBS_BLUE,
    OBS_GREEN,
    OBS_NAME,
    OBS_ORANGE,
    OBS_PURPLE,
    OBS_SHORT,
    OBS_TEAL,
    OBS_YELLOW,
    OQ_BEAM_K_DEFAULT,
    OQ_CACHE_VERSION_DEFAULT,
    OQ_DEFAULT_CACHE_MAX_GB,
    OQ_DEFAULT_RAM_CACHE_MB,
    TARGET_PURPLES,
    OqSolver,
    build_cache_for_initial_state,
    idx_to_rc,
    rc_to_idx,
    trim_cache_to_first_branch,
)

TIME_LIMIT_SECONDS = 9999

INPUT_MAP = {
    "B": OBS_BLUE,
    "BLUE": OBS_BLUE,
    "0": OBS_BLUE,
    "T": OBS_TEAL,
    "TEAL": OBS_TEAL,
    "1": OBS_TEAL,
    "G": OBS_GREEN,
    "GREEN": OBS_GREEN,
    "2": OBS_GREEN,
    "Y": OBS_YELLOW,
    "YELLOW": OBS_YELLOW,
    "3": OBS_YELLOW,
    "O": OBS_ORANGE,
    "ORANGE": OBS_ORANGE,
    "4": OBS_ORANGE,
    "P": OBS_PURPLE,
    "PURPLE": OBS_PURPLE,
}


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


def _render_cache_build_progress(progress: Dict[str, Any]) -> None:
    total_branches = int(progress.get("total_branches") or 0)
    completed_branches = int(progress.get("completed_branches") or 0)
    active_branch_index = int(progress.get("active_branch_index") or 0)
    total_positions = int(progress.get("total_positions") or 0)
    completed_positions = int(progress.get("completed_positions") or 0)
    active_position_index = int(progress.get("active_position_index") or 0)
    completed_states = int(progress.get("completed_states") or 0)
    elapsed_sec = float(progress.get("elapsed_sec") or 0.0)
    current_pos_raw = progress.get("current_pos")
    current_pos = int(current_pos_raw) if isinstance(current_pos_raw, int) else None

    if total_branches <= 0:
        ratio = 1.0
    else:
        ratio = max(0.0, min(1.0, completed_branches / total_branches))
    pct_value = ratio * 100.0
    if pct_value <= 0.0 and completed_states > 0:
        pct_text = "<0.01%"
    else:
        pct_text = f"{pct_value:6.2f}%"

    width = 30
    filled = int(width * ratio)
    bar = ("#" * filled) + ("-" * (width - filled))

    cache_stats = progress.get("cache_stats")
    writes = 0
    mem_mb = 0.0
    db_size_gb = 0.0
    db_cap_gb = 0.0
    db_cap_hit = False
    if isinstance(cache_stats, dict):
        writes = int(cache_stats.get("writes", 0))
        mem_mb = float(cache_stats.get("memory_bytes", 0)) / (1024 * 1024)
        db_size_gb = float(cache_stats.get("db_size_bytes", 0)) / (1024 * 1024 * 1024)
        db_cap_gb = float(cache_stats.get("max_db_bytes", 0)) / (1024 * 1024 * 1024)
        db_cap_hit = bool(int(cache_stats.get("db_writes_disabled", 0)))

    prev_elapsed = float(getattr(_render_cache_build_progress, "_prev_elapsed", 0.0))
    prev_writes = int(getattr(_render_cache_build_progress, "_prev_writes", 0))
    if elapsed_sec < prev_elapsed:
        prev_elapsed = 0.0
        prev_writes = 0
    rate_cached = 0.0
    if elapsed_sec > prev_elapsed:
        rate_cached = max(0.0, (writes - prev_writes) / (elapsed_sec - prev_elapsed))
    _render_cache_build_progress._prev_elapsed = elapsed_sec  # type: ignore[attr-defined]
    _render_cache_build_progress._prev_writes = writes  # type: ignore[attr-defined]

    if bool(progress.get("finished")):
        active_text = "-"
    elif current_pos is None:
        active_text = "?"
    else:
        r, c = idx_to_rc(current_pos)
        active_text = f"({r + 1},{c + 1})"

    if active_position_index <= 0 and not bool(progress.get("finished")) and total_positions > 0:
        active_position_index = min(total_positions, completed_positions + 1)

    eta_text = "eta=?"
    if bool(progress.get("finished")):
        eta_text = "eta=0s"
    elif completed_branches > 0 and total_branches > completed_branches and elapsed_sec > 0:
        branch_rate = completed_branches / elapsed_sec
        if branch_rate > 0:
            eta_sec = int((total_branches - completed_branches) / branch_rate)
            mins, secs = divmod(max(0, eta_sec), 60)
            hrs, mins = divmod(mins, 60)
            if hrs > 0:
                eta_text = f"eta~{hrs}h{mins:02d}m"
            elif mins > 0:
                eta_text = f"eta~{mins}m{secs:02d}s"
            else:
                eta_text = f"eta~{secs}s"

    if not bool(progress.get("finished")) and total_branches > 0:
        if active_branch_index <= 0:
            active_branch_index = min(total_branches, completed_branches + 1)
        task_text = f"task {active_branch_index}/{total_branches}"
    else:
        task_text = f"task {completed_branches}/{total_branches}"

    message = (
        f"[OQ] Build [{bar}] {pct_text} "
        f"{task_text} done={completed_branches}/{total_branches} "
        f"cell {active_position_index}/{total_positions} {active_text} "
        f"solved={completed_states:,} cached={writes:,} +{rate_cached:,.0f}/s "
        f"mem={mem_mb:.1f}MB db={db_size_gb:.2f}/{db_cap_gb:.2f}GB "
        f"{'CAP-HIT ' if db_cap_hit else ''}t={elapsed_sec:.1f}s {eta_text}"
    )

    # Dynamic width control avoids wrapped carriage-return output in narrow terminals.
    cols = max(40, int(shutil.get_terminal_size(fallback=(120, 30)).columns))
    max_len = max(20, cols - 1)
    if len(message) > max_len:
        if max_len > 3:
            message = message[: max_len - 3] + "..."
        else:
            message = message[:max_len]

    finished = bool(progress.get("finished"))
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())

    if is_tty:
        last_len = int(getattr(_render_cache_build_progress, "_last_render_len", 0))
        padded = message
        if len(padded) < last_len:
            padded += " " * (last_len - len(padded))
        print("\r" + padded, end="", flush=True)
        _render_cache_build_progress._last_render_len = len(padded)  # type: ignore[attr-defined]
        if finished:
            print()
    else:
        now = time.time()
        last_print = float(getattr(_render_cache_build_progress, "_last_print_time", 0.0))
        if finished or (now - last_print) >= 1.0:
            print(message, flush=True)
            _render_cache_build_progress._last_print_time = now  # type: ignore[attr-defined]

    if finished:
        _render_cache_build_progress._prev_elapsed = 0.0  # type: ignore[attr-defined]
        _render_cache_build_progress._prev_writes = 0  # type: ignore[attr-defined]
        _render_cache_build_progress._last_render_len = 0  # type: ignore[attr-defined]


def parse_observation(raw: str) -> Tuple[Optional[int], bool]:
    if not raw:
        return None, False
    key = raw.strip().upper()
    if key in {"R", "RED"}:
        return OBS_PURPLE, True
    return INPUT_MAP.get(key), False


def display_grid(observed: Dict[int, int], suggestion: Optional[int] = None) -> None:
    print("\n    1   2   3   4   5")
    print("  +---+---+---+---+---+")
    for r in range(GRID_SIZE):
        row = f"{r + 1} |"
        for c in range(GRID_SIZE):
            idx = rc_to_idx(r, c)
            if idx in observed:
                cell = f" {OBS_SHORT[observed[idx]]} "
            else:
                cell = f"{r + 1},{c + 1}"
            if suggestion == idx and idx not in observed:
                cell = f"[{cell}]"
            else:
                cell = f" {cell} "
            row += cell + "|"
        print(row)
        print("  +---+---+---+---+---+")
    print()


def _print_cache_stats(solver: OqSolver) -> None:
    stats = solver.cache_stats(include_db=False)
    db_mb = stats.get("db_size_bytes", 0) / 1024 / 1024
    cap_hit = bool(int(stats.get("db_writes_disabled", 0)))
    print(
        "Cache stats: "
        f"mem_entries={stats.get('memory_entries')} "
        f"mem_mb={stats.get('memory_bytes', 0)/1024/1024:.1f} "
        f"db_mb={db_mb:.1f} "
        f"db_cap_hit={cap_hit} "
        f"hits(mem/disk)={stats.get('hits_memory')}/{stats.get('hits_disk')} "
        f"misses={stats.get('misses')} "
        f"evictions={stats.get('evictions')}"
    )


def interactive_loop(
    *,
    max_clicks: int = MAX_CLICKS,
    time_limit_seconds: int = TIME_LIMIT_SECONDS,
    cache_ram_mb: int = OQ_DEFAULT_RAM_CACHE_MB,
    cache_version: str = OQ_CACHE_VERSION_DEFAULT,
    beam_k: int = OQ_BEAM_K_DEFAULT,
    cache_max_gb: float = OQ_DEFAULT_CACHE_MAX_GB,
    show_cache_stats: bool = False,
    show_build_progress: bool = True,
) -> None:
    solver = OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=cache_ram_mb,
        cache_version=cache_version,
        beam_k=beam_k,
        cache_max_gb=cache_max_gb,
    )
    observed: Dict[int, int] = {}
    start_time = time.time()

    print("\n=== OQ Interactive Suggester ===")
    print(f"Max clicks: {max_clicks}. Time limit: {time_limit_seconds} seconds.")
    print(f"Policy: beam-k={beam_k}")
    print(f"Cache: version={cache_version}, ram={cache_ram_mb}MB, cap={cache_max_gb:.1f}GB")
    print("Purple does not consume a click. Red consumes one click.")
    print("Input colors: B/T/G/Y/O/P (or full names).")
    print("Use R/Red if the red sphere is shown.")
    print("Input formats:")
    print("  color            -> apply to suggested cell")
    print("  r c color        -> apply to specific cell (e.g. '2 4 G')")
    print("Input color code: B,T,G,Y,O,P,R. If you clicked another cell, use 'r c color'.")
    print("Type 'q' to quit.")

    while solver.clicks_left > 0:
        if time.time() - start_time > time_limit_seconds:
            print("[TIMEOUT] Ending session.")
            break

        progress_cb = None
        if show_build_progress and solver.revealed_mask == 0 and solver.found_purples == 0:
            progress_cb = _render_cache_build_progress

        suggestion = solver.pick_next_click(progress_callback=progress_cb)
        if suggestion is not None:
            r, c = idx_to_rc(suggestion)
            print(
                f"\nNext suggestion (click #{solver.clicks_used + 1}): "
                f"({r + 1},{c + 1})"
            )
        else:
            print("\nNo suggestion available (target reached or no clicks left).")
        display_grid(observed, suggestion)
        if show_cache_stats:
            _print_cache_stats(solver)

        raw = input("Enter result (or 'q' to quit): ").strip()
        if not raw:
            print("Enter a color or coordinates + color.")
            continue
        command = raw.lower()
        if command in {"q", "quit", "exit"}:
            print("Exit interactive mode.")
            break
        if command == "stats":
            _print_cache_stats(solver)
            continue

        parts = raw.split()
        if len(parts) == 1:
            if suggestion is None:
                print("No suggestion left. Enter coordinates + color.")
                continue
            coord_idx = suggestion
            obs_code, is_red = parse_observation(parts[0])
        else:
            try:
                rr = int(parts[0]) - 1
                cc = int(parts[1]) - 1
                if not (0 <= rr < GRID_SIZE and 0 <= cc < GRID_SIZE):
                    print("Coordinates out of 5x5 range.")
                    continue
                coord_idx = rc_to_idx(rr, cc)
                if len(parts) >= 3:
                    obs_code, is_red = parse_observation(parts[2])
                else:
                    obs_code, is_red = parse_observation(input(f"Color at ({rr + 1},{cc + 1}): ").strip())
            except ValueError:
                print("Invalid input. Example: 'B' or '1 5 O' or '2 3 G'.")
                continue

        if obs_code is None:
            print("Invalid color code. Use one of: B T G Y O P R")
            continue

        if coord_idx in observed:
            print("This cell was already entered.")
            continue

        if is_red:
            # Red consumes one click in-game.
            solver.note_red(coord_idx)
            solver.consume_click(1)
            observed[coord_idx] = OBS_PURPLE
        else:
            solver.apply_observation(coord_idx, obs_code)
            observed[coord_idx] = obs_code

        rr, cc = idx_to_rc(coord_idx)
        print(
            f"Result cell ({rr + 1},{cc + 1}) = {OBS_NAME[obs_code]} | "
            f"purples={solver.found_purples} clicks_left={solver.clicks_left}"
        )
        print(f"Current success probability: {solver.current_success_prob():.2%}")

        if solver.found_purples >= TARGET_PURPLES:
            print(f"\nFound {solver.found_purples} PURPLE. End quest solve loop.")
            break

        tops = solver.top_purple_candidates(5)
        if tops:
            print("Top PURPLE candidates (position : probability):")
            for pos, p in tops:
                tr, tc = idx_to_rc(pos)
                print(f"  ({tr + 1},{tc + 1}) : {p:.2%}")

    print("\nEnd: no remaining clicks or stopped.")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ouro Quest interactive beam solver")
    default_cache_ram_mb = _env_int("OQ_CACHE_RAM_MB", OQ_DEFAULT_RAM_CACHE_MB)
    default_beam_k = _env_int("OQ_BEAM_K", OQ_BEAM_K_DEFAULT)
    default_cache_max_gb = _env_float("OQ_CACHE_MAX_GB", OQ_DEFAULT_CACHE_MAX_GB)
    parser.add_argument("--clicks", type=int, default=MAX_CLICKS, help="Max non-purple clicks")
    parser.add_argument("--time-limit", type=int, default=TIME_LIMIT_SECONDS, help="Session time limit in seconds")
    parser.add_argument(
        "--cache-ram-mb",
        type=int,
        default=default_cache_ram_mb,
        help="In-process cache RAM limit (or env OQ_CACHE_RAM_MB)",
    )
    parser.add_argument("--beam-k", type=int, default=default_beam_k, help="Beam width (or env OQ_BEAM_K)")
    parser.add_argument(
        "--cache-max-gb",
        type=float,
        default=default_cache_max_gb,
        help="Persistent cache cap in GB (or env OQ_CACHE_MAX_GB)",
    )
    parser.add_argument("--cache-version", type=str, default=OQ_CACHE_VERSION_DEFAULT, help="Cache version key")
    parser.add_argument("--build-cache", action="store_true", help="Prebuild initial-state beam cache before loop")
    parser.add_argument("--rebuild-cache", action="store_true", help="Rebuild initial-state beam cache before loop")
    parser.add_argument(
        "--trim-after-build",
        action="store_true",
        help="Trim cache to first-branch states after build",
    )
    parser.add_argument(
        "--trim-mode",
        type=str,
        choices=("policy_eval", "first_bit"),
        default="policy_eval",
        help="Trim strategy: policy_eval (smaller) or first_bit (faster coarse trim)",
    )
    parser.add_argument(
        "--no-trim-vacuum",
        action="store_true",
        help="Skip VACUUM after trim (faster, but file may not shrink immediately)",
    )
    parser.add_argument("--show-cache-stats", action="store_true", help="Show cache stats each turn")
    parser.add_argument("--no-build-progress", action="store_true", help="Disable initial cache build progress bar")
    args = parser.parse_args(argv)

    if args.build_cache or args.rebuild_cache:
        info = build_cache_for_initial_state(
            max_clicks=args.clicks,
            cache_ram_mb=args.cache_ram_mb,
            cache_version=args.cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.cache_max_gb,
            rebuild=args.rebuild_cache,
            progress_callback=(None if args.no_build_progress else _render_cache_build_progress),
        )
        print(
            f"[OQ] Beam cache build done in {info['elapsed_sec']:.2f}s, "
            f"best_pos={info['best_pos']}, success_prob={info['success_prob']:.6f}"
        )
        if args.trim_after_build:
            trim_info = trim_cache_to_first_branch(
                max_clicks=args.clicks,
                cache_version=args.cache_version,
                beam_k=args.beam_k,
                vacuum=(not args.no_trim_vacuum),
                mode=args.trim_mode,
            )
            before_gb = float(trim_info["before_bytes"]) / (1024 * 1024 * 1024)
            after_gb = float(trim_info["after_bytes"]) / (1024 * 1024 * 1024)
            print(
                f"[OQ] Trim done mode={trim_info['mode']} first_pos={trim_info['first_pos']} "
                f"rows {trim_info['before_rows']} -> {trim_info['after_rows']} "
                f"size {before_gb:.2f}GB -> {after_gb:.2f}GB"
            )
        return

    while True:
        interactive_loop(
            max_clicks=args.clicks,
            time_limit_seconds=args.time_limit,
            cache_ram_mb=args.cache_ram_mb,
            cache_version=args.cache_version,
            beam_k=args.beam_k,
            cache_max_gb=args.cache_max_gb,
            show_cache_stats=args.show_cache_stats,
            show_build_progress=(not args.no_build_progress),
        )
        choice = input("\nRestart? (y/n): ").strip().lower()
        if choice not in ("y", "yes"):
            break


if __name__ == "__main__":
    main()

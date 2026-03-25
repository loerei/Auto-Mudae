from __future__ import annotations

from functools import lru_cache

import pytest

import mudae.ouro.Oq_solver as oq


def _mask_from_indices(indices: list[int]) -> int:
    mask = 0
    for idx in indices:
        mask |= 1 << idx
    return mask


def _reference_best_success_prob(
    possible_mask: int,
    revealed_mask: int,
    found_purples: int,
    clicks_left: int,
) -> float:
    @lru_cache(maxsize=None)
    def rec(pm: int, rm: int, fp: int, cl: int) -> float:
        if fp >= oq.TARGET_PURPLES:
            return 1.0
        if cl <= 0:
            return 0.0
        if pm == 0:
            return 0.0
        total = pm.bit_count()
        if total == 0:
            return 0.0

        best = 0.0
        for pos in oq.POSITIONS:
            if rm & (1 << pos):
                continue
            expected = 0.0
            for obs_code, obs_mask in enumerate(oq.OBS_MASKS[pos]):
                subset = pm & obs_mask
                if subset == 0:
                    continue
                prob = subset.bit_count() / total
                if obs_code == oq.OBS_PURPLE:
                    val = rec(subset, rm | (1 << pos), fp + 1, cl)
                else:
                    val = rec(subset, rm | (1 << pos), fp, cl - 1)
                expected += prob * val
            if expected > best:
                best = expected
                if best >= 1.0:
                    break
        return best

    return rec(possible_mask, revealed_mask, found_purples, clicks_left)


def _reference_pick_next(
    possible_mask: int,
    revealed_mask: int,
    found_purples: int,
    clicks_left: int,
) -> int | None:
    if found_purples >= oq.TARGET_PURPLES or clicks_left <= 0:
        return None
    if possible_mask == 0:
        for pos in oq.POSITIONS:
            if not (revealed_mask & (1 << pos)):
                return pos
        return None

    total = possible_mask.bit_count()
    if total == 0:
        return None

    best_pos = None
    best_val = -1.0
    for pos in oq.POSITIONS:
        if revealed_mask & (1 << pos):
            continue
        expected = 0.0
        for obs_code, obs_mask in enumerate(oq.OBS_MASKS[pos]):
            subset = possible_mask & obs_mask
            if subset == 0:
                continue
            prob = subset.bit_count() / total
            if obs_code == oq.OBS_PURPLE:
                val = _reference_best_success_prob(subset, revealed_mask | (1 << pos), found_purples + 1, clicks_left)
            else:
                val = _reference_best_success_prob(subset, revealed_mask | (1 << pos), found_purples, clicks_left - 1)
            expected += prob * val
        if expected > best_val:
            best_val = expected
            best_pos = pos
    return best_pos


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ouro"
    cache_dir.mkdir()
    monkeypatch.setattr(oq, "OURO_CACHE_DIR", cache_dir)
    oq.reset_global_state_cache()
    yield
    oq.reset_global_state_cache()


@pytest.mark.parametrize(
    "possible_indices,revealed_mask,found_purples,clicks_left",
    [
        ([0, 1, 2, 3], 0, 0, 2),
        ([1, 4, 7, 9, 12], 1 << 5, 1, 2),
        ([2, 6, 11, 15, 20, 24], (1 << 0) | (1 << 1), 0, 3),
    ],
)
def test_exact_solver_matches_reference(possible_indices, revealed_mask, found_purples, clicks_left):
    solver = oq.OqSolver(
        max_clicks=oq.MAX_CLICKS,
        cache_ram_mb=32,
        cache_version="exactness_test_v2",
        policy_mode=oq.OQ_POLICY_MODE_EXACT_REF,
    )
    solver.possible_mask = _mask_from_indices(possible_indices)
    solver.revealed_mask = revealed_mask
    solver.found_purples = found_purples
    solver.clicks_left = clicks_left

    expected_prob = _reference_best_success_prob(
        solver.possible_mask,
        solver.revealed_mask,
        solver.found_purples,
        solver.clicks_left,
    )
    expected_pos = _reference_pick_next(
        solver.possible_mask,
        solver.revealed_mask,
        solver.found_purples,
        solver.clicks_left,
    )

    assert solver.current_success_prob() == pytest.approx(expected_prob, abs=1e-12)
    assert solver.pick_next_click() == expected_pos

from __future__ import annotations

import pickle

import pytest

import mudae.ouro.Oq_solver as oq


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ouro"
    cache_dir.mkdir()
    monkeypatch.setattr(oq, "OURO_CACHE_DIR", cache_dir)
    oq.reset_global_state_cache()
    yield cache_dir
    oq.reset_global_state_cache()


def test_state_cache_persists_entries_to_disk(_isolated_cache_dir):
    cache_path = _isolated_cache_dir / "state_cache.sqlite3"

    cache = oq.OqStateCache(cache_path=cache_path, cache_ram_mb=8, version="persist_test_v2")
    key = (123456789, 0b1010, 1, 3)
    cache.set(key, 0.375)
    cache.flush()
    cache.close()

    reopened = oq.OqStateCache(cache_path=cache_path, cache_ram_mb=8, version="persist_test_v2")
    assert reopened.get(key) == pytest.approx(0.375, abs=1e-12)
    stats = reopened.stats(include_db=True)
    assert stats["db_entries"] >= 1
    reopened.close()


def test_state_cache_respects_memory_cap(_isolated_cache_dir):
    cache_path = _isolated_cache_dir / "memory_cap.sqlite3"
    cache = oq.OqStateCache(cache_path=cache_path, cache_ram_mb=1, version="memcap_test_v2")

    base_mask = (1 << 8192) - 1
    for i in range(3000):
        key = (base_mask ^ i, i & ((1 << 25) - 1), i % 4, i % 8)
        cache.set(key, float(i))

    stats = cache.stats(include_db=False)
    assert stats["memory_bytes"] <= stats["memory_limit_bytes"] + stats["largest_entry_bytes"]
    assert stats["evictions"] > 0
    cache.close()


def test_legacy_pickle_cache_migrates(_isolated_cache_dir):
    legacy_path = _isolated_cache_dir / oq.OQ_LEGACY_CACHE_FILENAME
    legacy_payload = {
        "version": oq.OQ_LEGACY_CACHE_VERSION,
        "cache": {
            (1001, 0, 0, 2): 0.11,
            (2002, 1, 1, 2): 0.22,
            (3003, 2, 2, 2): 0.33,
        },
    }
    with open(legacy_path, "wb") as f:
        pickle.dump(legacy_payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    solver = oq.OqSolver(
        max_clicks=oq.MAX_CLICKS,
        cache_ram_mb=32,
        cache_version="migration_test_v2",
        policy_mode=oq.OQ_POLICY_MODE_EXACT_REF,
    )
    stats = solver.cache_stats(include_db=True)
    assert stats["db_entries"] >= 3
    assert solver._cache.get((1001, 0, 0, 2)) == pytest.approx(0.11, abs=1e-12)


def test_trim_to_first_branch_keeps_root_and_first_bit_states(_isolated_cache_dir):
    max_clicks = 7
    cache_version = "trim_test_v2"
    solver = oq.OqSolver(
        max_clicks=max_clicks,
        cache_ram_mb=32,
        cache_version=cache_version,
    )
    cache = solver._cache
    first_pos = 3
    first_bit = 1 << first_pos

    root_key = (oq.ALL_CONFIG_MASK, 0, 0, max_clicks)
    keep_key_1 = (123456, first_bit, 0, 6)
    keep_key_2 = (654321, first_bit | (1 << 7), 1, 5)
    drop_key_1 = (999999, 1 << 10, 0, 6)
    drop_key_2 = (888888, (1 << 10) | (1 << 12), 1, 5)

    cache.set(root_key, 0.1)
    cache.set(keep_key_1, 0.2)
    cache.set(keep_key_2, 0.3)
    cache.set(drop_key_1, 0.4)
    cache.set(drop_key_2, 0.5)
    cache.flush()

    policy_signature = oq._policy_signature(oq.OQ_POLICY_MODE_BEAM, oq.OQ_BEAM_K_DEFAULT)
    oq._save_first_suggestion(max_clicks, cache_version, policy_signature, first_pos)
    info = oq.trim_cache_to_first_branch(
        max_clicks=max_clicks,
        cache_version=cache_version,
        vacuum=False,
        mode="first_bit",
    )

    assert info["mode"] == "first_bit"
    assert info["first_pos"] == first_pos
    assert info["before_rows"] == 5
    assert info["after_rows"] == 3
    assert info["trimmed_rows"] == 2

    oq.reset_global_state_cache()
    policy_signature = oq._policy_signature(oq.OQ_POLICY_MODE_BEAM, oq.OQ_BEAM_K_DEFAULT)
    reopened = oq.OqStateCache(
        cache_path=oq._state_cache_path(cache_version),
        cache_ram_mb=8,
        version=cache_version,
        policy_signature=policy_signature,
    )
    assert reopened.get(root_key) == pytest.approx(0.1, abs=1e-12)
    assert reopened.get(keep_key_1) == pytest.approx(0.2, abs=1e-12)
    assert reopened.get(keep_key_2) == pytest.approx(0.3, abs=1e-12)
    assert reopened.get(drop_key_1) is None
    assert reopened.get(drop_key_2) is None
    reopened.close()


def test_trim_to_first_branch_policy_eval_keeps_policy_reachable_states(
    _isolated_cache_dir,
    monkeypatch,
):
    max_clicks = 1
    cache_version = "trim_policy_eval_test_v2"
    cache_path = oq._state_cache_path(cache_version)

    monkeypatch.setattr(oq, "POSITIONS", [0, 1])
    monkeypatch.setattr(
        oq,
        "OBS_MASKS",
        [
            (0b10, 0, 0, 0, 0, 0b01),  # pos 0: blue -> cfg1, purple -> cfg0
            (0b01, 0, 0, 0, 0, 0b10),  # pos 1: blue -> cfg0, purple -> cfg1
        ],
    )
    monkeypatch.setattr(oq, "ALL_CONFIG_MASK", 0b11)

    root = (0b11, 0, 0, max_clicks)
    # Root action children (all are needed for evaluating root in policy_eval mode).
    pos0_purple = (0b01, 1 << 0, 1, 1)
    pos0_blue = (0b10, 1 << 0, 0, 0)
    pos1_purple = (0b10, 1 << 1, 1, 1)
    pos1_blue = (0b01, 1 << 1, 0, 0)
    # Child needed for the next policy state (from pos0_purple, clicking pos1).
    second_step_child = (0b01, (1 << 0) | (1 << 1), 1, 0)
    # Unrelated row that should be pruned.
    dropped = (0b11, (1 << 0) | (1 << 1), 0, 1)

    cache = oq.OqStateCache(
        cache_path=cache_path,
        cache_ram_mb=8,
        version=cache_version,
        policy_signature=oq._policy_signature(oq.OQ_POLICY_MODE_BEAM, oq.OQ_BEAM_K_DEFAULT),
    )
    cache.set(root, 0.5)
    cache.set(pos0_purple, 0.9)
    cache.set(pos0_blue, 0.1)
    cache.set(pos1_purple, 0.4)
    cache.set(pos1_blue, 0.2)
    cache.set(second_step_child, 0.05)
    cache.set(dropped, 0.77)
    cache.flush()
    cache.close()

    policy_signature = oq._policy_signature(oq.OQ_POLICY_MODE_BEAM, oq.OQ_BEAM_K_DEFAULT)
    oq._save_first_suggestion(max_clicks, cache_version, policy_signature, 0)
    info = oq.trim_cache_to_first_branch(
        max_clicks=max_clicks,
        cache_version=cache_version,
        vacuum=False,
        mode="policy_eval",
    )

    assert info["mode"] == "policy_eval"
    assert info["before_rows"] == 7
    assert info["after_rows"] == 6
    assert info["trimmed_rows"] == 1

    oq.reset_global_state_cache()
    reopened = oq.OqStateCache(
        cache_path=cache_path,
        cache_ram_mb=8,
        version=cache_version,
        policy_signature=policy_signature,
    )
    assert reopened.get(root) == pytest.approx(0.5, abs=1e-12)
    assert reopened.get(pos0_purple) == pytest.approx(0.9, abs=1e-12)
    assert reopened.get(pos0_blue) == pytest.approx(0.1, abs=1e-12)
    assert reopened.get(pos1_purple) == pytest.approx(0.4, abs=1e-12)
    assert reopened.get(pos1_blue) == pytest.approx(0.2, abs=1e-12)
    assert reopened.get(second_step_child) == pytest.approx(0.05, abs=1e-12)
    assert reopened.get(dropped) is None
    reopened.close()


def test_state_cache_disables_db_writes_when_cap_reached(_isolated_cache_dir):
    cache_path = _isolated_cache_dir / "cap_test.sqlite3"
    cache = oq.OqStateCache(
        cache_path=cache_path,
        cache_ram_mb=8,
        version="cap_test_v2",
        policy_signature=oq._policy_signature(oq.OQ_POLICY_MODE_BEAM, oq.OQ_BEAM_K_DEFAULT),
        max_db_bytes=1,
    )

    key = (123456, 0, 0, 7)
    cache.set(key, 0.42)
    cache.flush()

    stats = cache.stats(include_db=True)
    assert stats["db_writes_disabled"] == 1
    assert stats["db_entries"] == 0
    assert cache.get(key) == pytest.approx(0.42, abs=1e-12)
    cache.close()

from __future__ import annotations

import json
import time
from pathlib import Path

from mudae.core import session_engine as Function
from mudae.storage import coordination as Coordination


def _write_foreign_lease(path: Path, expires_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scope": "test-scope",
        "owner_token": "foreign-owner",
        "owner_label": "foreign",
        "pid": 99999,
        "host": "test-host",
        "acquired_at": expires_at - 10.0,
        "updated_at": expires_at - 5.0,
        "expires_at": expires_at,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_message_indicates_roll_exhausted() -> None:
    assert Function._message_indicates_roll_exhausted("You don't have any rolls left.")
    assert Function._message_indicates_roll_exhausted("You have **0** rolls left. Next rolls reset in **59** min.")
    assert not Function._message_indicates_roll_exhausted("Some unrelated bot message")


def test_reconcile_roll_target_shrinks_when_live_rolls_drop() -> None:
    new_target, rolls_now, exhausted = Function._reconcile_roll_target(
        roll_count=13,
        rolls_to_make=14,
        refreshed_tu={"rolls": 0},
    )
    assert new_target == 13
    assert rolls_now == 0
    assert exhausted is True


def test_reconcile_roll_target_keeps_target_when_rolls_match_expectation() -> None:
    new_target, rolls_now, exhausted = Function._reconcile_roll_target(
        roll_count=5,
        rolls_to_make=14,
        refreshed_tu={"rolls": 9},
    )
    assert new_target == 14
    assert rolls_now == 9
    assert exhausted is False


def test_acquire_lease_blocks_on_live_foreign_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Coordination, "LEASE_DIR", tmp_path)
    path = Coordination._lease_path("test-scope")
    _write_foreign_lease(path, time.time() + 60.0)

    handle = Coordination.acquire_lease(
        "test-scope",
        "local-owner",
        ttl_sec=30.0,
        heartbeat_sec=1.0,
        wait_timeout_sec=0.05,
        poll_sec=0.01,
    )

    assert handle.acquired is False


def test_acquire_lease_reclaims_expired_foreign_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Coordination, "LEASE_DIR", tmp_path)
    path = Coordination._lease_path("test-scope")
    _write_foreign_lease(path, time.time() - 1.0)

    handle = Coordination.acquire_lease(
        "test-scope",
        "local-owner",
        ttl_sec=30.0,
        heartbeat_sec=1.0,
        wait_timeout_sec=0.05,
        poll_sec=0.01,
    )

    try:
        assert handle.acquired is True
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["owner_label"] == "local-owner"
    finally:
        handle.release()

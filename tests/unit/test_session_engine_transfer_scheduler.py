from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from mudae.core import session_engine as Session


class _AcquiredLease:
    acquired = True
    waited_sec = 0.0

    def __enter__(self) -> "_AcquiredLease":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def release(self) -> None:
        return None


def _stub_roll_common(monkeypatch: Any) -> None:
    Session.initial_tu_cache.clear()
    monkeypatch.setattr(
        Session,
        "Vars",
        type(
            "_Vars",
            (),
            {
                "tokens": [
                    {
                        "id": 2,
                        "name": "Loerei",
                        "discordusername": "loereidaparamecium",
                        "token": "tok-loerei",
                        "max_power": 110,
                    }
                ],
                "channelId": "channel",
                "serverId": "server",
                "SLEEP_SHORT_SEC": 0.0,
                "SLEEP_MED_SEC": 0.0,
                "SLEEP_LONG_SEC": 0.0,
                "ROLL_COORDINATION_ENABLED": True,
                "ROLL_LEASE_WAIT_SEC": 120.0,
                "ROLL_LEASE_TTL_SEC": 90.0,
                "ROLL_LEASE_HEARTBEAT_SEC": 10.0,
                "rollCommand": "wa",
            },
        ),
    )
    monkeypatch.setattr(Session, "_dashboard_set_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_set_wishlist", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_reset_roll_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "render_dashboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_warn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (object(), {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "getUrl", lambda: "https://discord.test/api/messages")
    monkeypatch.setattr(Session, "acquire_lease", lambda *_args, **_kwargs: _AcquiredLease())
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: ("901139470747832390", "loereidaparamecium"))


def _stub_scheduler_common(monkeypatch: Any, tmp_path: Path) -> None:
    Session._auto_give_seen_keys.clear()
    Session._last_tu_info_cache.clear()
    Session._last_tu_info_at.clear()
    monkeypatch.setattr(Session, "AUTO_GIVE_STATE_FILE", str(tmp_path / "auto_give_state.json"))
    monkeypatch.setattr(
        Session,
        "Vars",
        type(
            "_Vars",
            (),
            {
                "tokens": [
                    {
                        "id": 2,
                        "name": "Loerei",
                        "discordusername": "loereidaparamecium",
                        "discord_user_id": "901139470747832390",
                        "token": "tok-loerei",
                        "max_power": 110,
                    },
                    {
                        "id": 9,
                        "name": "TargetK",
                        "discordusername": "targetk",
                        "discord_user_id": "999",
                        "token": "tok-targetk",
                    },
                    {
                        "id": 10,
                        "name": "TargetS",
                        "discordusername": "targets",
                        "discord_user_id": "1010",
                        "token": "tok-targets",
                    },
                ],
                "Kakera_Give": [[2, 9]],
                "Sphere_Give": [[2, 10]],
                "ROLL_COORDINATION_ENABLED": True,
                "ROLL_LEASE_TTL_SEC": 90.0,
                "ROLL_LEASE_HEARTBEAT_SEC": 10.0,
            },
        ),
    )
    monkeypatch.setattr(Session, "acquire_lease", lambda *_args, **_kwargs: _AcquiredLease())
    monkeypatch.setattr(Session, "_dashboard_set_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_warn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "logRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "logSessionRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_sleep_interruptible", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: ("901139470747832390", "loereidaparamecium"))


def _minute_20_timestamp() -> float:
    return time.mktime(time.strptime("2026-03-27 08:20:05", "%Y-%m-%d %H:%M:%S"))


def test_enhanced_roll_skips_preroll_revalidation_after_fresh_tu(monkeypatch: Any) -> None:
    _stub_roll_common(monkeypatch)
    tu_calls = {"count": 0}
    captured: Dict[str, Any] = {}

    monkeypatch.setattr(
        Session,
        "getTuInfo",
        lambda _token: tu_calls.__setitem__("count", tu_calls["count"] + 1) or {"rolls": 3, "next_reset_min": 20},
    )
    monkeypatch.setattr(
        Session,
        "fetchAndParseMudaeWishlist",
        lambda _token: {"status": "success", "star_wishes": [], "regular_wishes": []},
    )

    def _run_session(**kwargs: Any) -> Dict[str, Any]:
        captured["skip_pre_roll_revalidation"] = kwargs["skip_pre_roll_revalidation"]
        return kwargs["tu_info"]

    monkeypatch.setattr(Session, "_run_enhanced_roll_session", _run_session)

    result = Session.enhancedRoll("tok-loerei")

    assert result == {"rolls": 3, "next_reset_min": 20}
    assert tu_calls["count"] == 1
    assert captured["skip_pre_roll_revalidation"] is True


def test_enhanced_roll_revalidates_when_using_cached_tu(monkeypatch: Any) -> None:
    _stub_roll_common(monkeypatch)
    captured: Dict[str, Any] = {}

    monkeypatch.setattr(
        Session,
        "fetchAndParseMudaeWishlist",
        lambda _token: {"status": "success", "star_wishes": [], "regular_wishes": []},
    )

    def _run_session(**kwargs: Any) -> Dict[str, Any]:
        captured["skip_pre_roll_revalidation"] = kwargs["skip_pre_roll_revalidation"]
        return kwargs["tu_info"]

    monkeypatch.setattr(Session, "_run_enhanced_roll_session", _run_session)
    monkeypatch.setattr(Session, "getTuInfo", lambda _token: (_ for _ in ()).throw(AssertionError("unexpected fresh /tu")))

    result = Session.enhancedRoll("tok-loerei", initial_tu_info={"rolls": 4, "next_reset_min": 15})

    assert result == {"rolls": 4, "next_reset_min": 15}
    assert captured["skip_pre_roll_revalidation"] is False


def test_scheduled_transfers_send_once_per_hour_without_fetching_tu(monkeypatch: Any, tmp_path: Path) -> None:
    _stub_scheduler_common(monkeypatch, tmp_path)
    Session._last_tu_info_cache["tok-loerei"] = {"total_balance": 1500, "sphere_balance": 0}
    sent_commands: list[str] = []

    monkeypatch.setattr(Session, "_acquire_same_account_action_gate", lambda *_args, **_kwargs: (_AcquiredLease(), True))
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (object(), {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "getUrl", lambda: "https://discord.test/api/messages")
    monkeypatch.setattr(Session, "getTuInfo", lambda _token: (_ for _ in ()).throw(AssertionError("scheduler must not fetch /tu")))
    monkeypatch.setattr(
        Session,
        "_send_text_command",
        lambda **kwargs: sent_commands.append(kwargs["content"]) or True,
    )

    now = _minute_20_timestamp()
    Session.maybe_run_scheduled_transfers("tok-loerei", now=now)
    Session.maybe_run_scheduled_transfers("tok-loerei", now=now)

    assert sent_commands == ["$givek <@999> 1500", "y"]
    assert Session._get_cached_tu_info("tok-loerei") == {"total_balance": 0, "sphere_balance": 0}

    payload = json.loads(Path(Session.AUTO_GIVE_STATE_FILE).read_text(encoding="utf-8"))
    bucket = Session._auto_give_hour_bucket(now)
    assert payload["entries"][f"{bucket}|2|kakera"]["status"] == "confirmed"
    assert payload["entries"][f"{bucket}|2|sphere"]["status"] == "no_balance"


def test_scheduled_transfers_busy_skip_is_recorded_and_not_retried(monkeypatch: Any, tmp_path: Path) -> None:
    _stub_scheduler_common(monkeypatch, tmp_path)
    Session._last_tu_info_cache["tok-loerei"] = {"total_balance": 777, "sphere_balance": 0}
    gate_calls = {"count": 0}
    sent_commands: list[str] = []

    def _busy_gate(*_args: Any, **_kwargs: Any) -> tuple[None, bool]:
        gate_calls["count"] += 1
        return (None, False)

    monkeypatch.setattr(Session, "_acquire_same_account_action_gate", _busy_gate)
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (_ for _ in ()).throw(AssertionError("gate busy should skip client init")))
    monkeypatch.setattr(
        Session,
        "_send_text_command",
        lambda **kwargs: sent_commands.append(kwargs["content"]) or True,
    )

    now = _minute_20_timestamp()
    Session.maybe_run_scheduled_transfers("tok-loerei", now=now)
    Session._auto_give_seen_keys.clear()
    Session.maybe_run_scheduled_transfers("tok-loerei", now=now)

    assert gate_calls["count"] == 1
    assert sent_commands == []

    payload = json.loads(Path(Session.AUTO_GIVE_STATE_FILE).read_text(encoding="utf-8"))
    bucket = Session._auto_give_hour_bucket(now)
    assert payload["entries"][f"{bucket}|2|kakera"]["status"] == "busy_skip"


def test_synthesize_tu_after_dk_updates_cached_state(monkeypatch: Any) -> None:
    _stub_roll_common(monkeypatch)
    Session._last_tu_info_cache["tok-loerei"] = {"current_power": 12, "dk_ready": True}
    monkeypatch.setattr(Session, "getMaxPowerForToken", lambda _token: 110)

    updated = Session._synthesize_tu_after_dk("tok-loerei", {"current_power": 12, "dk_ready": True})

    assert updated is not None
    assert updated["current_power"] == 110
    assert updated["max_power"] == 110
    assert updated["dk_ready"] is False
    assert Session._get_cached_tu_info("tok-loerei")["current_power"] == 110


def test_synthesize_tu_after_rt_updates_cached_state(monkeypatch: Any) -> None:
    _stub_roll_common(monkeypatch)
    Session._last_tu_info_cache["tok-loerei"] = {"can_claim_now": False, "rt_available": True}

    updated = Session._synthesize_tu_after_rt("tok-loerei", {"can_claim_now": False, "rt_available": True})

    assert updated is not None
    assert updated["can_claim_now"] is True
    assert updated["rt_available"] is False
    assert Session._get_cached_tu_info("tok-loerei")["can_claim_now"] is True

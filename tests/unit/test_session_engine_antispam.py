from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from mudae.core import session_engine as Session


class _DummyResponse:
    status_code = 200

    def json(self) -> List[Dict[str, Any]]:
        return []


class _BusyLease:
    acquired = False
    waited_sec = 0.0

    def release(self) -> None:
        return None


class _DummyBot:
    def __init__(self) -> None:
        self.trigger_calls = 0

    def triggerSlashCommand(self, *_args: Any, **_kwargs: Any) -> Dict[str, str]:
        self.trigger_calls += 1
        return {"id": "interaction"}


def _make_wl_message(
    *,
    message_id: str,
    interaction_id: str,
    user_id: str,
    username: str,
    global_name: Optional[str],
    author_name: str,
    description: str,
) -> Dict[str, Any]:
    interaction_user = {
        "id": user_id,
        "username": username,
        "global_name": global_name,
    }
    return {
        "id": message_id,
        "content": "",
        "timestamp": "2026-03-26T09:27:40.000000+00:00",
        "author": {"id": str(Session.botID), "username": "Mudae"},
        "interaction": {"id": interaction_id, "name": "wl", "user": interaction_user},
        "interaction_metadata": {"id": interaction_id, "name": "wl", "user": interaction_user},
        "embeds": [
            {
                "author": {"name": author_name},
                "description": description,
            }
        ],
    }


def _stub_common_session_state(monkeypatch: Any) -> None:
    Session.initial_tu_cache.clear()
    Session._last_fetch_reason["tu"].clear()
    Session._last_fetch_reason["wl"].clear()
    Session._last_fetch_reason["report"].clear()
    Session._last_fetch_reason["special"].clear()
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
                "TU_INFO_REUSE_MAX_AGE_SEC": 90.0,
                "WISHLIST_CACHE_TTL_SEC": 300.0,
                "ROLLS_PER_RESET": 14,
                "rollCommand": "wa",
                "DISCORD_API_BASE": "https://discord.test/api",
                "DISCORD_API_VERSION_MESSAGES": "v10",
            },
        ),
    )
    monkeypatch.setattr(Session, "_dashboard_set_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_set_wishlist", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_reset_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_reset_roll_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "render_dashboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "log_warn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "logRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "logSessionRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_note_last_seen_from_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_mark_last_seen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_run_auto_give_from_tu", lambda **_kwargs: None)
    monkeypatch.setattr(Session, "getMaxPowerForToken", lambda _token: 110)
    monkeypatch.setattr(Session, "calculateFixedResetSeconds", lambda: (1800, 5400))
    monkeypatch.setattr(Session.Latency, "get_active_tier", lambda: "aggressive_auto")
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: ("901139470747832390", "loereidaparamecium"))


def test_get_tu_info_uses_fresh_cache_when_action_gate_is_busy(monkeypatch: Any) -> None:
    _stub_common_session_state(monkeypatch)
    dummy_bot = _DummyBot()
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (dummy_bot, {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "getUrl", lambda: "https://discord.test/api/messages")
    monkeypatch.setattr(Session, "_acquire_same_account_action_gate", lambda *_args, **_kwargs: (None, False))
    monkeypatch.setattr(
        Session,
        "_get_cached_tu_info",
        lambda _token, max_age_sec=None: {"rolls": 5, "next_reset_min": 30, "claim_reset_min": 90},
    )

    status = Session.getTuInfo("tok-loerei")

    assert status == {"rolls": 5, "next_reset_min": 30, "claim_reset_min": 90}
    assert dummy_bot.trigger_calls == 0
    assert Session.getLastTuFetchReason("tok-loerei") == "cache_hit"


def test_initialize_session_prefers_fresh_cached_tu(monkeypatch: Any, tmp_path: Any) -> None:
    _stub_common_session_state(monkeypatch)
    monkeypatch.setattr(Session, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(Session, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(Session, "ensure_json_array_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "append_json_array", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_build_session_artifact_path", lambda *_args, **_kwargs: str(tmp_path / "session.json"))
    monkeypatch.setattr(Session, "setSessionRawResponseFile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_load_last_seen_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_get_last_seen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        Session,
        "_get_cached_tu_info",
        lambda _token, max_age_sec=None: {"rolls": 7, "next_reset_min": 15, "claim_reset_min": 60},
    )
    calls = {"count": 0}
    monkeypatch.setattr(Session, "getTuInfo", lambda _token: calls.__setitem__("count", calls["count"] + 1) or None)

    Session.initializeSession("tok-loerei", "Loerei")

    assert calls["count"] == 0
    assert Session.initial_tu_cache["tok-loerei"]["rolls"] == 7


def test_fetch_wishlist_uses_fresh_cache_without_sending_command(monkeypatch: Any) -> None:
    _stub_common_session_state(monkeypatch)
    monkeypatch.setattr(
        Session,
        "_get_cached_wishlist",
        lambda _token, max_age_sec=None: {
            "status": "success",
            "star_wishes": ["Asuna"],
            "regular_wishes": ["Kurisu"],
            "all_wishes": ["Asuna", "Kurisu"],
        },
    )
    monkeypatch.setattr(Session, "getClientAndAuth", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no command send")))

    data = Session.fetchAndParseMudaeWishlist("tok-loerei")

    assert data["status"] == "success"
    assert data["star_wishes"] == ["Asuna"]
    assert data["regular_wishes"] == ["Kurisu"]


def test_fetch_wishlist_ignores_other_user_response_in_shared_channel(monkeypatch: Any) -> None:
    _stub_common_session_state(monkeypatch)
    dummy_bot = _DummyBot()
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (dummy_bot, {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "getUrl", lambda: "https://discord.test/api/messages")
    monkeypatch.setattr(Session, "_get_cached_wishlist", lambda _token, max_age_sec=None: None)
    monkeypatch.setattr(Session, "_acquire_same_account_action_gate", lambda *_args, **_kwargs: (None, True))
    monkeypatch.setattr(Session, "getSlashCommand", lambda *_args, **_kwargs: {"name": "wl"})
    monkeypatch.setattr(Session.Fetch, "fetch_messages", lambda *_args, **_kwargs: (_DummyResponse(), []))

    other_message = _make_wl_message(
        message_id="501",
        interaction_id="other-wl",
        user_id="1440660675314585732",
        username="otheruser",
        global_name="Other",
        author_name="Other Wishlist (3 / 10 $wl, 1 / 3 $sw)",
        description="⭐ Wrong Star\nWrong Regular",
    )
    our_message = _make_wl_message(
        message_id="502",
        interaction_id="our-wl",
        user_id="901139470747832390",
        username="loereidaparamecium",
        global_name="Loerei",
        author_name="Loerei Wishlist (2 / 10 $wl, 1 / 3 $sw)",
        description="⭐ Asuna\nKurisu",
    )
    monkeypatch.setattr(
        Session.Fetch,
        "wait_for_interaction_message",
        lambda *_args, **_kwargs: (_DummyResponse(), [other_message, our_message], None),
    )

    data = Session.fetchAndParseMudaeWishlist("tok-loerei")

    assert dummy_bot.trigger_calls == 1
    assert data["status"] == "success"
    assert data["star_wishes"] == ["Asuna"]
    assert data["regular_wishes"] == ["Kurisu"]


def test_enhanced_roll_lease_busy_skips_preflight_commands(monkeypatch: Any) -> None:
    _stub_common_session_state(monkeypatch)
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (object(), {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "_get_cached_tu_info", lambda _token, max_age_sec=None: {"rolls": 3, "next_reset_min": 20})
    monkeypatch.setattr(Session, "acquire_lease", lambda *_args, **_kwargs: _BusyLease())

    calls = {"tu": 0, "wl": 0}
    monkeypatch.setattr(
        Session,
        "getTuInfo",
        lambda _token: calls.__setitem__("tu", calls["tu"] + 1) or {"rolls": 1},
    )
    monkeypatch.setattr(
        Session,
        "fetchAndParseMudaeWishlist",
        lambda _token: calls.__setitem__("wl", calls["wl"] + 1) or {"status": "success", "star_wishes": [], "regular_wishes": []},
    )

    result = Session.enhancedRoll("tok-loerei")

    assert result == {"rolls": 3, "next_reset_min": 20}
    assert calls == {"tu": 0, "wl": 0}
    assert Session.getLastTuFetchReason("tok-loerei") == "cache_hit"

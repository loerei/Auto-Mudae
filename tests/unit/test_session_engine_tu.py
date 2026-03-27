from typing import Any, Dict, List, Optional, Tuple

from mudae.core import session_engine as Session


TU_CONTENT = """**loereidaparamecium**, you __can__ claim right now! The next claim reset is in **1h 29** min.
You have **14** rolls left. Next rolls reset in **29** min.

Next $daily reset in **16h 54** min.

(Keys LVL 6+) **4,500**<:kakera:469835869059153940>to collect before the next reset (**1h 29** min.)
Probability to complete + reset $bku on your next $sw: **10%**

You __can__ react to kakera right now!
Power: **100%**
Each kakera button consumes 36% of your reaction power.
Your characters with 10+ keys consume half the power (18%)

Stock: **0**<:kakera:469835869059153940>

$rt is available!

$dk is ready!

**0** $oh left for today, **0** $oc, **0** $oq and **0** $ot.
**20h 34** min before the refill.
Stock: **0** <:sp:1437140700604137554>"""


class _DummyResponse:
    status_code = 200

    def json(self) -> List[Dict[str, Any]]:
        return []


class _DummyBot:
    def triggerSlashCommand(self, *_args: Any, **_kwargs: Any) -> Dict[str, str]:
        return {"id": "expected-tu-interaction"}


def _make_tu_message(
    *,
    message_id: str,
    interaction_id: str,
    user_id: str,
    username: str,
    global_name: Optional[str] = None,
    content: str = TU_CONTENT,
) -> Dict[str, Any]:
    return {
        "id": message_id,
        "content": content,
        "timestamp": "2026-03-26T09:27:40.000000+00:00",
        "author": {"id": str(Session.botID), "username": "Mudae"},
        "interaction": {
            "id": interaction_id,
            "name": "tu",
            "user": {
                "id": user_id,
                "username": username,
                "global_name": global_name,
            },
        },
        "interaction_metadata": {
            "id": interaction_id,
            "name": "tu",
            "user": {
                "id": user_id,
                "username": username,
                "global_name": global_name,
            },
        },
    }


def _stub_get_tu_info_dependencies(monkeypatch: Any, *, wait_result: Tuple[Any, List[Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
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
            },
        ),
    )
    monkeypatch.setattr(Session, "getClientAndAuth", lambda _token: (_DummyBot(), {"authorization": "tok-loerei"}))
    monkeypatch.setattr(Session, "getUrl", lambda: "https://discord.test/api/messages")
    monkeypatch.setattr(Session, "getSlashCommand", lambda *_args, **_kwargs: {"name": "tu"})
    monkeypatch.setattr(Session, "logRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "logSessionRawResponse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_note_last_seen_from_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_mark_last_seen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_dashboard_set_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "render_dashboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_cache_tu_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Session, "_run_auto_give_from_tu", lambda **_kwargs: None)
    monkeypatch.setattr(Session, "calculateFixedResetSeconds", lambda: (1800, 5400))
    monkeypatch.setattr(Session, "getMaxPowerForToken", lambda _token: 110)
    monkeypatch.setattr(Session.Latency, "get_active_tier", lambda: "aggressive_auto")
    monkeypatch.setattr(Session.Fetch, "fetch_messages", lambda *_args, **_kwargs: (_DummyResponse(), []))
    monkeypatch.setattr(Session.Fetch, "wait_for_interaction_message", lambda *_args, **_kwargs: wait_result)
    monkeypatch.setattr(Session, "current_user_name", None)


def test_get_tu_info_uses_user_command_match_when_interaction_id_misses(monkeypatch: Any) -> None:
    other_message = _make_tu_message(
        message_id="100",
        interaction_id="other-interaction",
        user_id="1440660675314585732",
        username="tehuongnoihahahahaha",
        content=TU_CONTENT.replace("loereidaparamecium", "tehuongnoihahahahaha"),
    )
    our_message = _make_tu_message(
        message_id="101",
        interaction_id="wrong-interaction",
        user_id="901139470747832390",
        username="loereidaparamecium",
        global_name="Loerei",
    )

    _stub_get_tu_info_dependencies(monkeypatch, wait_result=(_DummyResponse(), [other_message, our_message], None))
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: ("901139470747832390", "loereidaparamecium"))

    status = Session.getTuInfo("tok-loerei")

    assert status is not None
    assert status["max_power"] == 110
    assert status["next_reset_min"] == 30


def test_get_tu_info_matches_configured_discord_username_when_resolved_name_is_display_name(monkeypatch: Any) -> None:
    our_message = _make_tu_message(
        message_id="201",
        interaction_id="mismatched-interaction",
        user_id="901139470747832390",
        username="loereidaparamecium",
        global_name="Loerei",
    )

    _stub_get_tu_info_dependencies(monkeypatch, wait_result=(_DummyResponse(), [our_message], None))
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: (None, "Loerei"))

    status = Session.getTuInfo("tok-loerei")

    assert status is not None
    assert status["max_power"] == 110
    assert status["next_reset_min"] == 30


def test_get_tu_info_rejects_single_tu_message_for_other_user(monkeypatch: Any) -> None:
    other_message = _make_tu_message(
        message_id="301",
        interaction_id="other-interaction",
        user_id="1440660675314585732",
        username="tehuongnoihahahahaha",
        content=TU_CONTENT.replace("loereidaparamecium", "tehuongnoihahahahaha"),
    )

    _stub_get_tu_info_dependencies(monkeypatch, wait_result=(_DummyResponse(), [other_message], None))
    monkeypatch.setattr(Session, "_ensure_user_identity", lambda _token: ("901139470747832390", "loereidaparamecium"))

    status = Session.getTuInfo("tok-loerei")

    assert status is None

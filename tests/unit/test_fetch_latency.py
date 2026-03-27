from typing import Any, Dict, List, Optional, Tuple

from mudae.discord import fetch as Fetch


class _DummyResponse:
    def __init__(
        self,
        status_code: int,
        *,
        payload: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self) -> Any:
        return self._payload


def test_wait_for_interaction_uses_schedule_length(monkeypatch: Any) -> None:
    calls = {"count": 0}
    sleeps: List[float] = []

    def _fake_fetch_messages(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], List[Dict[str, Any]]]:
        calls["count"] += 1
        if calls["count"] < 3:
            return _DummyResponse(200, payload=[]), []
        return (
            _DummyResponse(200, payload=[]),
            [
                {
                    "id": "10",
                    "interaction": {"user": {"id": "u1"}, "name": "wl"},
                }
            ],
        )

    monkeypatch.setattr(Fetch, "fetch_messages", _fake_fetch_messages)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda delay_sec, _stop: sleeps.append(float(delay_sec)) or False)

    _, _, msg = Fetch.wait_for_interaction_message(
        "url",
        {"authorization": "token"},
        user_id="u1",
        command_name="wl",
        attempts=1,
        delay_sec=1.0,
        delay_schedule=[0.0, 0.0, 0.0],
        latency_context="unit_interaction",
    )
    assert msg is not None
    assert calls["count"] == 3
    assert len(sleeps) == 2


def test_wait_for_author_retry_after_precedence(monkeypatch: Any) -> None:
    calls = {"count": 0}
    sleeps: List[float] = []

    def _fake_fetch_messages(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], List[Dict[str, Any]]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return _DummyResponse(429, payload={"retry_after": 2.0}, headers={"Retry-After": "2"}), []
        return _DummyResponse(200, payload=[]), [{"author": {"id": "bot"}, "content": "ok"}]

    monkeypatch.setattr(Fetch, "fetch_messages", _fake_fetch_messages)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda delay_sec, _stop: sleeps.append(float(delay_sec)) or False)

    _, _, msg = Fetch.wait_for_author_message(
        "url",
        {"authorization": "token"},
        author_id="bot",
        attempts=2,
        delay_sec=0.1,
        delay_schedule=[0.0, 0.0],
        latency_context="unit_author",
    )
    assert msg is not None
    assert sleeps
    assert sleeps[0] >= 2.0


def test_wait_for_message_update_schedule(monkeypatch: Any) -> None:
    calls = {"count": 0}
    sleeps: List[float] = []
    base_msg = {"id": "1", "content": "a", "components": []}
    changed_msg = {"id": "1", "content": "b", "components": []}

    def _fake_fetch_message_by_id(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], Optional[Dict[str, Any]]]:
        calls["count"] += 1
        if calls["count"] < 3:
            return _DummyResponse(200, payload=base_msg), dict(base_msg)
        return _DummyResponse(200, payload=changed_msg), dict(changed_msg)

    monkeypatch.setattr(Fetch, "fetch_message_by_id", _fake_fetch_message_by_id)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda delay_sec, _stop: sleeps.append(float(delay_sec)) or False)

    prev_hash = Fetch._message_hash(base_msg)
    _, message, _ = Fetch.wait_for_message_update(
        "base",
        {"authorization": "token"},
        "channel",
        "1",
        prev_hash=prev_hash,
        attempts=1,
        delay_sec=1.0,
        delay_schedule=[0.0, 0.0, 0.0],
        latency_context="unit_update",
    )
    assert message is not None
    assert message.get("content") == "b"
    assert calls["count"] == 3
    assert len(sleeps) == 2


def test_wait_for_interaction_preserves_earlier_soft_match(monkeypatch: Any) -> None:
    calls = {"count": 0}
    first_response = _DummyResponse(200, payload=[])
    last_response = _DummyResponse(200, payload=[])
    target_message = {
        "id": "10",
        "interaction": {"id": "wrong", "user": {"id": "u1", "username": "loerei"}, "name": "tu"},
    }
    noisy_message = {
        "id": "11",
        "interaction": {"id": "other", "user": {"id": "u2", "username": "other"}, "name": "tu"},
    }

    def _fake_fetch_messages(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], List[Dict[str, Any]]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return first_response, [noisy_message, target_message]
        return last_response, []

    monkeypatch.setattr(Fetch, "fetch_messages", _fake_fetch_messages)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda *_args, **_kwargs: False)

    response, messages, msg = Fetch.wait_for_interaction_message(
        "url",
        {"authorization": "token"},
        after_id="9",
        user_id="u1",
        command_name="tu",
        interaction_id="expected",
        attempts=2,
        delay_sec=0.0,
        delay_schedule=[0.0, 0.0],
    )

    assert response is first_response
    assert messages == [noisy_message, target_message]
    assert msg == target_message


def test_wait_for_interaction_ignores_other_user_until_our_response_arrives(monkeypatch: Any) -> None:
    calls = {"count": 0}
    other_message = {
        "id": "20",
        "interaction": {"id": "i-other", "user": {"id": "u2", "username": "other"}, "name": "tu"},
    }
    our_message = {
        "id": "21",
        "interaction": {"id": "i-ours", "user": {"id": "u1", "username": "loerei"}, "name": "tu"},
    }

    def _fake_fetch_messages(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], List[Dict[str, Any]]]:
        calls["count"] += 1
        if calls["count"] == 1:
            return _DummyResponse(200, payload=[]), [other_message]
        return _DummyResponse(200, payload=[]), [our_message]

    monkeypatch.setattr(Fetch, "fetch_messages", _fake_fetch_messages)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda *_args, **_kwargs: False)

    response, messages, msg = Fetch.wait_for_interaction_message(
        "url",
        {"authorization": "token"},
        user_id="u1",
        command_name="tu",
        interaction_id="i-ours",
        attempts=2,
        delay_sec=0.0,
        delay_schedule=[0.0, 0.0],
    )

    assert response is not None
    assert messages == [our_message]
    assert msg == our_message


def test_wait_for_interaction_returns_relevant_batch_not_final_empty_poll(monkeypatch: Any) -> None:
    target_message = {
        "id": "31",
        "interaction": {"id": "stale", "user": {"id": "u1", "username": "loerei"}, "name": "tu"},
    }
    unrelated_message = {
        "id": "32",
        "interaction": {"id": "other", "user": {"id": "u9", "username": "noise"}, "name": "wl"},
    }
    first_response = _DummyResponse(200, payload=[])

    def _fake_fetch_messages(*_args: Any, **_kwargs: Any) -> Tuple[Optional[_DummyResponse], List[Dict[str, Any]]]:
        sequence = getattr(_fake_fetch_messages, "sequence", 0)
        setattr(_fake_fetch_messages, "sequence", sequence + 1)
        if sequence == 0:
            return first_response, [unrelated_message, target_message]
        return _DummyResponse(200, payload=[]), []

    monkeypatch.setattr(Fetch, "fetch_messages", _fake_fetch_messages)
    monkeypatch.setattr(Fetch, "_sleep_with_stop", lambda *_args, **_kwargs: False)

    response, messages, msg = Fetch.wait_for_interaction_message(
        "url",
        {"authorization": "token"},
        user_id="u1",
        command_name="tu",
        interaction_id="expected",
        attempts=2,
        delay_sec=0.0,
        delay_schedule=[0.0, 0.0],
    )

    assert response is first_response
    assert messages == [unrelated_message, target_message]
    assert msg == target_message


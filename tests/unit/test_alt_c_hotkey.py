from mudae.cli import bot as Bot


def _reset_hotkey_state() -> None:
    Bot.stop_requested = False
    Bot.alt_pressed = False
    Bot._alt_c_last_trigger_ts = 0.0
    Bot._alt_c_ignore_input_until = 0.0


def test_alt_c_requires_focused_window(monkeypatch) -> None:
    _reset_hotkey_state()
    events = []

    monkeypatch.setattr(Bot, "_is_alt_key", lambda key: key == "alt")
    monkeypatch.setattr(Bot, "_is_c_key", lambda key: key == "c")
    monkeypatch.setattr(Bot, "_is_window_focused", lambda: False)
    monkeypatch.setattr(Bot, "_flush_console_input_buffer", lambda: events.append("flush"))
    monkeypatch.setattr(Bot, "setStopRequested", lambda value: events.append(("set", value)))
    monkeypatch.setattr(Bot, "log_info", lambda *_: None)
    monkeypatch.setattr(Bot.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(Bot.Vars, "ALT_C_DEBOUNCE_MS", 250, raising=False)
    monkeypatch.setattr(Bot.Vars, "ALT_C_INPUT_GUARD_MS", 600, raising=False)

    Bot.on_press("alt")
    Bot.on_press("c")

    assert Bot.stop_requested is False
    assert events == []


def test_alt_c_debounce_blocks_double_trigger(monkeypatch) -> None:
    _reset_hotkey_state()
    events = []
    t_values = iter([1.0, 1.0, 1.1])

    monkeypatch.setattr(Bot, "_is_alt_key", lambda key: key == "alt")
    monkeypatch.setattr(Bot, "_is_c_key", lambda key: key == "c")
    monkeypatch.setattr(Bot, "_is_window_focused", lambda: True)
    monkeypatch.setattr(Bot, "_flush_console_input_buffer", lambda: events.append("flush"))
    monkeypatch.setattr(Bot, "setStopRequested", lambda value: events.append(("set", value)))
    monkeypatch.setattr(Bot, "log_info", lambda *_: None)
    monkeypatch.setattr(Bot.time, "monotonic", lambda: next(t_values))
    monkeypatch.setattr(Bot.Vars, "ALT_C_DEBOUNCE_MS", 250, raising=False)
    monkeypatch.setattr(Bot.Vars, "ALT_C_INPUT_GUARD_MS", 600, raising=False)

    Bot.on_press("alt")
    Bot.on_press("c")
    Bot.stop_requested = False
    Bot.on_press("alt")
    Bot.on_press("c")

    assert events.count("flush") == 1
    assert events.count(("set", True)) == 1


def test_sanitize_user_choice_ignores_stray_c_during_guard(monkeypatch) -> None:
    _reset_hotkey_state()
    monkeypatch.setattr(Bot, "_WINDOWS", True)
    Bot._alt_c_ignore_input_until = 10.0
    monkeypatch.setattr(Bot.time, "monotonic", lambda: 9.0)

    assert Bot._sanitize_user_choice("c") == ""
    assert Bot._sanitize_user_choice("c2") == "2"
    assert Bot._sanitize_user_choice("2") == "2"


def test_sanitize_user_choice_keeps_input_after_guard(monkeypatch) -> None:
    _reset_hotkey_state()
    monkeypatch.setattr(Bot, "_WINDOWS", True)
    Bot._alt_c_ignore_input_until = 10.0
    monkeypatch.setattr(Bot.time, "monotonic", lambda: 11.0)

    assert Bot._sanitize_user_choice("c2") == "c2"

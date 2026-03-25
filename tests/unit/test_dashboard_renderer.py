from mudae.core import session_engine as Session


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        return len(text)

    def flush(self) -> None:
        return

    def isatty(self) -> bool:
        return True


def test_ansi_full_renderer_uses_full_clear_each_frame(monkeypatch) -> None:
    fake = _FakeStdout()
    monkeypatch.setattr(Session.sys, "stdout", fake)

    assert Session._render_dashboard_ansi_full(["line 1", "line 2"]) is True
    first = fake.buffer
    fake.buffer = ""
    assert Session._render_dashboard_ansi_full(["line 1", "line 2"]) is True
    second = fake.buffer

    assert first.startswith("\x1b[H\x1b[2J")
    assert second.startswith("\x1b[H\x1b[2J")
    assert "\x1b[2Kline 1" in first
    assert "\x1b[2Kline 2" in first


def test_dashboard_width_applies_safety_cols(monkeypatch) -> None:
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: (100, 30))
    monkeypatch.setattr(Session.Vars, "DASHBOARD_AUTO_FIT", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MIN_WIDTH", 60, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MAX_WIDTH", 120, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_NO_SCROLL", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDER_SAFETY_COLS", 2, raising=False)

    assert Session._dashboard_width() == 98


def test_dashboard_visible_len_counts_emoji_as_wide(monkeypatch) -> None:
    monkeypatch.setattr(Session.Vars, "DASHBOARD_WIDECHAR_AWARE", True, raising=False)
    assert Session._dashboard_visible_len("A✅B") == 4


def test_dashboard_fit_height_honors_safety_rows(monkeypatch) -> None:
    monkeypatch.setattr(Session, "_dashboard_terminal_rows", lambda default=30: 20)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_NO_SCROLL", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDER_SAFETY_ROWS", 2, raising=False)
    monkeypatch.setenv("MUDAE_DASHBOARD_RESERVED_ROWS", "3")
    monkeypatch.setenv("MUDAE_DASHBOARD_FIT_HEIGHT", "1")

    lines = [f"line {i}" for i in range(40)]
    fitted = Session._dashboard_fit_height(lines, 70)
    assert len(fitted) <= 15


def test_dashboard_fit_height_keeps_noscroll_even_when_fit_disabled(monkeypatch) -> None:
    monkeypatch.setattr(Session, "_dashboard_terminal_rows", lambda default=30: 20)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_NO_SCROLL", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDER_SAFETY_ROWS", 2, raising=False)
    monkeypatch.setenv("MUDAE_DASHBOARD_RESERVED_ROWS", "3")
    monkeypatch.setenv("MUDAE_DASHBOARD_FIT_HEIGHT", "0")

    lines = [f"line {i}" for i in range(40)]
    fitted = Session._dashboard_fit_height(lines, 70)
    assert len(fitted) <= 15

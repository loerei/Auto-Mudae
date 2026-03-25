import os

from mudae.core import session_engine as Session


def _mock_terminal_width(monkeypatch, columns: int, lines: int = 30) -> None:
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: None)
    monkeypatch.setattr(
        Session.os,
        "get_terminal_size",
        lambda: os.terminal_size((columns, lines)),
    )
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDER_SAFETY_COLS", 0, raising=False)


def test_dashboard_width_auto_fit_respects_narrow_window(monkeypatch) -> None:
    _mock_terminal_width(monkeypatch, 44)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_AUTO_FIT", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MIN_WIDTH", 60, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MAX_WIDTH", 120, raising=False)

    assert Session._dashboard_width() == 44


def test_dashboard_width_auto_fit_honors_max_width(monkeypatch) -> None:
    _mock_terminal_width(monkeypatch, 180)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_AUTO_FIT", True, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MIN_WIDTH", 60, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MAX_WIDTH", 120, raising=False)

    assert Session._dashboard_width() == 120


def test_dashboard_width_legacy_mode_keeps_min_width(monkeypatch) -> None:
    _mock_terminal_width(monkeypatch, 44)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_AUTO_FIT", False, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MIN_WIDTH", 60, raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_MAX_WIDTH", 120, raising=False)

    assert Session._dashboard_width() == 60


def test_dashboard_fit_height_trims_without_scroll(monkeypatch) -> None:
    monkeypatch.setenv("MUDAE_DASHBOARD_FIT_HEIGHT", "1")
    monkeypatch.setenv("MUDAE_DASHBOARD_RESERVED_ROWS", "1")
    monkeypatch.setattr(Session, "_dashboard_terminal_rows", lambda default=30: 12)

    width = 70
    lines = [f"line {i}" for i in range(25)]
    fitted = Session._dashboard_fit_height(lines, width)

    assert len(fitted) <= 11
    assert any("hidden" in line.lower() for line in fitted)


def test_dashboard_terminal_rows_minimum(monkeypatch) -> None:
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: None)
    monkeypatch.setattr(
        Session.os,
        "get_terminal_size",
        lambda: os.terminal_size((100, 3)),
    )
    assert Session._dashboard_terminal_rows() == 8


def test_dashboard_terminal_rows_prefers_viewport_size(monkeypatch) -> None:
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDERER_MODE", "auto", raising=False)
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: (120, 19))
    assert Session._dashboard_terminal_rows() == 19


def test_dashboard_fit_height_budget_override(monkeypatch) -> None:
    monkeypatch.setenv("MUDAE_DASHBOARD_FIT_HEIGHT", "1")
    lines = [f"line {i}" for i in range(25)]
    fitted = Session._dashboard_fit_height(lines, 70, budget_rows=10)
    assert len(fitted) <= 10


def test_dashboard_mark_layout_dirty_can_preserve_line_count() -> None:
    Session._dashboard_state["last_render_lines"] = 17
    Session._dashboard_state["last_render_width"] = 80
    Session._dashboard_state["anchor_pos"] = (0, 10)
    Session._dashboard_mark_layout_dirty(reset_line_count=False)

    assert Session._dashboard_state["last_render_lines"] == 17
    assert Session._dashboard_state["last_render_width"] == 0
    assert Session._dashboard_state["anchor_pos"] is None


def test_dashboard_terminal_rows_uses_os_size_in_windows_terminal(monkeypatch) -> None:
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDERER_MODE", "auto", raising=False)
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: (120, 10))
    monkeypatch.setattr(
        Session.os,
        "get_terminal_size",
        lambda *args: os.terminal_size((140, 33)),
    )
    assert Session._dashboard_terminal_rows() == 33


def test_dashboard_terminal_rows_explicit_win32_mode_prefers_viewport(monkeypatch) -> None:
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.setattr(Session.Vars, "DASHBOARD_RENDERER_MODE", "win32", raising=False)
    monkeypatch.setattr(Session, "_dashboard_console_viewport_size", lambda: (120, 17))
    monkeypatch.setattr(
        Session.os,
        "get_terminal_size",
        lambda *args: os.terminal_size((140, 33)),
    )
    assert Session._dashboard_terminal_rows() == 17

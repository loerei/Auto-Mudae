from mudae.core import session_engine as Session


def test_dashboard_sanitize_text_replaces_standard_symbols() -> None:
    text = "✅ ok | ❌ fail | ⚠️ warn | ⭐ star"
    result = Session._dashboard_sanitize_text(text)
    assert "OK" in result
    assert "X" in result
    assert "!" in result
    assert "*" in result

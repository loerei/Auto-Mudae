from mudae.core import session_engine as Session


def _mojibake(symbol: str) -> str:
    return symbol.encode("utf-8").decode("latin1")


def test_normalize_wishlist_text_repairs_mojibake(monkeypatch) -> None:
    monkeypatch.setattr(Session.Vars, "WISHLIST_NORMALIZE_TEXT", True, raising=False)
    source = f"**Rem** {_mojibake('✅')} {_mojibake('⭐')}"
    normalized = Session._normalize_wishlist_text(source)

    assert "✅" in normalized
    assert "⭐" in normalized


def test_parse_wishlist_line_handles_mojibake_markers(monkeypatch) -> None:
    monkeypatch.setattr(Session.Vars, "WISHLIST_NORMALIZE_TEXT", True, raising=False)
    line = f"**Rem** {_mojibake('✅')} {_mojibake('⭐')}"
    name, has_star, is_claimed, is_failed = Session._parse_wishlist_line(line)

    assert name == "Rem"
    assert has_star is True
    assert is_claimed is True
    assert is_failed is False


from mudae.core import session_engine as Session


def test_parse_wishlist_line_detects_markers() -> None:
    name, has_star, is_claimed, is_failed = Session._parse_wishlist_line("**Rem** ✅ ⭐")
    assert name == "Rem"
    assert has_star is True
    assert is_claimed is True
    assert is_failed is False


def test_parse_wishlist_line_regular_unclaimed() -> None:
    name, has_star, is_claimed, is_failed = Session._parse_wishlist_line("**Kyouko Hori** +60%")
    assert name == "Kyouko Hori"
    assert has_star is False
    assert is_claimed is False
    assert is_failed is False


def test_parse_wishlist_line_failed_star() -> None:
    name, has_star, is_claimed, is_failed = Session._parse_wishlist_line("**Takina Inoue** ❌ ⭐")
    assert name == "Takina Inoue"
    assert has_star is True
    assert is_claimed is False
    assert is_failed is True


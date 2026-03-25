from mudae.core import session_engine as Session


def test_wishlist_priority_parity_star_regular() -> None:
    match_star = Session.matchesWishlist(
        "Yui Hirasawa",
        "K-ON!",
        mudae_star_wishes=["Yui Hirasawa"],
        mudae_regular_wishes=[],
    )
    assert match_star == (True, 3)

    match_regular = Session.matchesWishlist(
        "Any Name",
        "Arknights: Endfield",
        mudae_star_wishes=[],
        mudae_regular_wishes=["Arknights: Endfield"],
    )
    assert match_regular == (True, 2)

    no_match = Session.matchesWishlist(
        "Unknown",
        "Unknown Series",
        mudae_star_wishes=[],
        mudae_regular_wishes=[],
    )
    assert no_match == (False, 1)


def test_claim_response_parser_parity() -> None:
    claim_resp = [
        {"content": "**User** and **Card** are now married! **+444**"},
    ]
    success, kakera = Session._parse_claim_response(claim_resp, 100)
    assert success is True
    assert kakera == 444

    failed_resp = [{"content": "random message"}]
    success2, kakera2 = Session._parse_claim_response(failed_resp, 100)
    assert success2 is False
    assert kakera2 == 100


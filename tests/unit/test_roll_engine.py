from typing import Optional, Dict, Any, Tuple
from mudae.core.roll_engine import RollEngine, RollDependencies


def test_roll_engine_wish_matching():
    engine = RollEngine()
    assert engine.wish_matches("Rem", "Rem") is True
    assert engine.wish_matches("Rem (Re:Zero)", "rem") is True
    assert engine.wish_matches("Emilia", "Rem") is False


def test_roll_engine_evaluate_candidate():
    engine = RollEngine()
    star_wishes = ["Rem"]
    regular_wishes = ["Asuka"]

    # Star wish match
    res1 = engine.evaluate_candidate("Rem", "Re:Zero", 100, star_wishes, regular_wishes)
    assert res1.should_claim is True
    assert res1.priority == 3
    assert "Star wish" in res1.reason

    # Regular wish match
    res2 = engine.evaluate_candidate("Asuka Langley", "Evangelion", 100, star_wishes, regular_wishes)
    assert res2.should_claim is True
    assert res2.priority == 2

    # High kakera fallback match
    res3 = engine.evaluate_candidate("Random Character", "Unknown Anime", 300, star_wishes, regular_wishes, min_kakera_claim=150)
    assert res3.should_claim is True
    assert res3.priority == 1

    # No match
    res4 = engine.evaluate_candidate("Random Character", "Unknown Anime", 50, star_wishes, regular_wishes, min_kakera_claim=150)
    assert res4.should_claim is False


def test_roll_engine_message_exhausted():
    engine = RollEngine()
    assert engine.message_indicates_roll_exhausted("The roulette is limited!") is True
    assert engine.message_indicates_roll_exhausted("You don't have any rolls left") is True
    assert engine.message_indicates_roll_exhausted("Here is your roll!") is False


def test_roll_engine_reconcile_target():
    engine = RollEngine()

    # Refreshed rolls remaining
    new_target, rolls_left, exhausted = engine.reconcile_roll_target(10, 5, {"rolls": 3})
    assert new_target == 13
    assert rolls_left == 3
    assert exhausted is False

    # Refreshed 0 rolls remaining
    new_target2, rolls_left2, exhausted2 = engine.reconcile_roll_target(10, 5, {"rolls": 0})
    assert rolls_left2 == 0
    assert exhausted2 is True


def test_roll_engine_run_session_with_mock_dependencies():
    engine = RollEngine()

    def mock_client(token: str) -> Tuple[Any, Dict[str, str]]:
        return None, {"authorization": token}

    def mock_url() -> str:
        return "https://discord.com/api/v9/channels/123/messages"

    def mock_identity(token: str) -> Tuple[Optional[str], Optional[str]]:
        return "user_123", "TestUser"

    def mock_tu(token: str) -> Optional[Dict[str, Any]]:
        return {"rolls": 10, "usables": 10}

    def mock_wishlist(token: str) -> Dict[str, Any]:
        return {"star_wishes": ["Rem"], "regular_wishes": ["Asuka"]}

    class MockLease:
        acquired = True

    def mock_lease(*args, **kwargs):
        return MockLease()

    deps = RollDependencies(
        client_provider=mock_client,
        url_provider=mock_url,
        identity_provider=mock_identity,
        tu_provider=mock_tu,
        wishlist_provider=mock_wishlist,
        lease_provider=mock_lease,
    )

    res = engine.run_session("test_token_123", deps=deps)
    assert res is not None
    assert res.get("rolls") == 10

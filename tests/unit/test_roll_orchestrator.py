import pytest
from mudae.core.roll_orchestrator import RollOrchestrator, ClaimDecision

def test_roll_orchestrator_star_wish_match():
    orchestrator = RollOrchestrator()
    decision = orchestrator.evaluate_candidate(
        card_name="Rem",
        card_series="Re:Zero",
        kakera_value=500,
        star_wishes=["Rem"],
        regular_wishes=[]
    )
    assert decision.should_claim is True
    assert decision.priority == 3
    assert "star wish" in decision.reason.lower()

def test_roll_orchestrator_regular_wish_match():
    orchestrator = RollOrchestrator()
    decision = orchestrator.evaluate_candidate(
        card_name="Emilia",
        card_series="Re:Zero",
        kakera_value=200,
        star_wishes=[],
        regular_wishes=["Emilia"]
    )
    assert decision.should_claim is True
    assert decision.priority == 2

def test_roll_orchestrator_no_match():
    orchestrator = RollOrchestrator()
    decision = orchestrator.evaluate_candidate(
        card_name="Random Character",
        card_series="Unknown",
        kakera_value=50,
        star_wishes=["Rem"],
        regular_wishes=["Emilia"]
    )
    assert decision.should_claim is False
    assert decision.priority == 1

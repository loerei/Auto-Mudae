import pytest
from mudae.core.command_gate import CommandAntiSpamGate

def test_command_gate_allows_initial():
    gate = CommandAntiSpamGate(min_delay_sec=1.5)
    assert gate.can_execute("user1", now=100.0) is True

def test_command_gate_blocks_rapid_succession():
    gate = CommandAntiSpamGate(min_delay_sec=1.5)
    gate.record_execution("user1", now=100.0)
    
    # Executing 0.5s later should be blocked
    assert gate.can_execute("user1", now=100.5) is False
    assert gate.time_until_next("user1", now=100.5) == pytest.approx(1.0)

    # Executing 1.6s later should be allowed
    assert gate.can_execute("user1", now=101.6) is True
    assert gate.time_until_next("user1", now=101.6) == 0.0

def test_command_gate_per_account_isolation():
    gate = CommandAntiSpamGate(min_delay_sec=1.5)
    gate.record_execution("user1", now=100.0)
    
    # user2 should still be allowed immediately
    assert gate.can_execute("user2", now=100.1) is True

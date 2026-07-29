import pytest
from mudae.core.transfer_scheduler import TransferScheduler

def test_transfer_scheduler_tu_cache_validity():
    scheduler = TransferScheduler(cache_ttl_sec=90.0)
    tu_data = {"dk_claimed": False, "rolls_left": 14}
    
    scheduler.update_tu_info("acc1", tu_data, now=100.0)
    
    # Valid within TTL
    assert scheduler.get_valid_tu_info("acc1", now=150.0) == tu_data
    
    # Expired after TTL
    assert scheduler.get_valid_tu_info("acc1", now=200.0) is None

def test_transfer_scheduler_auto_give_keys():
    scheduler = TransferScheduler()
    assert scheduler.is_auto_give_seen("acc1", "2026-03-27:14") is False
    
    scheduler.mark_auto_give_key("acc1", "2026-03-27:14")
    assert scheduler.is_auto_give_seen("acc1", "2026-03-27:14") is True
    assert scheduler.is_auto_give_seen("acc1", "2026-03-27:15") is False

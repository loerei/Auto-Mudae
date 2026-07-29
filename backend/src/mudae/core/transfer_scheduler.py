import time
from typing import Dict, Any, Optional

class TransferScheduler:
    """
    Manages /tu response info caching, hourly auto-give keys, and reset schedule tracking.
    """
    def __init__(self, cache_ttl_sec: float = 90.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self.tu_cache: Dict[str, Dict[str, Any]] = {}
        self.tu_updated_at: Dict[str, float] = {}
        self.auto_give_seen_keys: set = set()

    def update_tu_info(self, account_id: str, tu_info: Dict[str, Any], now: Optional[float] = None) -> None:
        now_ts = now if now is not None else time.time()
        self.tu_cache[account_id] = tu_info
        self.tu_updated_at[account_id] = now_ts

    def get_valid_tu_info(self, account_id: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        now_ts = now if now is not None else time.time()
        last_updated = self.tu_updated_at.get(account_id, 0.0)
        if (now_ts - last_updated) <= self.cache_ttl_sec:
            return self.tu_cache.get(account_id)
        return None

    def mark_auto_give_key(self, account_id: str, hour_key: str) -> None:
        self.auto_give_seen_keys.add(f"{account_id}:{hour_key}")

    def is_auto_give_seen(self, account_id: str, hour_key: str) -> bool:
        return f"{account_id}:{hour_key}" in self.auto_give_seen_keys

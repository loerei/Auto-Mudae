import time
from typing import Dict, Optional

class CommandAntiSpamGate:
    """
    Manages rate limits, min delays, and anti-spam gating for Discord account operations.
    """
    def __init__(self, min_delay_sec: float = 1.0) -> None:
        self.min_delay_sec = min_delay_sec
        self._last_command_time: Dict[str, float] = {}

    def can_execute(self, account_id: str, now: Optional[float] = None) -> bool:
        now_ts = now if now is not None else time.time()
        last_ts = self._last_command_time.get(account_id, 0.0)
        return (now_ts - last_ts) >= self.min_delay_sec

    def record_execution(self, account_id: str, now: Optional[float] = None) -> None:
        now_ts = now if now is not None else time.time()
        self._last_command_time[account_id] = now_ts

    def time_until_next(self, account_id: str, now: Optional[float] = None) -> float:
        now_ts = now if now is not None else time.time()
        last_ts = self._last_command_time.get(account_id, 0.0)
        remaining = self.min_delay_sec - (now_ts - last_ts)
        return max(0.0, remaining)

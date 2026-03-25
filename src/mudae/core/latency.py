from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from mudae.config import vars as Vars

AGGRESSIVE_DELAYS: List[float] = [0.00, 0.06, 0.08, 0.10, 0.12, 0.16, 0.22, 0.30, 0.45, 0.65]
BALANCED_DELAYS: List[float] = [0.00, 0.12, 0.16, 0.22, 0.30, 0.42, 0.60, 0.90]
_TIER_LEGACY = "legacy"
_TIER_BALANCED = "balanced"
_TIER_AGGRESSIVE = "aggressive"
_VALID_TIERS = {_TIER_LEGACY, _TIER_BALANCED, _TIER_AGGRESSIVE}

_DOWNGRADE_WINDOW_SEC = 30.0
_RECOVERY_WINDOW_SEC = 120.0
_MAX_EVENT_AGE_SEC = _RECOVERY_WINDOW_SEC + 5.0


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "y"}:
            return True
        if lowered in {"0", "false", "no", "off", "n"}:
            return False
    return default


def _normalize_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _VALID_TIERS:
        return text
    return None


def _tier_from_profile(profile: Any) -> str:
    text = str(profile or "aggressive_auto").strip().lower()
    if text in {"legacy", "safe", "compat"}:
        return _TIER_LEGACY
    if text in {"balanced", "safe_auto"}:
        return _TIER_BALANCED
    return _TIER_AGGRESSIVE


class LatencyController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Deque[Tuple[float, Optional[int], bool]] = deque()
        self._consecutive_failures = 0
        self._auto_degrade = True
        self._forced_tier: Optional[str] = None
        self._profile_default = "aggressive_auto"
        self._active_tier = _TIER_AGGRESSIVE
        self._tier_since = time.time()

    def configure(self, profile_default: Any, forced_tier: Any, auto_degrade: Any) -> None:
        normalized_force = _normalize_tier(forced_tier)
        normalized_auto = _to_bool(auto_degrade, True)
        target_profile = str(profile_default or "aggressive_auto")

        with self._lock:
            changed = False
            if self._profile_default != target_profile:
                self._profile_default = target_profile
                changed = True
            if self._forced_tier != normalized_force:
                self._forced_tier = normalized_force
                changed = True
            if self._auto_degrade != normalized_auto:
                self._auto_degrade = normalized_auto
                changed = True

            if not changed:
                return

            old_tier = self._active_tier
            if self._forced_tier:
                self._active_tier = self._forced_tier
            else:
                self._active_tier = _tier_from_profile(self._profile_default)
            if self._active_tier != old_tier:
                self._tier_since = time.time()
                self._emit_tier_change(old_tier, self._active_tier, "config_update")

    def active_tier(self) -> str:
        with self._lock:
            return self._active_tier

    def get_schedule(
        self,
        delay_sec: float,
        attempts: int,
        delay_schedule: Optional[List[float]] = None,
    ) -> Optional[List[float]]:
        with self._lock:
            tier = self._active_tier

        # Explicit call-site schedule wins.
        if delay_schedule:
            normalized = [max(0.0, float(value)) for value in delay_schedule]
            return normalized

        if tier == _TIER_LEGACY:
            return None

        base = AGGRESSIVE_DELAYS if tier == _TIER_AGGRESSIVE else BALANCED_DELAYS
        try:
            max_delay = float(delay_sec)
        except (TypeError, ValueError):
            max_delay = 0.0

        capped: List[float] = []
        for value in base:
            delay_value = max(0.0, float(value))
            if max_delay > 0:
                delay_value = min(delay_value, max_delay)
            capped.append(delay_value)

        # Keep full tier schedule even when caller passes a smaller attempt count.
        # Fetch waiters can then use the schedule length for aggressive burst polling.
        if attempts <= len(capped):
            return list(capped)
        if not capped:
            return None
        return capped + [capped[-1]] * (attempts - len(capped))

    def record_poll_result(
        self,
        status_code: Optional[int],
        *,
        error: bool = False,
        retry_after: Optional[float] = None,
        context: Optional[str] = None,
    ) -> None:
        del retry_after
        del context
        now = time.time()
        with self._lock:
            self._events.append((now, status_code, bool(error)))
            self._prune_events(now)

            transient_failure = bool(error) or status_code is None or int(status_code) >= 500
            if transient_failure:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0

            if self._forced_tier:
                return
            if not self._auto_degrade:
                return

            self._maybe_transition(now)

    def _prune_events(self, now: float) -> None:
        while self._events and (now - self._events[0][0]) > _MAX_EVENT_AGE_SEC:
            self._events.popleft()

    def _window_stats(self, now: float, window_sec: float) -> Tuple[int, int]:
        request_count = 0
        rate_limit_count = 0
        for ts, status_code, _error in self._events:
            if (now - ts) > window_sec:
                continue
            request_count += 1
            if status_code == 429:
                rate_limit_count += 1
        return request_count, rate_limit_count

    def _maybe_transition(self, now: float) -> None:
        request_count, rate_limit_count = self._window_stats(now, _DOWNGRADE_WINDOW_SEC)
        ratio_429 = (rate_limit_count / request_count) if request_count else 0.0
        old_tier = self._active_tier
        reason: Optional[str] = None

        if self._active_tier == _TIER_AGGRESSIVE:
            if (request_count >= 20 and ratio_429 >= 0.05) or self._consecutive_failures >= 3:
                self._active_tier = _TIER_BALANCED
                reason = "auto_downgrade_aggressive_to_balanced"
        elif self._active_tier == _TIER_BALANCED:
            if (request_count >= 20 and ratio_429 >= 0.10) or self._consecutive_failures >= 6:
                self._active_tier = _TIER_LEGACY
                reason = "auto_downgrade_balanced_to_legacy"

        if reason is None and (now - self._tier_since) >= _RECOVERY_WINDOW_SEC:
            healthy_req, healthy_429 = self._window_stats(now, _RECOVERY_WINDOW_SEC)
            healthy_ratio = (healthy_429 / healthy_req) if healthy_req else 0.0
            if healthy_req >= 20 and healthy_ratio < 0.01 and self._consecutive_failures == 0:
                if self._active_tier == _TIER_LEGACY:
                    self._active_tier = _TIER_BALANCED
                    reason = "auto_recover_legacy_to_balanced"
                elif self._active_tier == _TIER_BALANCED:
                    self._active_tier = _TIER_AGGRESSIVE
                    reason = "auto_recover_balanced_to_aggressive"

        if self._active_tier != old_tier:
            self._tier_since = now
            self._emit_tier_change(old_tier, self._active_tier, reason or "auto_transition")

    def _emit_tier_change(self, from_tier: str, to_tier: str, reason: str) -> None:
        try:
            from mudae.storage.latency_metrics import record_event

            record_event(
                "tier_change",
                from_tier=from_tier,
                to_tier=to_tier,
                reason=reason,
            )
        except Exception:
            return


_CONTROLLER = LatencyController()


def configure_from_vars() -> None:
    _CONTROLLER.configure(
        profile_default=getattr(Vars, "LATENCY_PROFILE_DEFAULT", "aggressive_auto"),
        forced_tier=getattr(Vars, "LATENCY_FORCE_PROFILE", ""),
        auto_degrade=getattr(Vars, "LATENCY_AUTO_DEGRADE", True),
    )


def get_active_tier() -> str:
    configure_from_vars()
    return _CONTROLLER.active_tier()


def get_delay_schedule(
    delay_sec: float,
    attempts: int,
    delay_schedule: Optional[List[float]] = None,
) -> Optional[List[float]]:
    configure_from_vars()
    return _CONTROLLER.get_schedule(delay_sec, attempts, delay_schedule=delay_schedule)


def record_poll_result(
    status_code: Optional[int],
    *,
    error: bool = False,
    retry_after: Optional[float] = None,
    context: Optional[str] = None,
) -> None:
    configure_from_vars()
    _CONTROLLER.record_poll_result(
        status_code,
        error=error,
        retry_after=retry_after,
        context=context,
    )

from typing import Optional, Tuple
from datetime import datetime, timezone, timedelta
import math
import re


def _parse_discord_timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        raw = value.replace('Z', '+00:00')
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return None


def calculateFixedResetSeconds(now: Optional[datetime] = None) -> Tuple[int, int]:
    """Return seconds until next roll reset (:55 hourly) and claim reset (:55 every 3 hours)."""
    current = now or datetime.now()
    roll_candidate = current.replace(minute=55, second=0, microsecond=0)
    if roll_candidate <= current:
        roll_candidate = roll_candidate + timedelta(hours=1)
    roll_seconds = int((roll_candidate - current).total_seconds())

    claim_candidate = current.replace(minute=55, second=0, microsecond=0)
    hour_mod = claim_candidate.hour % 3
    if hour_mod != 2:
        offset = (2 - hour_mod) % 3
        claim_candidate = claim_candidate + timedelta(hours=offset)
    if claim_candidate <= current:
        claim_candidate = claim_candidate + timedelta(hours=3)
    claim_seconds = int((claim_candidate - current).total_seconds())

    return (max(0, roll_seconds), max(0, claim_seconds))


def calculateFixedResetMinutes(now: Optional[datetime] = None) -> Tuple[int, int]:
    """Return minutes until next roll reset (:55 hourly) and claim reset (:55 every 3 hours)."""
    roll_seconds, claim_seconds = calculateFixedResetSeconds(now)
    roll_minutes = int(math.ceil(roll_seconds / 60)) if roll_seconds > 0 else 0
    claim_minutes = int(math.ceil(claim_seconds / 60)) if claim_seconds > 0 else 0
    return (roll_minutes, claim_minutes)


def parseMudaeTime(time_str: str) -> int:
    """Parse Mudae time format: '19h 34' -> 1174 minutes, '20h 00' -> 1200 minutes, '46' -> 46 minutes"""
    if not time_str:
        return 0
    time_str = time_str.strip().lower()
    total_minutes = 0

    hours_match = re.search(r'(\d+)\s*h', time_str)
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60

    minutes_match = None
    if 'h' in time_str:
        after_h = time_str.split('h', 1)[1]
        minutes_match = re.search(r'(\d+)', after_h)
    else:
        minutes_match = re.search(r'(\d+)', time_str)

    if minutes_match:
        total_minutes += int(minutes_match.group(1))

    return total_minutes if total_minutes > 0 else 0


def formatTimeHrsMin(minutes: int) -> str:
    """Convert minutes to 'Xhrs Ymin' format. E.g., 100 -> '1hrs 40min', 45 -> '45min'"""
    if minutes <= 0:
        return '0min'
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0 and mins > 0:
        return f"{hours}hrs {mins}min"
    if hours > 0:
        return f"{hours}hrs"
    return f"{mins}min"


def formatTimeHrsMinSec(seconds: int) -> str:
    """Convert seconds to 'Xhrs Ymin Zsec' format with seconds precision."""
    if seconds <= 0:
        return '0sec'
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}hrs {minutes}min {secs}sec"
    if minutes > 0:
        return f"{minutes}min {secs}sec"
    return f"{secs}sec"

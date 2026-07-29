import re
import time
import copy
from typing import Optional, Dict, Any, Tuple
from mudae.config import vars as Vars
from mudae.parsers.time_parser import _parse_discord_timestamp, parseMudaeTime, formatTimeHrsMin
from mudae.core.session_logging import log_warn

_last_tu_info_cache: Dict[str, Dict[str, Any]] = {}
_last_tu_info_at: Dict[str, float] = {}
initial_tu_cache: Dict[str, Dict[str, Any]] = {}
_last_fetch_reason: Dict[str, Dict[str, str]] = {
    "tu": {},
    "wl": {},
    "report": {},
    "special": {},
}

def getMaxPowerForToken(token: str) -> int:
    """Resolve max power for a token from Vars."""
    if not token:
        return 100
    if hasattr(Vars, "tokens") and isinstance(Vars.tokens, list):
        for entry in Vars.tokens:
            if isinstance(entry, dict) and entry.get("token") == token:
                if "max_power" in entry:
                    try:
                        return int(entry["max_power"])
                    except (TypeError, ValueError):
                        pass
    overrides = getattr(Vars, "TOKEN_MAX_POWER", None)
    if isinstance(overrides, dict) and token in overrides:
        try:
            return int(overrides[token])
        except (TypeError, ValueError):
            pass
    return int(getattr(Vars, "MAX_POWER", 100) or 100)

def _cache_tu_info(token: str, tu_info: Optional[Dict[str, Any]]) -> None:
    if not token or not tu_info:
        return
    _last_tu_info_cache[token] = dict(tu_info)
    _last_tu_info_at[token] = time.time()

def _set_last_fetch_reason(kind: str, token: str, reason: Optional[str]) -> None:
    if not kind or not token:
        return
    bucket = _last_fetch_reason.setdefault(kind, {})
    if reason:
        bucket[token] = str(reason)
    else:
        bucket.pop(token, None)

def _get_last_fetch_reason(kind: str, token: str) -> Optional[str]:
    if not kind or not token:
        return None
    return _last_fetch_reason.get(kind, {}).get(token)

def getLastTuFetchReason(token: str) -> Optional[str]:
    return _get_last_fetch_reason("tu", token)

def _tu_reuse_max_age_sec() -> float:
    try:
        return max(0.0, float(getattr(Vars, "TU_INFO_REUSE_MAX_AGE_SEC", 90.0) or 90.0))
    except (TypeError, ValueError):
        return 90.0

def _get_cached_tu_info(token: str, max_age_sec: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    cached = _last_tu_info_cache.get(token)
    if not cached:
        return None
    timestamp = _last_tu_info_at.get(token)
    if max_age_sec is not None and timestamp is not None:
        try:
            if (time.time() - float(timestamp)) > float(max_age_sec):
                return None
        except (TypeError, ValueError):
            pass
    return dict(cached)

def _apply_cached_tu_updates(
    token: str,
    tu_info: Optional[Dict[str, Any]],
    **updates: Any,
) -> Optional[Dict[str, Any]]:
    base = dict(tu_info) if tu_info else _get_cached_tu_info(token)
    if not base:
        return tu_info
    base.update(updates)
    _cache_tu_info(token, base)
    if token in initial_tu_cache:
        initial_tu_cache[token] = dict(base)
    return base

def _synthesize_tu_after_dk(token: str, tu_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    from mudae.core import session_engine as Session
    max_power = Session.getMaxPowerForToken(token)
    return _apply_cached_tu_updates(
        token,
        tu_info,
        current_power=max_power,
        max_power=max_power,
        dk_ready=False,
    )

def _synthesize_tu_after_rt(token: str, tu_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _apply_cached_tu_updates(
        token,
        tu_info,
        can_claim_now=True,
        rt_available=False,
    )

def _merge_tu_info(primary: Optional[Dict[str, Any]], fallback: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if fallback:
        merged.update(fallback)
    if primary:
        merged.update(primary)
    return merged

def _parse_tu_message(message: str) -> Optional[Dict[str, Any]]:
    """Parse /tu response content into status dict."""
    if not message:
        return None
    if 'Command under maintenance' in message:
        match = re.search(r'For \*\*(\d+)\*\* minutes', message)
        maintenance_min = int(match.group(1)) if match else 3
        log_warn(f"Mudae is under maintenance for {maintenance_min} minutes")
        return {'rolls': 0, 'next_reset_min': maintenance_min, 'maintenance': True}

    roll_match = re.search(r'You have \*\*(\d+)\*\* rolls left', message)
    rolls_left = int(roll_match.group(1)) if roll_match else 0

    time_match = re.search(r'Next rolls reset in \*\*(\d+)\*\* min', message)
    next_roll_min = int(time_match.group(1)) if time_match else 0

    can_claim_now = re.search(r'you __can__ claim right now!', message, re.IGNORECASE) is not None
    claim_reset_match = re.search(r'The next claim reset is in \*\*([\dh\s]+)\*\* min', message)
    claim_cant_match = re.search(r"you can't claim for another \*\*([\dh\s]+)\*\* min", message)
    if claim_reset_match:
        time_str = claim_reset_match.group(1).strip()
        claim_reset_min = parseMudaeTime(time_str)
    elif claim_cant_match:
        time_str = claim_cant_match.group(1).strip()
        claim_reset_min = parseMudaeTime(time_str)
    else:
        claim_reset_min = 0

    daily_reset_match = re.search(r'Next \$daily reset in \*\*([\dh\s]+)\*\* min', message)
    if daily_reset_match:
        time_str = daily_reset_match.group(1).strip()
        daily_reset_min = parseMudaeTime(time_str)
    else:
        daily_reset_min = 0

    power_match = re.search(r'Power: \*\*(\d+)%\*\*', message)
    current_power = int(power_match.group(1)) if power_match else 0

    cost_match = re.search(r'Each kakera button consumes (\d+)% of your reaction power', message)
    kakera_cost = int(cost_match.group(1)) if cost_match else 40

    can_react_now = re.search(r'you __can__ react to kakera right now!', message, re.IGNORECASE) is not None
    react_cooldown_match = re.search(r"You can't react to kakera for \*\*([\dh\s]+)\*\* min", message)
    if react_cooldown_match:
        time_str = react_cooldown_match.group(1).strip()
        react_cooldown_min = parseMudaeTime(time_str)
        if react_cooldown_min > 0:
            can_react_now = False
    else:
        react_cooldown_min = 0

    daily_available = '$daily is available!' in message

    rt_available = '$rt is available!' in message
    rt_reset_match = re.search(r'The cooldown of \$rt is not over\. Time left: \*\*([\dh\s]+)\*\* min', message)
    if rt_reset_match:
        rt_available = False
        time_str = rt_reset_match.group(1).strip()
        rt_reset_min = parseMudaeTime(time_str)
    else:
        rt_reset_min = 0

    dk_ready = '$dk is ready!' in message
    dk_reset_match = re.search(r'Next \$dk in \*\*([\dh\s]+)\*\* min', message)
    if dk_reset_match:
        dk_ready = False
        time_str = dk_reset_match.group(1).strip()
        dk_reset_min = parseMudaeTime(time_str)
    else:
        dk_reset_min = 0

    total_balance = 0
    balance_match = re.search(r'Stock: \*\*([0-9,]+)\*\*\s*<:kakera:', message)
    if not balance_match:
        for match in re.finditer(r'Stock: \*\*([0-9,]+)\*\*', message):
            after = message[match.end():match.end() + 10]
            if '<:sp' not in after:
                balance_match = match
                break
    if balance_match:
        balance_str = balance_match.group(1).replace(',', '')
        total_balance = int(balance_str)

    sphere_balance = None
    sphere_match = re.search(r'Stock: \*\*([0-9,]+)\*\*\s*<:sp:', message)
    if sphere_match:
        sphere_str = sphere_match.group(1).replace(',', '')
        try:
            sphere_balance = int(sphere_str)
        except ValueError:
            sphere_balance = None

    oh_left = None
    oc_left = None
    oq_left = None
    oh_stored = 0
    oc_stored = 0
    oq_stored = 0

    oh_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oh left for today(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oh_match:
        oh_left = int(oh_match.group(1))
        if oh_match.group(2):
            oh_stored = int(oh_match.group(2))

    oc_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oc(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oc_match:
        oc_left = int(oc_match.group(1))
        if oc_match.group(2):
            oc_stored = int(oc_match.group(2))

    oq_match = re.search(
        r'\*\*(\d+)\*\*\s*\$oq(?:\s*\(\+\*\*(\d+)\*\*\s*stored\))?',
        message,
        re.IGNORECASE
    )
    if oq_match:
        oq_left = int(oq_match.group(1))
        if oq_match.group(2):
            oq_stored = int(oq_match.group(2))

    ouro_refill_min = None
    refill_match = re.search(r'\*\*([\dh\s]+)\*\*\s*min before the refill', message, re.IGNORECASE)
    if refill_match:
        time_str = refill_match.group(1).strip()
        ouro_refill_min = parseMudaeTime(time_str)

    return {
        'rolls': rolls_left,
        'next_reset_min': next_roll_min,
        'can_claim_now': can_claim_now,
        'claim_reset_min': claim_reset_min,
        'current_power': current_power,
        'kakera_cost': kakera_cost,
        'can_react_now': can_react_now,
        'react_cooldown_min': react_cooldown_min,
        'daily_available': daily_available,
        'daily_reset_min': daily_reset_min,
        'rt_available': rt_available,
        'rt_reset_min': rt_reset_min,
        'dk_ready': dk_ready,
        'dk_reset_min': dk_reset_min,
        'total_balance': total_balance,
        'oh_left': oh_left,
        'oc_left': oc_left,
        'oq_left': oq_left,
        'oh_stored': oh_stored,
        'oc_stored': oc_stored,
        'oq_stored': oq_stored,
        'oh_total': (oh_left + oh_stored) if oh_left is not None else None,
        'oc_total': (oc_left + oc_stored) if oc_left is not None else None,
        'oq_total': (oq_left + oq_stored) if oq_left is not None else None,
        'ouro_refill_min': ouro_refill_min,
        'sphere_balance': sphere_balance
    }

def _cache_initial_tu(token: str, message: str, timestamp: Optional[str] = None) -> None:
    status = _parse_tu_message(message)
    if status:
        status['max_power'] = getMaxPowerForToken(token)
        parsed_timestamp = _parse_discord_timestamp(timestamp)
        if parsed_timestamp:
            status['tu_timestamp'] = parsed_timestamp
        initial_tu_cache[token] = status

def calculatePowerStats(current_power: int, kakera_cost: int, dk_ready: bool, minutes_to_wait: int = 0, max_power: int = 100) -> Tuple[str, int]:
    """Calculate power stats for status display."""
    regen_rate_per_hour = getattr(Vars, 'POWER_REGEN_PER_HOUR', 100)
    regen_per_min = regen_rate_per_hour / 60.0
    projected = min(max_power, current_power + int(minutes_to_wait * regen_per_min))
    if dk_ready:
        projected = max_power

    reactions = projected // kakera_cost
    rem_power = projected % kakera_cost

    if reactions > 0:
        return f"{projected}% ({reactions} rxns, {rem_power}% rem)", projected
    else:
        needed = kakera_cost - projected
        mins_needed = int(needed / regen_per_min) if regen_per_min > 0 else 0
        return f"{projected}% (need +{needed}% = {mins_needed}m)", projected

def formatDetailedStatus(tu_info: Dict[str, Any], is_prediction: bool = False, predicted_power: Optional[int] = None) -> str:
    """Format comprehensive status with hours/minutes and detailed power analysis"""
    lines = []
    prefix = "Predicted Status" if is_prediction else "Current Status"
    lines.append(f"--- {prefix} ---")

    rolls = tu_info.get('rolls', 0)
    roll_reset = formatTimeHrsMin(tu_info.get('next_reset_min', 0))
    lines.append(f"Rolls: {rolls} left (Reset in {roll_reset})")

    if is_prediction:
        power = predicted_power if predicted_power is not None else tu_info.get('current_power', 0)
        power_str, _ = calculatePowerStats(power, tu_info.get('kakera_cost', 40), False, 0, tu_info.get('max_power', 100))
        lines.append(f"Power: {power_str}")
    else:
        power_str, _ = calculatePowerStats(
            tu_info.get('current_power', 0),
            tu_info.get('kakera_cost', 40),
            tu_info.get('dk_ready', False),
            0,
            tu_info.get('max_power', 100)
        )
        lines.append(f"Power: {power_str}")

    if tu_info.get('can_claim_now'):
        lines.append("Claim: AVAILABLE NOW!")
    else:
        claim_reset = formatTimeHrsMin(tu_info.get('claim_reset_min', 0))
        lines.append(f"Claim: Cooldown ({claim_reset} left)")

    daily_str = "Available!" if tu_info.get('daily_available') else f"Cooldown ({formatTimeHrsMin(tu_info.get('daily_reset_min', 0))})"
    lines.append(f"$daily: {daily_str}")

    rt_str = "Available!" if tu_info.get('rt_available') else f"Cooldown ({formatTimeHrsMin(tu_info.get('rt_reset_min', 0))})"
    lines.append(f"$rt: {rt_str}")

    dk_str = "READY!" if tu_info.get('dk_ready') else f"Cooldown ({formatTimeHrsMin(tu_info.get('dk_reset_min', 0))})"
    lines.append(f"$dk: {dk_str}")

    return "\n".join(lines)

def predictStatusAfterCountdown(tu_info: Dict[str, Any], minutes_to_wait: int) -> str:
    """Predict the status after waiting X minutes with proper time calculations"""
    pred = dict(tu_info)

    if pred.get('next_reset_min', 0) <= minutes_to_wait:
        pred['rolls'] = Vars.ROLLS_PER_RESET
        pred['next_reset_min'] = max(0, pred.get('next_reset_min', 0) + 60 - minutes_to_wait)
    else:
        pred['next_reset_min'] = pred.get('next_reset_min', 0) - minutes_to_wait

    if pred.get('claim_reset_min', 0) <= minutes_to_wait:
        pred['can_claim_now'] = True
        pred['claim_reset_min'] = 0
    else:
        pred['claim_reset_min'] = pred.get('claim_reset_min', 0) - minutes_to_wait

    if pred.get('daily_reset_min', 0) <= minutes_to_wait:
        pred['daily_available'] = True
        pred['daily_reset_min'] = 0
    else:
        pred['daily_reset_min'] = pred.get('daily_reset_min', 0) - minutes_to_wait

    if pred.get('rt_reset_min', 0) <= minutes_to_wait:
        pred['rt_available'] = True
        pred['rt_reset_min'] = 0
    else:
        pred['rt_reset_min'] = pred.get('rt_reset_min', 0) - minutes_to_wait

    if pred.get('dk_reset_min', 0) <= minutes_to_wait:
        pred['dk_ready'] = True
        pred['dk_reset_min'] = 0
    else:
        pred['dk_reset_min'] = pred.get('dk_reset_min', 0) - minutes_to_wait

    _, pred_power = calculatePowerStats(
        tu_info.get('current_power', 0),
        tu_info.get('kakera_cost', 40),
        tu_info.get('dk_ready', False),
        minutes_to_wait,
        tu_info.get('max_power', 100)
    )

    return formatDetailedStatus(pred, is_prediction=True, predicted_power=pred_power)

def isSessionEligible(tu_info: Dict[str, Any]) -> bool:
    """Check if a rolling session can proceed based on /tu status"""
    if not tu_info or not isinstance(tu_info, dict):
        return False
    if tu_info.get('maintenance'):
        return False

    has_rolls = tu_info.get('rolls', 0) > 0
    kakera_cost = tu_info.get('kakera_cost', 40)
    has_power = (tu_info.get('current_power', 0) >= kakera_cost) or tu_info.get('can_react_now', False)
    can_claim = tu_info.get('can_claim_now', False)
    has_dk = tu_info.get('dk_ready', False)

    return has_rolls or has_power or can_claim or has_dk

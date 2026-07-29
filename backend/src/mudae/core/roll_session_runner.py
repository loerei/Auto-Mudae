import time
from typing import Optional, Dict, Any, Tuple, List
from mudae.config import vars as Vars
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.core.session_logging import log, log_warn, log_info, log_error
from mudae.core.tu_status_parser import isSessionEligible, _get_cached_tu_info
from mudae.core.command_gate import CommandAntiSpamGate

_command_gate = CommandAntiSpamGate()

def _build_roll_coordination_scope(
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
) -> Tuple[str, str]:
    token_key = token[:12] if token else "none"
    owner_id = f"roll@pid{user_id or user_name or token_key}"
    server_id = getattr(Vars, "serverId", None)
    channel_id = getattr(Vars, "channelId", None)
    scope, _ = build_identity_scope(
        "roll-session",
        token=token,
        user_id=user_id,
        user_name=user_name,
        server_id=server_id,
        channel_id=channel_id,
    )
    return scope, owner_id

def _acquire_same_account_action_gate(
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
    *,
    wait_timeout_sec: float = 0.0,
) -> Tuple[Optional[Any], bool]:
    from mudae.core import session_engine as Session
    scope, owner_id = _build_roll_coordination_scope(token, user_id, user_name)
    acquire_lease_fn = getattr(Session, "acquire_lease", acquire_lease)
    lease = acquire_lease_fn(
        scope,
        owner_id,
        ttl_sec=float(getattr(Vars, "SAME_ACCOUNT_ACTION_GATE_TTL_SEC", 120.0) or 120.0),
        heartbeat_sec=5.0,
        wait_timeout_sec=wait_timeout_sec,
    )
    return lease, lease.acquired

def _message_indicates_roll_exhausted(content: str) -> bool:
    if not content or not isinstance(content, str):
        return False
    lowered = content.lower()
    return (
        "the roulette is limit" in lowered
        or "the roulette is disabled" in lowered
        or "0 rolls left" in lowered
        or "0** rolls left" in lowered
        or "don't have any rolls left" in lowered
        or "don't have any roll left" in lowered
    )

def _refresh_roll_status(token: str, reason: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    log_info(f"Refreshing roll status: {reason}")
    cached = _get_cached_tu_info(token)
    return cached, False

def _reconcile_roll_target(
    roll_count: int,
    rolls_to_make: int,
    refreshed_tu: Optional[Dict[str, Any]],
) -> Tuple[int, int, bool]:
    if not refreshed_tu:
        return roll_count, rolls_to_make, False
    refreshed_rolls = refreshed_tu.get("rolls", 0)
    if refreshed_rolls <= 0:
        return roll_count, 0, True
    new_target = roll_count + refreshed_rolls
    return new_target, refreshed_rolls, False

def _run_enhanced_roll_session(
    *,
    token: str,
    bot: Any,
    auth: Dict[str, str],
    url: str,
    user_id: Optional[str],
    user_name: Optional[str],
    tu_info: Dict[str, Any],
    mudae_star_wishes: List[str],
    mudae_regular_wishes: List[str],
    skip_pre_roll_revalidation: bool = False,
) -> Optional[Dict[str, Any]]:
    log(f"Starting enhanced roll session for {user_name or 'user'}")
    return tu_info

def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Enhanced rolling system with command management and eligibility checks."""
    if not token:
        log_error("enhancedRoll: Token is empty")
        return None

    from mudae.core import session_engine as Session

    bot_fn = getattr(Session, "getClientAndAuth", None)
    if bot_fn:
        bot, auth = bot_fn(token)
    else:
        bot, auth = Session.getClientAndAuth(token)

    url_fn = getattr(Session, "getUrl", None)
    url = url_fn() if url_fn else Session.getUrl()

    identity_fn = getattr(Session, "_ensure_user_identity", None)
    if identity_fn:
        user_id, user_name = identity_fn(token)
    else:
        user_id, user_name = Session._ensure_user_identity(token)

    gate_fn = getattr(Session, "_acquire_same_account_action_gate", _acquire_same_account_action_gate)
    gate_lease, gate_acquired = gate_fn(token, user_id, user_name, wait_timeout_sec=0.0)
    if not gate_acquired:
        Session._set_last_fetch_reason("tu", token, "cache_hit")
        get_cached_fn = getattr(Session, "_get_cached_tu_info", _get_cached_tu_info)
        return get_cached_fn(token)

    skip_pre_roll = False
    if initial_tu_info is not None:
        tu_info = dict(initial_tu_info)
    else:
        get_tu_fn = getattr(Session, "getTuInfo", None)
        if get_tu_fn:
            tu_info = get_tu_fn(token)
        else:
            tu_info = Session.getTuInfo(token)
        skip_pre_roll = True

    if not tu_info:
        log_warn("enhancedRoll: Unable to obtain /tu status")
        return None

    fetch_wl_fn = getattr(Session, "fetchAndParseMudaeWishlist", None)
    if fetch_wl_fn:
        wishlist = fetch_wl_fn(token)
    else:
        wishlist = Session.fetchAndParseMudaeWishlist(token)
    star_wishes = wishlist.get("star_wishes", []) if wishlist else []
    regular_wishes = wishlist.get("regular_wishes", []) if wishlist else []

    run_session_fn = getattr(Session, "_run_enhanced_roll_session", _run_enhanced_roll_session)
    return run_session_fn(
        token=token,
        bot=bot,
        auth=auth,
        url=url,
        user_id=user_id,
        user_name=user_name,
        tu_info=tu_info,
        mudae_star_wishes=star_wishes,
        mudae_regular_wishes=regular_wishes,
        skip_pre_roll_revalidation=skip_pre_roll,
    )

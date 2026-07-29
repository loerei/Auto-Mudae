import re
from typing import List, Optional, Tuple, Any, Dict, NamedTuple, Callable
from dataclasses import dataclass

from mudae.config import vars as Vars
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.core.session_logging import log, log_warn, log_info, log_error
from mudae.core.tu_status_parser import _get_cached_tu_info, _set_last_fetch_reason
from mudae.core.command_gate import CommandAntiSpamGate


class ClaimDecision(NamedTuple):
    should_claim: bool
    priority: int
    reason: str


@dataclass
class RollDependencies:
    client_provider: Optional[Callable[[str], Tuple[Any, Dict[str, str]]]] = None
    url_provider: Optional[Callable[[], str]] = None
    identity_provider: Optional[Callable[[str], Tuple[Optional[str], Optional[str]]]] = None
    tu_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
    wishlist_provider: Optional[Callable[[str], Dict[str, Any]]] = None
    lease_provider: Optional[Callable[..., Any]] = None


class RollEngine:
    """
    Unified Roll Engine encapsulating candidate evaluation, rate-limiting gate coordination,
    roll target reconciliation, and roll session execution.
    """
    def __init__(self, command_gate: Optional[CommandAntiSpamGate] = None) -> None:
        self.command_gate = command_gate or CommandAntiSpamGate()

    def wish_matches(self, target: str, wish: str) -> bool:
        if not wish:
            return False
        try:
            pattern = rf'(?<!\w){re.escape(wish)}(?!\w)'
            return re.search(pattern, target, re.IGNORECASE) is not None
        except re.error:
            return wish.lower() == target.lower()

    def evaluate_candidate(
        self,
        card_name: str,
        card_series: str,
        kakera_value: int = 0,
        star_wishes: Optional[List[str]] = None,
        regular_wishes: Optional[List[str]] = None,
        min_kakera_claim: int = 150
    ) -> ClaimDecision:
        star_wishes = star_wishes or []
        regular_wishes = regular_wishes or []

        # 1. Star wishes (Priority 3)
        for wish in star_wishes:
            if self.wish_matches(card_name, wish) or self.wish_matches(card_series, wish):
                return ClaimDecision(True, 3, f"Star wish match: {wish}")

        # 2. Regular wishes (Priority 2)
        for wish in regular_wishes:
            if self.wish_matches(card_name, wish) or self.wish_matches(card_series, wish):
                return ClaimDecision(True, 2, f"Regular wish match: {wish}")

        # 3. Vars.py wishlist
        wishlist_items = getattr(Vars, "wishlist", []) or []
        for item in wishlist_items:
            priority = 2
            wish_value = item
            if isinstance(item, dict):
                wish_value = item.get('name') or item.get('value') or ''
                try:
                    priority = int(item.get('priority') or 2)
                except (TypeError, ValueError):
                    priority = 2
                if bool(item.get('is_star') or item.get('star')):
                    priority = max(priority, 3)
            if self.wish_matches(card_name, str(wish_value)) or self.wish_matches(card_series, str(wish_value)):
                return ClaimDecision(True, max(1, priority), f"Vars wishlist match: {wish_value}")

        # 4. Kakera fallback threshold
        if kakera_value >= min_kakera_claim and min_kakera_claim > 0:
            return ClaimDecision(True, 1, f"High Kakera value ({kakera_value})")

        return ClaimDecision(False, 1, "No wishlist or kakera match")

    def build_coordination_scope(
        self,
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

    def acquire_action_gate(
        self,
        token: str,
        user_id: Optional[str],
        user_name: Optional[str],
        *,
        wait_timeout_sec: float = 0.0,
        lease_fn: Optional[Callable[..., Any]] = None,
    ) -> Tuple[Optional[Any], bool]:
        scope, owner_id = self.build_coordination_scope(token, user_id, user_name)
        acquire_fn = lease_fn or acquire_lease
        lease = acquire_fn(
            scope,
            owner_id,
            ttl_sec=float(getattr(Vars, "SAME_ACCOUNT_ACTION_GATE_TTL_SEC", 120.0) or 120.0),
            heartbeat_sec=5.0,
            wait_timeout_sec=wait_timeout_sec,
        )
        return lease, lease.acquired

    def message_indicates_roll_exhausted(self, content: str) -> bool:
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

    def refresh_roll_status(self, token: str, reason: str) -> Tuple[Optional[Dict[str, Any]], bool]:
        log_info(f"Refreshing roll status: {reason}")
        cached = _get_cached_tu_info(token)
        return cached, False

    def reconcile_roll_target(
        self,
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

    def run_enhanced_roll_session(
        self,
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

    def run_session(
        self,
        token: str,
        initial_tu_info: Optional[Dict[str, Any]] = None,
        deps: Optional[RollDependencies] = None,
    ) -> Optional[Dict[str, Any]]:
        if not token:
            log_error("RollEngine.run_session: Token is empty")
            return None

        deps = deps or RollDependencies()
        from mudae.core import session_engine as Session

        bot_fn = deps.client_provider or getattr(Session, "getClientAndAuth", None)
        if bot_fn:
            bot, auth = bot_fn(token)
        else:
            bot, auth = Session.getClientAndAuth(token)

        url_fn = deps.url_provider or getattr(Session, "getUrl", None)
        url = url_fn() if url_fn else Session.getUrl()

        identity_fn = deps.identity_provider or getattr(Session, "_ensure_user_identity", None)
        if identity_fn:
            user_id, user_name = identity_fn(token)
        else:
            user_id, user_name = Session._ensure_user_identity(token)

        lease_fn = deps.lease_provider or getattr(Session, "acquire_lease", acquire_lease)
        gate_lease, gate_acquired = self.acquire_action_gate(token, user_id, user_name, wait_timeout_sec=0.0, lease_fn=lease_fn)
        if not gate_acquired:
            set_reason_fn = getattr(Session, "_set_last_fetch_reason", _set_last_fetch_reason)
            set_reason_fn("tu", token, "cache_hit")
            get_cached_fn = getattr(Session, "_get_cached_tu_info", _get_cached_tu_info)
            return get_cached_fn(token)

        skip_pre_roll = False
        if initial_tu_info is not None:
            tu_info = dict(initial_tu_info)
        else:
            tu_fn = deps.tu_provider or getattr(Session, "getTuInfo", None)
            if tu_fn:
                tu_info = tu_fn(token)
            else:
                tu_info = Session.getTuInfo(token)
            skip_pre_roll = True

        if not tu_info:
            log_warn("RollEngine.run_session: Unable to obtain /tu status")
            return None

        wl_fn = deps.wishlist_provider or getattr(Session, "fetchAndParseMudaeWishlist", None)
        if wl_fn:
            wishlist = wl_fn(token)
        else:
            wishlist = Session.fetchAndParseMudaeWishlist(token)

        star_wishes = wishlist.get("star_wishes", []) if wishlist else []
        regular_wishes = wishlist.get("regular_wishes", []) if wishlist else []

        run_session_fn = getattr(Session, "_run_enhanced_roll_session", self.run_enhanced_roll_session)
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


_default_roll_engine = RollEngine()
RollOrchestrator = RollEngine

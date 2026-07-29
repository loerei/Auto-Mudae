from typing import Optional, Dict, Any, Tuple, List
from mudae.core.roll_engine import RollEngine, RollDependencies, _default_roll_engine

def _build_roll_coordination_scope(
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
) -> Tuple[str, str]:
    return _default_roll_engine.build_coordination_scope(token, user_id, user_name)

def _acquire_same_account_action_gate(
    token: str,
    user_id: Optional[str],
    user_name: Optional[str],
    *,
    wait_timeout_sec: float = 0.0,
) -> Tuple[Optional[Any], bool]:
    return _default_roll_engine.acquire_action_gate(token, user_id, user_name, wait_timeout_sec=wait_timeout_sec)

def _message_indicates_roll_exhausted(content: str) -> bool:
    return _default_roll_engine.message_indicates_roll_exhausted(content)

def _refresh_roll_status(token: str, reason: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    return _default_roll_engine.refresh_roll_status(token, reason)

def _reconcile_roll_target(
    roll_count: int,
    rolls_to_make: int,
    refreshed_tu: Optional[Dict[str, Any]],
) -> Tuple[int, int, bool]:
    return _default_roll_engine.reconcile_roll_target(roll_count, rolls_to_make, refreshed_tu)

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
    return _default_roll_engine.run_enhanced_roll_session(
        token=token,
        bot=bot,
        auth=auth,
        url=url,
        user_id=user_id,
        user_name=user_name,
        tu_info=tu_info,
        mudae_star_wishes=mudae_star_wishes,
        mudae_regular_wishes=mudae_regular_wishes,
        skip_pre_roll_revalidation=skip_pre_roll_revalidation,
    )

def enhancedRoll(
    token: str,
    initial_tu_info: Optional[Dict[str, Any]] = None,
    deps: Optional[RollDependencies] = None,
) -> Optional[Dict[str, Any]]:
    """Enhanced rolling system delegating to RollEngine."""
    return _default_roll_engine.run_session(token, initial_tu_info=initial_tu_info, deps=deps)

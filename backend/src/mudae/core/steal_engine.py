import time
from collections import deque
from typing import Optional, Dict, Any, List
from mudae.config import vars as Vars
from mudae.discord import fetch as Fetch
from mudae.core.session_logging import log, log_warn
from mudae.core.tu_status_parser import _get_cached_tu_info, _merge_tu_info
from mudae.core.wishlist_manager import matchesWishlist
from mudae.core.claim_engine import _card_is_claimed, _attempt_claim_with_button_and_fallback
from mudae.parsers.card_parser import extractCardInfo
from mudae.parsers.reactions import _message_has_kakera_button, _find_claim_button

_processed_external_roll_ids: deque = deque(maxlen=200)

def _name_in_whitelist(name: Optional[str], whitelist: List[str]) -> bool:
    if not name or not whitelist:
        return False
    name_clean = name.strip().lower()
    return any(w.strip().lower() == name_clean for w in whitelist if w)

def _track_external_roll_id(message_id: str) -> bool:
    """Track a processed external roll id with bounded memory."""
    if not message_id:
        return False
    if message_id in _processed_external_roll_ids:
        return False
    _processed_external_roll_ids.append(message_id)
    return True

def _get_steal_tu_info(token: str, tu_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    max_age = getattr(Vars, 'STEAL_TU_MAX_AGE_SEC', 180)
    cached = _get_cached_tu_info(token, max_age)
    merged = _merge_tu_info(tu_info, cached)
    if merged:
        return merged
    return {}

def _try_external_kakera_react(
    message: Dict[str, Any],
    token: str,
    bot: Any,
    auth: Dict[str, str],
    url: str,
    tu_info: Dict[str, Any]
) -> Dict[str, Any]:
    """React to kakera buttons on another user's roll when possible."""
    if not _message_has_kakera_button(message):
        return {'reacted': False}
    return {'reacted': False}

def _scan_external_rolls(
    messages: List[Dict[str, Any]],
    token: str,
    user_id: Optional[str],
    url: str,
    auth: Dict[str, str],
    bot: Any,
    tu_info: Optional[Dict[str, Any]],
    mudae_star_wishes: List[str],
    mudae_regular_wishes: List[str],
    claim_emoji: str
) -> Dict[str, Any]:
    """Scan recent messages for other users' rolls and attempt steal-claim on wishlist hits."""
    result = {'scanned': 0, 'claimed': 0, 'kakera_reacted': 0}
    if not messages:
        return result
    return result

def pollExternalRolls(token: str, limit: int = 50) -> int:
    """Poll recent channel messages for other users' rolls."""
    if not token or not getattr(Vars, 'ENABLE_EXTERNAL_ROLL_POLLING', False):
        return 0
    return 0

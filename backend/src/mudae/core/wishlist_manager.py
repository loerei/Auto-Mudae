import time
import copy
import re
import unicodedata
from typing import Optional, Dict, Any, Tuple, List
from mudae.config import vars as Vars
from mudae.discord import fetch as Fetch
from mudae.core.session_logging import log, log_warn, log_success, logRawResponse, logSessionRawResponse

from mudae.core.session_state import _default_session_state_engine

_last_wishlist_cache: Dict[str, Dict[str, Any]] = _default_session_state_engine._last_wishlist_cache
_last_wishlist_at: Dict[str, float] = _default_session_state_engine._last_wishlist_at

def _cache_wishlist(token: str, wishlist_data: Optional[Dict[str, Any]]) -> None:
    _default_session_state_engine.cache_wishlist(token, wishlist_data)

def _wishlist_cache_ttl_sec() -> float:
    return _default_session_state_engine.wishlist_cache_ttl_sec()

def _get_cached_wishlist(token: str, max_age_sec: Optional[float] = None) -> Optional[Dict[str, Any]]:
    return _default_session_state_engine.get_cached_wishlist(token, max_age_sec)

def matchesWishlist(
    cardName: str,
    cardSeries: str,
    mudae_star_wishes: Optional[List[str]] = None,
    mudae_regular_wishes: Optional[List[str]] = None,
) -> Tuple[bool, int]:
    """Check if card matches wishlist."""
    stars = mudae_star_wishes or []
    regulars = mudae_regular_wishes or []

    cardName_lower = cardName.lower()
    cardSeries_lower = cardSeries.lower()

    for wish in stars:
        wish_lower = wish.lower()
        if wish_lower == cardName_lower or wish_lower == cardSeries_lower:
            return True, 3

    for wish in regulars:
        wish_lower = wish.lower()
        if wish_lower == cardName_lower or wish_lower == cardSeries_lower:
            return True, 2

    return False, 1

def _repair_mojibake(text: str) -> str:
    if not text:
        return text
    try:
        encoded = text.encode("latin1")
        decoded = encoded.decode("utf-8")
        return decoded
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def _normalize_wishlist_text(text: str) -> str:
    if not text:
        return ""
    text = _repair_mojibake(text)
    normalized = unicodedata.normalize("NFKC", text)
    return normalized

def _parse_wishlist_line(line: str) -> Tuple[str, bool, bool, bool]:
    """Return (name, has_star, is_claimed, is_failed) for a /wl line."""
    line_clean = line.strip()
    if not line_clean:
        return "", False, False, False

    line_clean = _repair_mojibake(line_clean)
    is_claimed = "claimed by" in line_clean.lower() or "belong to" in line_clean.lower() or "✅" in line_clean or "\u2705" in line_clean
    is_failed = "failed" in line_clean.lower() or "missing" in line_clean.lower() or "❌" in line_clean or "\u274c" in line_clean
    has_star = "⭐" in line_clean or "\u2b50" in line_clean

    match = re.search(r"\*\*([^*]+)\*\*", line_clean)
    if match:
        name = match.group(1).strip()
    else:
        cleaned_name = re.sub(r"<:[^:]+:\d+>", "", line_clean)
        cleaned_name = re.sub(r"\([^)]*\)", "", cleaned_name)
        cleaned_name = re.sub(r"\[[^\]]*\]", "", cleaned_name)
        cleaned_name = _normalize_wishlist_text(cleaned_name)
        name = re.sub(r"^[\s\-\*\•\d\.\>\#]+", "", cleaned_name).strip()

    name = re.sub(r"^[⭐\u2b50\s]+", "", name).strip()
    return name, has_star, is_claimed, is_failed

def fetchAndParseMudaeWishlist(token: str) -> Dict[str, Any]:
    """Fetch Mudae's wishlist via /wl command and parse it."""
    if not token:
        return {"status": "error", "error": "Token missing"}

    from mudae.core import session_engine as Session

    cached = Session._get_cached_wishlist(token, Session._wishlist_cache_ttl_sec())
    if cached:
        Session._set_last_fetch_reason("wl", token, "cache_hit")
        return cached

    Session._set_last_fetch_reason("wl", token, "fresh_fetch")
    bot, auth = Session.getClientAndAuth(token)
    url = Session.getUrl()
    user_id, user_name = Session._ensure_user_identity(token)

    gate_lease, gate_acquired = Session._acquire_same_account_action_gate(token, user_id, user_name, wait_timeout_sec=0.0)
    if not gate_acquired:
        return {"status": "error", "error": "Action gate busy"}

    cmd = Session.getSlashCommand(bot, ["wl"])
    trigger_fn = getattr(bot, "triggerSlashCommand", getattr(bot, "triggerSlash", None))
    if trigger_fn and cmd:
        trigger_fn(
            cmd.get("application_id"),
            cmd.get("id"),
            cmd.get("version"),
            cmd.get("type"),
            guildID=Vars.serverId,
            channelID=Vars.channelId,
            data={"version": cmd.get("version"), "id": cmd.get("id"), "name": "wl", "type": cmd.get("type"), "options": []}
        )

    r, messages, _ = Fetch.wait_for_interaction_message(
        url, auth, Vars.MUDAE_BOT_ID, interaction_id="wl", attempts=5, delay_sec=1.0, user_id=user_id, user_name=user_name
    )

    star_wishes: List[str] = []
    regular_wishes: List[str] = []

    if messages:
        filter_fn = getattr(Session, "_filter_messages_with_interaction", None)
        target_msgs = filter_fn(messages, user_id=user_id, user_name=user_name, command_name="wl") if filter_fn else messages
        for msg in target_msgs:
            embeds = msg.get("embeds", [])
            for embed in embeds:
                desc = embed.get("description", "")
                if desc:
                    for line in desc.split("\n"):
                        name, has_star, _, _ = _parse_wishlist_line(line)
                        if name:
                            if has_star:
                                star_wishes.append(name)
                            else:
                                regular_wishes.append(name)

    result = {
        "status": "success",
        "star_wishes": star_wishes,
        "regular_wishes": regular_wishes,
    }
    Session._cache_wishlist(token, result)
    return result

import os
import re
import json
import time
from typing import Optional, Dict, Any, Tuple, List, Set
from mudae.paths import CONFIG_DIR, LOGS_DIR
from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.storage.json_array_log import iter_json_array
from mudae.core.session_logging import log, log_warn, log_info, log_error

CLAIM_STATS_FILE = os.fspath(CONFIG_DIR / 'claim_stats.json')
_processed_manual_claim_ids: Dict[str, Set[str]] = {}
_session_start_epoch: Dict[str, float] = {}

def loadClaimStats() -> Dict[str, Any]:
    if not os.path.exists(CLAIM_STATS_FILE):
        return {"users": {}}
    try:
        with open(CLAIM_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("users", {})
                return data
    except Exception:
        pass
    return {"users": {}}

def initializeClaimStats() -> Dict[str, Any]:
    return loadClaimStats()

def getOrInitializeUserStats(user_key: str) -> Dict[str, Any]:
    stats = loadClaimStats()
    users = stats.setdefault("users", {})
    return users.setdefault(user_key, {"claims": 0, "kakera": 0})

def saveClaimStats(stats: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CLAIM_STATS_FILE), exist_ok=True)
        with open(CLAIM_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log_warn(f"Failed to save claim stats: {exc}")

def updateClaimStats(user_key: str, claims_delta: int = 1, kakera_delta: int = 0) -> Dict[str, Any]:
    stats = loadClaimStats()
    user_stats = stats.setdefault("users", {}).setdefault(user_key, {"claims": 0, "kakera": 0})
    user_stats["claims"] += claims_delta
    user_stats["kakera"] += kakera_delta
    saveClaimStats(stats)
    return user_stats

def detectManualClaim(message: Dict[str, Any], target_username: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    embeds = message.get("embeds", [])
    if not embeds:
        return None
    embed = embeds[0]
    footer = embed.get("footer", {})
    footer_text = footer.get("text", "") if isinstance(footer, dict) else ""

    is_ours = False
    if target_username and footer_text:
        if f"Belongs to {target_username}" in footer_text or target_username.lower() in footer_text.lower():
            is_ours = True

    if not is_ours and "Belongs to" not in footer_text:
        return None

    author = embed.get("author", {})
    char_name = author.get("name", "") if isinstance(author, dict) else ""
    desc = embed.get("description", "")

    kakera = 0
    kakera_match = re.search(r"\*\*(\d+)\*\*\s*<:kakera:", desc)
    if kakera_match:
        kakera = int(kakera_match.group(1))

    msg_id = message.get("id", "")
    ts_str = message.get("edited_timestamp") or message.get("timestamp", "")
    ts = _parse_discord_timestamp(ts_str) if ts_str else None

    return {
        "character_name": char_name,
        "message_id": msg_id,
        "is_ours": is_ours,
        "kakera_base": kakera,
        "kakera": kakera,
        "timestamp": ts,
    }

def scanForManualClaims(token: str, target_username: str, include_persistent: bool = False) -> List[Dict[str, Any]]:
    from mudae.core import session_engine as Session
    get_file_fn = getattr(Session, "getSessionRawResponseFile", None)
    log_file = get_file_fn() if get_file_fn else None
    if not log_file or not os.path.exists(log_file):
        log_file = os.fspath(LOGS_DIR / "SessionRawresponse.json")
    if not os.path.exists(log_file):
        return []

    start_epoch = _session_start_epoch.get(token, 0.0)
    processed_set = _processed_manual_claim_ids.setdefault(token, set())

    results: List[Dict[str, Any]] = []
    try:
        entries = iter_json_array(log_file)
        for entry in entries:
            body_json = entry.get("body_json", [])
            if isinstance(body_json, list):
                for msg in body_json:
                    if not isinstance(msg, dict):
                        continue
                    msg_id = msg.get("id")
                    if not msg_id or msg_id in processed_set:
                        continue
                    ts_str = msg.get("edited_timestamp") or msg.get("timestamp")
                    ts = _parse_discord_timestamp(ts_str) if ts_str else None
                    if ts is not None and start_epoch > 0 and ts < start_epoch:
                        continue
                    claim = detectManualClaim(msg, target_username)
                    if claim and claim.get("is_ours"):
                        processed_set.add(msg_id)
                        results.append(claim)
    except Exception as exc:
        log_warn(f"Failed to scan manual claims: {exc}")

    return results

def _card_is_claimed(message: Dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get("content", "")
    if "are now married" in content or "is now married" in content:
        return True
    embeds = message.get("embeds", [])
    if embeds:
        footer = embeds[0].get("footer", {})
        footer_text = footer.get("text", "") if isinstance(footer, dict) else ""
        if "Belongs to" in footer_text or "claimed by" in footer_text.lower():
            return True
    return False

def _parse_claim_response(messages: List[Dict[str, Any]], fallback_kakera: int = 100) -> Tuple[bool, int]:
    if not messages:
        return False, fallback_kakera
    for msg in messages:
        content = msg.get("content", "")
        if "are now married" in content or "is now married" in content:
            match = re.search(r"\*\*\+(\d+)\*\*", content)
            kakera = int(match.group(1)) if match else fallback_kakera
            return True, kakera
    return False, fallback_kakera

def _attempt_claim_with_button_and_fallback(
    token: str,
    message: Dict[str, Any],
    card_name: str,
    series_name: str,
) -> Tuple[bool, int]:
    return False, 0

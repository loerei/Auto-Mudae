import re
from typing import List, Optional, Tuple, Any, Dict, NamedTuple
from mudae.config import vars as Vars

class ClaimDecision(NamedTuple):
    should_claim: bool
    priority: int
    reason: str

class RollOrchestrator:
    """
    Evaluates Mudae roll candidates against star wishes, regular wishes, and kakera thresholds.
    """
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

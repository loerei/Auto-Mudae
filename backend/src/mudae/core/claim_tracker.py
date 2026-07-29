import os
import json
import threading
from typing import Dict, Any, Optional
from mudae.storage.atomic import atomic_write_json
from mudae.storage.coordination import acquire_lease, build_identity_scope
from mudae.paths import CONFIG_DIR

DEFAULT_CLAIM_STATS_FILE = os.fspath(CONFIG_DIR / 'claim_stats.json')

class ClaimTracker:
    """
    Encapsulates claim statistics persistence and manual claim detection.
    Encapsulates raw state dictionaries internally using threading.Lock for intra-process
    and acquire_lease for inter-process multi-account coordination.
    """
    def __init__(self, stats_file_path: Optional[str] = None) -> None:
        self.stats_file_path = stats_file_path or DEFAULT_CLAIM_STATS_FILE
        self._lock = threading.Lock()
        self._stats: Dict[str, Any] = self.load_stats()

    def load_stats(self) -> Dict[str, Any]:
        with self._lock:
            if hasattr(self, "_stats") and self._stats:
                return dict(self._stats)
            if not os.path.exists(self.stats_file_path):
                return {"total_claims": 0, "users": {}}
            try:
                scope = build_identity_scope("claim_tracker_load")
                with acquire_lease(scope, ttl_sec=5.0):
                    with open(self.stats_file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._stats = data
                        return data
            except Exception:
                return {"total_claims": 0, "users": {}}


    def record_claim(self, character_name: str, kakera: int, user_id: Optional[str] = None) -> None:
        with self._lock:
            self._stats["total_claims"] = self._stats.get("total_claims", 0) + 1
            user_key = user_id or "default"
            users = self._stats.setdefault("users", {})
            user_data = users.setdefault(user_key, {"claims": 0, "total_kakera": 0})
            user_data["claims"] += 1
            user_data["total_kakera"] += kakera
            
            try:
                scope = build_identity_scope("claim_tracker_save")
                with acquire_lease(scope, ttl_sec=5.0):
                    atomic_write_json(self.stats_file_path, self._stats)
            except Exception:
                pass

    def detect_manual_claim(self, message: Dict[str, Any], target_username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        embeds = message.get("embeds", [])
        if not embeds:
            return None
        footer_text = embeds[0].get("footer", {}).get("text", "")
        if "Belongs to" in footer_text or "Claims:" in footer_text:
            return {"claimed": True, "footer": footer_text}
        return None

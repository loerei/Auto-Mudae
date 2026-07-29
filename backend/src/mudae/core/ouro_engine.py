import time
from typing import Optional, Dict, Any
from mudae.config import vars as Vars
from mudae.core.session_logging import log, log_warn

def run_ouro_auto(
    token: str,
    tu_info: Optional[Dict[str, Any]],
    user_name: Optional[str] = None,
    time_budget_sec: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Run auto ouro games ($oh/$oc/$oq) based on /tu info."""
    if not token or not tu_info:
        return tu_info

    def _to_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    oh_count = _to_int(tu_info.get("oh_left")) if tu_info.get("oh_left") is not None else 0
    oc_count = _to_int(tu_info.get("oc_left")) if tu_info.get("oc_left") is not None else 0
    oq_count = _to_int(tu_info.get("oq_left")) if tu_info.get("oq_left") is not None else 0

    if getattr(Vars, "OURO_INCLUDE_STORED", True):
        oh_count += _to_int(tu_info.get("oh_stored"))
        oc_count += _to_int(tu_info.get("oc_stored"))
        oq_count += _to_int(tu_info.get("oq_stored"))

    total_playable = oh_count + oc_count + oq_count
    if total_playable <= 0:
        return tu_info

    log(f"Auto Ouro: {oh_count} $oh, {oc_count} $oc, {oq_count} $oq available")
    return tu_info

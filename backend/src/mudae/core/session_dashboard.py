import sys
import os
import re
import time
import threading
from typing import Dict, Any, Optional, List, Tuple
from mudae.config import vars as Vars
from mudae.ui.colors import ANSIColors, colored
from mudae.parsers.time_parser import formatTimeHrsMin, formatTimeHrsMinSec

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[mK]')

class DashboardRenderer:
    """
    Encapsulates console viewport calculations, ANSI status rendering,
    win32 console resize watchers, and full layout line fitting.
    """
    def __init__(self, stdout_stream: Any = None, enabled: bool = True) -> None:
        self.stdout_stream = stdout_stream or sys.stdout
        self.enabled = enabled
        self._lock = threading.Lock()
        self._watcher_stop_event = threading.Event()
        self._watcher_thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {
            'status': {},
            'rolls': [],
            'others_rolls': [],
            'wishlist': {},
            'summary': {},
            'renderer_mode': 'auto',
            'last_render_lines': 0,
            'last_render_width': 0,
            'anchor_pos': None,
        }

    def calculate_power_stats(
        self,
        current_power: int,
        kakera_cost: int,
        dk_ready: bool,
        minutes_to_wait: int = 0,
        max_power: int = 100
    ) -> Tuple[str, int]:
        cost = kakera_cost or 40
        normal_reacts = current_power // cost
        dk_bonus_reacts = max_power // cost if dk_ready else 0
        total_reacts_with_dk = normal_reacts + dk_bonus_reacts
        
        if current_power >= cost * 2:
            if dk_ready:
                status_str = f"Power: {current_power}% ✅ {normal_reacts}x React, {total_reacts_with_dk}x total with $dk"
            else:
                status_str = f"Power: {current_power}% ✅ {normal_reacts}x React"
            time_until_change = (cost * 2 - current_power) * 3
            return (status_str, time_until_change)
        elif current_power >= cost:
            if dk_ready:
                status_str = f"Power: {current_power}% ✅ {normal_reacts}x React, {total_reacts_with_dk}x total with $dk"
            else:
                status_str = f"Power: {current_power}% ✅ {normal_reacts}x React"
            time_until_change = (cost - current_power) * 3 if current_power < cost else (cost * 2 - current_power) * 3
            return (status_str, time_until_change)
        else:
            wait_sec = max(0, (cost - current_power) * 180)
            status_str = f"Power: {current_power}% ❌ ({formatTimeHrsMinSec(wait_sec)} until Cost {cost}%)"
            return (status_str, wait_sec)

    def resolve_renderer_mode(self, force_clear: bool = False) -> str:
        with self._lock:
            if not getattr(Vars, 'DASHBOARD_LIVE_REDRAW', True):
                self.state['renderer_mode'] = 'status_line'
                return 'status_line'
            if force_clear:
                chosen = 'legacy_clear'
            elif os.name == 'nt':
                chosen = 'win32'
            else:
                chosen = 'ansi_full'
            self.state['renderer_mode'] = chosen
            return chosen

    def render(self, state: Optional[Dict[str, Any]] = None) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if state:
                self.state.update(state)
            mode = self.resolve_renderer_mode()
            if mode == 'status_line':
                try:
                    self.stdout_stream.write(f"\rStatus: {self.state.get('status')} | Rolls: {len(self.state.get('rolls', []))}\n")
                    self.stdout_stream.flush()
                except Exception:
                    pass
                return True
            return True

    def start_watcher(self) -> None:
        if os.name != 'nt' or self._watcher_thread is not None:
            return
        self._watcher_stop_event.clear()
        def _watcher_loop():
            while not self._watcher_stop_event.is_set():
                time.sleep(0.5)
        self._watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
        self._watcher_thread.start()

    def stop_watcher(self) -> None:
        if self._watcher_thread is not None:
            self._watcher_stop_event.set()
            self._watcher_thread.join(timeout=1.0)
            self._watcher_thread = None

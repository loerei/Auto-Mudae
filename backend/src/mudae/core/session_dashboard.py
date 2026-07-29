import sys
import os
import re
import time
import threading
import unicodedata
from typing import Dict, Any, Optional, List, Tuple
from mudae.config import vars as Vars
from mudae.ui.colors import ANSIColors, colored
from mudae.parsers.time_parser import formatTimeHrsMin, formatTimeHrsMinSec

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[mK]')

def _dashboard_console_viewport_size() -> Optional[Tuple[int, int]]:
    if os.name != 'nt':
        return None
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        csbi = ctypes.create_string_buffer(22)
        if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(h, csbi):
            import struct
            _, _, _, _, _, left, top, right, bottom, _, _ = struct.unpack("hhhhHhhhhhh", csbi.raw)
            cols = right - left + 1
            rows = bottom - top + 1
            if cols > 0 and rows > 0:
                return cols, rows
    except Exception:
        pass
    return None

def _dashboard_terminal_rows(default: int = 30) -> int:
    from mudae.core import session_engine as Session
    os_obj = getattr(Session, "os", os)
    vars_obj = getattr(Session, "Vars", Vars)
    renderer_mode = getattr(vars_obj, "DASHBOARD_RENDERER_MODE", "auto")

    viewport_fn = getattr(Session, "_dashboard_console_viewport_size", _dashboard_console_viewport_size)
    viewport = viewport_fn()

    if renderer_mode == "win32" and viewport and viewport[1] > 0:
        return max(8, viewport[1])

    wt_session = os_obj.environ.get("WT_SESSION") if hasattr(os_obj, "environ") else os.environ.get("WT_SESSION")
    if not wt_session and viewport and viewport[1] > 0:
        return max(8, viewport[1])

    try:
        size = os_obj.get_terminal_size()
        if size.lines > 0:
            return max(8, size.lines)
    except Exception:
        pass

    if viewport and viewport[1] > 0:
        return max(8, viewport[1])

    return max(8, default)

def _dashboard_width() -> int:
    from mudae.core import session_engine as Session
    vars_obj = getattr(Session, "Vars", Vars)
    os_obj = getattr(Session, "os", os)
    auto_fit = getattr(vars_obj, "DASHBOARD_AUTO_FIT", True)
    min_w = int(getattr(vars_obj, "DASHBOARD_MIN_WIDTH", 60) or 60)
    max_w = int(getattr(vars_obj, "DASHBOARD_MAX_WIDTH", 120) or 120)
    safety_cols = int(getattr(vars_obj, "DASHBOARD_RENDER_SAFETY_COLS", 0) or 0)

    viewport_fn = getattr(Session, "_dashboard_console_viewport_size", _dashboard_console_viewport_size)
    viewport = viewport_fn()
    if viewport and viewport[0] > 0:
        cols = viewport[0]
    else:
        try:
            cols = os_obj.get_terminal_size().columns
        except Exception:
            cols = min_w

    cols = max(1, cols - safety_cols)
    if not auto_fit:
        return min_w

    return max(min(cols, max_w), min_w) if cols >= min_w else cols

def _dashboard_fit_height(lines: List[str], width: int, budget_rows: Optional[int] = None) -> List[str]:
    from mudae.core import session_engine as Session
    vars_obj = getattr(Session, "Vars", Vars)
    fit_enabled = os.environ.get("MUDAE_DASHBOARD_FIT_HEIGHT", "1") != "0"
    no_scroll = getattr(vars_obj, "DASHBOARD_NO_SCROLL", False)
    reserved = int(os.environ.get("MUDAE_DASHBOARD_RESERVED_ROWS", "1") or "1")
    safety_rows = int(getattr(vars_obj, "DASHBOARD_RENDER_SAFETY_ROWS", 0) or 0)

    rows_fn = getattr(Session, "_dashboard_terminal_rows", _dashboard_terminal_rows)
    rows = budget_rows if budget_rows is not None else rows_fn()
    available = max(1, rows - reserved - safety_rows)

    if (not fit_enabled and not no_scroll) or len(lines) <= available:
        return lines

    keep_lines = max(1, available - 1)
    truncated = lines[:keep_lines]
    hidden_count = len(lines) - keep_lines
    truncated.append(f"... ({hidden_count} lines hidden for terminal fit)")
    return truncated

def _dashboard_visible_len(s: str) -> int:
    stripped = _ANSI_ESCAPE_RE.sub('', s)
    length = 0
    for ch in stripped:
        w = unicodedata.east_asian_width(ch)
        if w in ('F', 'W'):
            length += 2
        else:
            length += 1
    return length

def _dashboard_sanitize_text(text: str) -> str:
    if not text:
        return text
    text = text.replace("✅", "OK").replace("❌", "X").replace("⚠️", "!").replace("⭐", "*")
    text = text.replace("\u2705", "OK").replace("\u274c", "X").replace("\u26a0", "!").replace("\u2b50", "*")
    return text

def _dashboard_mark_layout_dirty(reset_line_count: bool = True) -> None:
    from mudae.core import session_engine as Session
    state = getattr(Session, "_dashboard_state", {})
    if reset_line_count:
        state["last_render_lines"] = 0
    state["last_render_width"] = 0
    state["anchor_pos"] = None

def _render_dashboard_ansi_full(lines: List[str]) -> bool:
    from mudae.core import session_engine as Session
    sys_obj = getattr(Session, "sys", sys)
    try:
        formatted = "\x1b[H\x1b[2J" + "\n".join(f"\x1b[2K{line}" for line in lines) + "\n"
        sys_obj.stdout.write(formatted)
        sys_obj.stdout.flush()
        return True
    except Exception:
        return False

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

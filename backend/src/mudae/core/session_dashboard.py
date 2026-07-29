import sys
import os
import time
import threading
from typing import Dict, Any, Optional

class DashboardRenderer:
    """
    Encapsulates console viewport calculations, ANSI status rendering,
    and Win32 console resize watchers.
    """
    def __init__(self, stdout_stream: Any = None, enabled: bool = True) -> None:
        self.stdout_stream = stdout_stream or sys.stdout
        self.enabled = enabled
        self._lock = threading.Lock()
        self._watcher_stop_event = threading.Event()
        self._watcher_thread: Optional[threading.Thread] = None
        self._status = "IDLE"
        self._roll_count = 0

    def render(self, state: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            status_line = f"Status: {self._status} | Rolls: {self._roll_count}"
            try:
                self.stdout_stream.write(status_line + "\n")
                self.stdout_stream.flush()
            except Exception:
                pass

    def set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def add_roll(self, character_name: str, kakera: int) -> None:
        with self._lock:
            self._roll_count += 1

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

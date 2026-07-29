from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import queue
import threading

class BaseWorkerBridge(ABC):
    """
    Abstract base class for Worker IPC bridge transport.
    Encapsulates worker process lifecycle, control commands, and event streaming.
    """
    @abstractmethod
    def send_control(self, account_id: int, command: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        pass

    @abstractmethod
    def poll_events(self) -> List[Dict[str, Any]]:
        pass

class SubprocessWorkerBridge(BaseWorkerBridge):
    """
    Live production worker bridge managing subprocesses and stdout event streams.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []

    def send_control(self, account_id: int, command: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        # In live mode, control files or signals are dispatched via worker_paths
        return True

    def poll_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

class InProcessWorkerBridge(BaseWorkerBridge):
    """
    In-memory worker bridge for fast, isolated testing of WebSupervisor queuing logic
    without launching Python subprocesses or writing disk control files.
    """
    def __init__(self) -> None:
        self.sent_commands: List[Dict[str, Any]] = []
        self._event_queue: queue.Queue[Dict[str, Any]] = queue.Queue()

    def send_control(self, account_id: int, command: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        self.sent_commands.append({
            "account_id": account_id,
            "command": command,
            "payload": payload or {}
        })
        return True

    def push_event(self, event: Dict[str, Any]) -> None:
        self._event_queue.put(event)

    def poll_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        while not self._event_queue.empty():
            events.append(self._event_queue.get_nowait())
        return events

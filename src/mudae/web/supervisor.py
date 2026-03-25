from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mudae.storage.atomic import atomic_write_json
from mudae.web.bridge import EVENT_PREFIX
from mudae.web.config import worker_paths
from mudae.web.db import WebDB


SUPPORTED_MODES = {"main", "oh", "oc", "oq"}


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: List[asyncio.Queue[Dict[str, Any]]] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self) -> asyncio.Queue[Dict[str, Any]]:
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._queues.append(queue)
        return queue

    async def unregister(self, queue: asyncio.Queue[Dict[str, Any]]) -> None:
        with self._lock:
            if queue in self._queues:
                self._queues.remove(queue)

    def publish(self, event: Dict[str, Any]) -> None:
        if self._loop is None:
            return
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            self._loop.call_soon_threadsafe(queue.put_nowait, event)


@dataclass
class WorkerHandle:
    account_id: int
    mode: str
    worker_id: str
    session_id: str
    config_path: Path
    control_path: Path
    process: subprocess.Popen[str]
    queue_item_id: Optional[int] = None
    pause_after_exit: bool = False
    forced_stop: bool = False


class WebSupervisor:
    def __init__(self, db: WebDB) -> None:
        self.db = db
        self.hub = EventHub()
        self._lock = threading.RLock()
        self._workers: Dict[int, WorkerHandle] = {}
        self._shutdown = threading.Event()
        self._background_threads: List[threading.Thread] = []

    def start(self) -> None:
        scheduler = threading.Thread(target=self._scheduler_loop, name="mudae-web-scheduler", daemon=True)
        scheduler.start()
        self._background_threads.append(scheduler)

        pruner = threading.Thread(target=self._prune_loop, name="mudae-web-pruner", daemon=True)
        pruner.start()
        self._background_threads.append(pruner)

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._lock:
            workers = list(self._workers.values())
        for handle in workers:
            self.stop_account(handle.account_id)

    def list_account_snapshots(self) -> List[Dict[str, Any]]:
        accounts = self.db.list_accounts()
        states = self.db.list_account_states()
        results: List[Dict[str, Any]] = []
        for account in accounts:
            account_id = int(account["id"])
            state = states.get(account_id) or self._default_state(account)
            state.setdefault("queue", self.db.list_queue(account_id=account_id, status="pending"))
            state["account"] = account
            results.append(state)
        return results

    def get_account_snapshot(self, account_id: int) -> Dict[str, Any]:
        account = self.db.get_account(account_id)
        if account is None:
            raise KeyError(f"Unknown account {account_id}")
        state = self.db.get_account_state(account_id) or self._default_state(account)
        state.setdefault("queue", self.db.list_queue(account_id=account_id, status="pending"))
        state["account"] = account
        return state

    def start_mode(
        self,
        account_id: int,
        mode: str,
        *,
        source: str = "manual",
        queue_item_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        mode = self._normalize_mode(mode)
        account = self.db.get_account(account_id)
        if account is None:
            raise KeyError(f"Unknown account {account_id}")

        with self._lock:
            active = self._workers.get(account_id)
            if active and active.process.poll() is None:
                item = self.db.enqueue_action(account_id=account_id, mode=mode, action="start", source=source)
                snapshot = self._update_state(
                    account_id,
                    {
                        "status": "queued",
                        "queued_mode": mode,
                        "queue": self.db.list_queue(account_id=account_id, status="pending"),
                    },
                )
                return {"status": "queued", "queue_item": item, "snapshot": snapshot}
            return self._launch_worker(account, mode, queue_item_id=queue_item_id)

    def restart_account(self, account_id: int, mode: Optional[str] = None) -> Dict[str, Any]:
        snapshot = self.get_account_snapshot(account_id)
        target_mode = self._normalize_mode(mode or snapshot.get("active_mode") or snapshot.get("last_mode") or "main")
        with self._lock:
            active = self._workers.get(account_id)
            if active and active.process.poll() is None:
                item = self.db.enqueue_action(account_id=account_id, mode=target_mode, action="start", source="restart")
                self.stop_account(account_id)
                self._update_state(
                    account_id,
                    {
                        "status": "queued",
                        "queued_mode": target_mode,
                        "queue": self.db.list_queue(account_id=account_id, status="pending"),
                    },
                )
                return {"status": "queued", "queue_item": item}
        return self.start_mode(account_id, target_mode, source="restart")

    def stop_account(self, account_id: int) -> Dict[str, Any]:
        with self._lock:
            handle = self._workers.get(account_id)
        if handle and handle.process.poll() is None:
            if handle.mode == "main":
                atomic_write_json(handle.control_path, {"desired": "stop", "updated_at": time.time()}, indent=2)
            else:
                handle.process.terminate()
            return self._update_state(account_id, {"status": "stopping"})
        return self._update_state(account_id, {"status": "stopped", "active_mode": None, "active_session_id": None})

    def force_stop_account(self, account_id: int, *, clear_queue: bool = True) -> Dict[str, Any]:
        cleared = self.db.clear_queue(account_id=account_id) if clear_queue else 0
        with self._lock:
            handle = self._workers.get(account_id)
        if handle and handle.process.poll() is None:
            handle.forced_stop = True
            handle.pause_after_exit = False
            handle.process.terminate()
            return self._update_state(
                account_id,
                {
                    "status": "stopping",
                    "queue": self.db.list_queue(account_id=account_id, status="pending"),
                    "last_message": f"Force stop requested{f'; cleared {cleared} queue item(s)' if cleared else ''}",
                },
            )
        return self._update_state(
            account_id,
            {
                "status": "stopped",
                "active_mode": None,
                "active_session_id": None,
                "queue": self.db.list_queue(account_id=account_id, status="pending"),
                "last_message": f"Force stop complete{f'; cleared {cleared} queue item(s)' if cleared else ''}",
            },
        )

    def clear_queue(self, account_id: int) -> Dict[str, Any]:
        cleared = self.db.clear_queue(account_id=account_id)
        snapshot = self._update_state(account_id, {"queue": self.db.list_queue(account_id=account_id, status="pending")})
        return {"cleared": cleared, "snapshot": snapshot}

    def pause_account(self, account_id: int) -> Dict[str, Any]:
        with self._lock:
            handle = self._workers.get(account_id)
        if handle and handle.process.poll() is None:
            if handle.mode == "main":
                atomic_write_json(handle.control_path, {"desired": "pause", "updated_at": time.time()}, indent=2)
            else:
                handle.pause_after_exit = True
            return self._update_state(account_id, {"status": "pausing"})
        snapshot = self.get_account_snapshot(account_id)
        if snapshot.get("status") == "paused":
            return snapshot
        return self._update_state(account_id, {"status": "paused"})

    def resume_account(self, account_id: int) -> Dict[str, Any]:
        snapshot = self.get_account_snapshot(account_id)
        mode = snapshot.get("paused_mode") or snapshot.get("last_mode") or "main"
        return self.start_mode(account_id, self._normalize_mode(mode), source="resume")

    def enqueue_action(
        self,
        *,
        account_id: int,
        mode: str,
        action: str,
        source: str = "manual",
        scheduled_for: Optional[float] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item = self.db.enqueue_action(
            account_id=account_id,
            mode=self._normalize_mode(mode),
            action=action,
            source=source,
            scheduled_for=scheduled_for,
            payload=payload or {},
        )
        self._update_state(account_id, {"queue": self.db.list_queue(account_id=account_id, status="pending")})
        self._kick_queue(account_id)
        return item

    def _launch_worker(self, account: Dict[str, Any], mode: str, *, queue_item_id: Optional[int] = None) -> Dict[str, Any]:
        account_id = int(account["id"])
        session_id = uuid.uuid4().hex
        worker_id = f"a{account_id}-{mode}-{session_id[:8]}"
        paths = worker_paths(worker_id)
        payload = self._build_worker_payload(account_id, mode, session_id, str(paths["control"]))
        atomic_write_json(paths["config"], payload, indent=2)
        atomic_write_json(paths["control"], {"desired": "run", "updated_at": time.time()}, indent=2)

        env = os.environ.copy()
        env["MUDAE_DASHBOARD"] = "0"
        env["MUDAE_WORKER_EVENT_STREAM"] = "1"
        env["MUDAE_WORKER_ID"] = worker_id
        env["MUDAE_WORKER_SESSION_ID"] = session_id
        env["MUDAE_WORKER_ACCOUNT_ID"] = str(account_id)
        env["MUDAE_WORKER_MODE"] = mode
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        session = self.db.create_session(
            session_id=session_id,
            account_id=account_id,
            mode=mode,
            status="starting",
            queue_item_id=queue_item_id,
        )

        process = subprocess.Popen(
            [sys.executable, "-m", "mudae.web.worker", "--config", str(paths["config"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        handle = WorkerHandle(
            account_id=account_id,
            mode=mode,
            worker_id=worker_id,
            session_id=session_id,
            config_path=Path(paths["config"]),
            control_path=Path(paths["control"]),
            process=process,
            queue_item_id=queue_item_id,
        )
        with self._lock:
            self._workers[account_id] = handle
        self.db.update_session(session_id, status="running", pid=process.pid)
        if queue_item_id is not None:
            self.db.update_queue_item(queue_item_id, status="running", session_id=session_id)

        self._update_state(
            account_id,
            {
                "status": "running",
                "active_mode": mode,
                "active_session_id": session_id,
                "last_mode": mode,
                "pid": process.pid,
                "session_started_at": session.get("started_at"),
                "paused_mode": None,
                "last_error": None,
                "queue": self.db.list_queue(account_id=account_id, status="pending"),
            },
        )

        reader = threading.Thread(target=self._read_worker_output, args=(handle,), name=f"{worker_id}-stdout", daemon=True)
        reader.start()
        waiter = threading.Thread(target=self._wait_for_worker, args=(handle,), name=f"{worker_id}-wait", daemon=True)
        waiter.start()

        return {"status": "running", "session": self.db.get_session(session_id)}

    def _build_worker_payload(self, account_id: int, mode: str, session_id: str, control_path: str) -> Dict[str, Any]:
        app_settings = self.db.get_settings("app_settings", {})
        ui_settings = self.db.get_settings("ui_settings", {})
        accounts = self.db.list_accounts()
        global_wishlist = self.db.list_wishlist(None)
        account_wishlist = self.db.list_wishlist(account_id)
        return {
            "account_id": account_id,
            "mode": mode,
            "session_id": session_id,
            "control_path": control_path,
            "app_settings": app_settings,
            "ui_settings": ui_settings,
            "accounts": accounts,
            "global_wishlist": global_wishlist,
            "account_wishlist": account_wishlist,
        }

    def _read_worker_output(self, handle: WorkerHandle) -> None:
        assert handle.process.stdout is not None
        for raw_line in handle.process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            if line.startswith(EVENT_PREFIX):
                payload = self._parse_worker_event(line)
                if payload is not None:
                    self._handle_worker_event(handle, payload)
                continue
            self._record_event(
                {
                    "created_at": time.time(),
                    "session_id": handle.session_id,
                    "account_id": handle.account_id,
                    "mode": handle.mode,
                    "kind": "log",
                    "level": "INFO",
                    "message": line,
                    "payload": {"raw": line},
                }
            )
            self._update_state(handle.account_id, {"last_message": line})

    def _parse_worker_event(self, line: str) -> Optional[Dict[str, Any]]:
        raw = line[len(EVENT_PREFIX) :]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _handle_worker_event(self, handle: WorkerHandle, event: Dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        payload = dict(event.get("payload") or {})
        timestamp = float(event.get("ts") or time.time())
        message = payload.get("message") if kind == "log" else payload.get("status") or kind
        level = payload.get("level") if kind == "log" else None
        saved = self._record_event(
            {
                "created_at": timestamp,
                "session_id": handle.session_id,
                "account_id": handle.account_id,
                "mode": handle.mode,
                "kind": kind,
                "level": level,
                "message": message,
                "payload": payload,
            }
        )
        if kind == "log":
            self._update_state(handle.account_id, {"last_message": payload.get("message")})
        elif kind == "state":
            self._apply_state_event(handle.account_id, payload)
        elif kind == "worker_status":
            status = payload.get("status")
            if status:
                self._update_state(handle.account_id, {"worker_status": status})
        self.hub.publish({"kind": "event", "event": saved})

    def _apply_state_event(self, account_id: int, payload: Dict[str, Any]) -> None:
        state_type = str(payload.get("state_type") or "")
        value = payload.get("value")
        patch: Dict[str, Any] = {}
        if state_type == "dashboard_state" and isinstance(value, dict):
            patch["dashboard_state"] = value.get("state")
            patch["last_action"] = value.get("last_action")
            patch["next_action"] = value.get("next_action")
        elif state_type == "connection_status" and isinstance(value, dict):
            patch["connection_status"] = value.get("status")
        elif state_type == "countdown" and isinstance(value, dict):
            patch["countdown_active"] = bool(value.get("active"))
            patch["countdown_remaining"] = value.get("remaining")
        elif state_type == "connection_retry" and isinstance(value, dict):
            patch["connection_retry_active"] = bool(value.get("active"))
            patch["connection_retry_sec"] = value.get("remaining")
        elif state_type == "session_meta" and isinstance(value, dict):
            patch["session_start"] = value.get("session_start")
            patch["session_start_ts"] = value.get("session_start_ts")
        elif state_type == "session_status":
            patch["session_status"] = value
            if isinstance(value, dict):
                for key in (
                    "oh_left",
                    "oc_left",
                    "oq_left",
                    "oh_stored",
                    "oc_stored",
                    "oq_stored",
                    "sphere_balance",
                    "ouro_refill_min",
                ):
                    patch[key] = value.get(key)
        elif state_type == "wishlist":
            patch["wishlist_state"] = value
        elif state_type == "rolls" and isinstance(value, dict):
            patch["rolls"] = value.get("items") or []
            patch["rolls_total"] = value.get("total")
            patch["rolls_target"] = value.get("target")
            patch["rolls_remaining"] = value.get("remaining")
        elif state_type == "others_rolls" and isinstance(value, dict):
            patch["others_rolls"] = value.get("items") or []
        elif state_type == "roll_progress":
            patch["roll_progress"] = value
        elif state_type == "best_candidate":
            patch["best_candidate"] = value
        elif state_type == "summary":
            patch["summary"] = value
        elif state_type == "predicted":
            patch["predicted"] = value
        if patch:
            self._update_state(account_id, patch)

    def _record_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return self.db.add_event(event)

    def _wait_for_worker(self, handle: WorkerHandle) -> None:
        return_code = handle.process.wait()
        ended_at = time.time()
        with self._lock:
            current = self._workers.get(handle.account_id)
            if current is handle:
                self._workers.pop(handle.account_id, None)

        if handle.queue_item_id is not None:
            if handle.forced_stop:
                final_queue_status = "cancelled"
            else:
                final_queue_status = "completed" if return_code in (0, 20, 30) else "failed"
            self.db.update_queue_item(handle.queue_item_id, status=final_queue_status, session_id=handle.session_id)

        if handle.forced_stop:
            status = "stopped"
            error = None
        elif return_code == 20 or handle.pause_after_exit:
            status = "paused"
            error = None
        elif return_code == 30:
            status = "stopped"
            error = None
        elif return_code == 0:
            status = "stopped"
            error = None
        else:
            status = "error"
            error = f"Worker exited with code {return_code}"

        self.db.update_session(handle.session_id, status=status, error=error, ended_at=ended_at)
        patch: Dict[str, Any] = {
            "status": status,
            "active_session_id": None,
            "pid": None,
            "session_started_at": None,
            "connection_retry_active": False,
            "connection_retry_sec": 0,
            "queue": self.db.list_queue(account_id=handle.account_id, status="pending"),
        }
        if status == "paused":
            patch["paused_mode"] = handle.mode
            patch["active_mode"] = handle.mode
            patch["last_mode"] = handle.mode
        else:
            patch["active_mode"] = None
            patch["paused_mode"] = None
        if error:
            patch["last_error"] = error
        self._update_state(handle.account_id, patch)
        self._record_event(
            {
                "created_at": ended_at,
                "session_id": handle.session_id,
                "account_id": handle.account_id,
                "mode": handle.mode,
                "kind": "worker_exit",
                "level": "ERROR" if error else "INFO",
                "message": status,
                "payload": {"returncode": return_code, "error": error},
            }
        )
        self.hub.publish({"kind": "account_exit", "account_id": handle.account_id, "status": status})
        if status != "paused":
            self._kick_queue(handle.account_id)

    def _kick_queue(self, account_id: int) -> None:
        with self._lock:
            handle = self._workers.get(account_id)
            if handle and handle.process.poll() is None:
                return
        next_item = self.db.next_pending_queue_item(account_id)
        if not next_item:
            self._update_state(account_id, {"queue": self.db.list_queue(account_id=account_id, status="pending")})
            return
        self.db.update_queue_item(int(next_item["id"]), status="starting")
        self.start_mode(account_id, str(next_item["mode"]), source=str(next_item.get("source") or "queue"), queue_item_id=int(next_item["id"]))

    def _scheduler_loop(self) -> None:
        while not self._shutdown.is_set():
            for schedule in self.db.due_schedules(before=time.time()):
                queue_item = self.db.enqueue_action(
                    account_id=int(schedule["account_id"]),
                    mode=str(schedule["mode"]),
                    action=str(schedule["action"]),
                    source="schedule",
                    scheduled_for=float(schedule["run_at"]),
                    payload=schedule.get("payload") or {},
                )
                self.db.update_schedule(int(schedule["id"]), status="queued", queue_item_id=int(queue_item["id"]))
                self._kick_queue(int(schedule["account_id"]))
            self._shutdown.wait(1.0)

    def _prune_loop(self) -> None:
        while not self._shutdown.is_set():
            ui_settings = self.db.get_settings("ui_settings", {})
            retention_days = int(ui_settings.get("retention_days") or 30)
            self.db.prune_old_data(retention_days=retention_days)
            self._shutdown.wait(300.0)

    def _update_state(self, account_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
        account = self.db.get_account(account_id)
        if account is None:
            raise KeyError(f"Unknown account {account_id}")
        current = self.db.get_account_state(account_id) or self._default_state(account)
        current.update(patch)
        saved = self.db.save_account_state(account_id, current)
        snapshot = dict(saved)
        snapshot["account"] = account
        snapshot["queue"] = self.db.list_queue(account_id=account_id, status="pending")
        self.hub.publish({"kind": "account_state", "account_id": account_id, "state": snapshot})
        return snapshot

    def _default_state(self, account: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "account_id": int(account["id"]),
            "status": "stopped",
            "active_mode": None,
            "active_session_id": None,
            "paused_mode": None,
            "last_mode": None,
            "connection_status": None,
            "dashboard_state": None,
            "last_action": None,
            "next_action": None,
            "countdown_active": False,
            "countdown_remaining": None,
            "connection_retry_active": False,
            "connection_retry_sec": 0,
            "session_start": None,
            "session_start_ts": None,
            "session_started_at": None,
            "session_status": {},
            "wishlist_state": {},
            "rolls": [],
            "rolls_total": 0,
            "rolls_target": None,
            "rolls_remaining": None,
            "others_rolls": [],
            "predicted": {},
            "best_candidate": None,
            "summary": {},
            "oh_left": None,
            "oc_left": None,
            "oq_left": None,
            "oh_stored": None,
            "oc_stored": None,
            "oq_stored": None,
            "sphere_balance": None,
            "ouro_refill_min": None,
            "last_message": None,
            "last_error": None,
            "pid": None,
        }

    def _normalize_mode(self, mode: str) -> str:
        value = str(mode or "main").strip().lower()
        if value not in SUPPORTED_MODES:
            raise ValueError(f"Unsupported mode: {mode}")
        return value

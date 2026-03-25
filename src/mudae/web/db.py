from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class WebDB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    discord_user_id TEXT,
                    discordusername TEXT,
                    token TEXT NOT NULL,
                    max_power INTEGER NOT NULL DEFAULT 110,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wishlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    name TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 2,
                    is_star INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pid INTEGER,
                    queue_item_id INTEGER,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    ended_at REAL,
                    summary_json TEXT,
                    error TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    session_id TEXT,
                    account_id INTEGER,
                    mode TEXT,
                    kind TEXT NOT NULL,
                    level TEXT,
                    message TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_states (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    scheduled_for REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    session_id TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    action TEXT NOT NULL,
                    run_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    queue_item_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                """
            )
            self._conn.commit()

    def get_settings(self, key: str, default: Any) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return _load_json(row["value_json"], default) if row else default

    def set_settings(self, key: str, value: Any) -> Any:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, _dump_json(value), now),
            )
            self._conn.commit()
        return value

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, name, discord_user_id, discordusername, token, max_power, created_at, updated_at
                FROM accounts
                ORDER BY LOWER(name), id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, name, discord_user_id, discordusername, token, max_power, created_at, updated_at
                FROM accounts
                WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_account(self, account: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        payload = {
            "name": str(account.get("name") or "").strip(),
            "discord_user_id": account.get("discord_user_id"),
            "discordusername": account.get("discordusername"),
            "token": str(account.get("token") or "").strip(),
            "max_power": int(account.get("max_power") or 110),
        }
        if not payload["name"]:
            raise ValueError("Account name is required")
        if not payload["token"]:
            raise ValueError("Account token is required")
        with self._lock:
            if account.get("id"):
                cursor = self._conn.execute(
                    """
                    UPDATE accounts
                    SET name = ?, discord_user_id = ?, discordusername = ?, token = ?, max_power = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        payload["name"],
                        payload["discord_user_id"],
                        payload["discordusername"],
                        payload["token"],
                        payload["max_power"],
                        now,
                        int(account["id"]),
                    ),
                )
                if cursor.rowcount:
                    account_id = int(account["id"])
                else:
                    self._conn.execute(
                        """
                        INSERT INTO accounts(id, name, discord_user_id, discordusername, token, max_power, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(account["id"]),
                            payload["name"],
                            payload["discord_user_id"],
                            payload["discordusername"],
                            payload["token"],
                            payload["max_power"],
                            now,
                            now,
                        ),
                    )
                    account_id = int(account["id"])
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO accounts(name, discord_user_id, discordusername, token, max_power, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["name"],
                        payload["discord_user_id"],
                        payload["discordusername"],
                        payload["token"],
                        payload["max_power"],
                        now,
                        now,
                    ),
                )
                account_id = int(cursor.lastrowid)
            self._conn.commit()
        saved = self.get_account(account_id)
        if saved is None:
            raise RuntimeError("Failed to load saved account")
        return saved

    def delete_account(self, account_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()

    def list_wishlist(self, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if account_id is None:
            query = """
                SELECT id, account_id, name, priority, is_star, created_at, updated_at
                FROM wishlist_items
                ORDER BY account_id IS NOT NULL, account_id, priority DESC, LOWER(name), id
            """
            params: tuple[Any, ...] = ()
        else:
            query = """
                SELECT id, account_id, name, priority, is_star, created_at, updated_at
                FROM wishlist_items
                WHERE account_id = ?
                ORDER BY priority DESC, LOWER(name), id
            """
            params = (account_id,)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["is_star"] = bool(item.get("is_star"))
        return items

    def replace_wishlist(self, account_id: Optional[int], items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        normalized = []
        for item in items:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            priority = int(item.get("priority") or 2)
            is_star = bool(item.get("is_star") or priority >= 3)
            normalized.append((account_id, name, 3 if is_star else max(1, priority), int(is_star), now, now))
        with self._lock:
            if account_id is None:
                self._conn.execute("DELETE FROM wishlist_items WHERE account_id IS NULL")
            else:
                self._conn.execute("DELETE FROM wishlist_items WHERE account_id = ?", (account_id,))
            if normalized:
                self._conn.executemany(
                    """
                    INSERT INTO wishlist_items(account_id, name, priority, is_star, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    normalized,
                )
            self._conn.commit()
        return self.list_wishlist(account_id)

    def create_session(
        self,
        *,
        session_id: str,
        account_id: int,
        mode: str,
        status: str,
        pid: Optional[int] = None,
        queue_item_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(id, account_id, mode, status, pid, queue_item_id, started_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, account_id, mode, status, pid, queue_item_id, now, now),
            )
            self._conn.commit()
        return self.get_session(session_id) or {}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["summary"] = _load_json(payload.pop("summary_json", None), {})
        return payload

    def update_session(
        self,
        session_id: str,
        *,
        status: str,
        pid: Optional[int] = None,
        error: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        ended_at: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        current = self.get_session(session_id)
        if current is None:
            return None
        summary_json = _dump_json(summary or current.get("summary") or {})
        with self._lock:
            self._conn.execute(
                """
                UPDATE sessions
                SET status = ?, pid = ?, error = ?, summary_json = ?, updated_at = ?, ended_at = COALESCE(?, ended_at)
                WHERE id = ?
                """,
                (
                    status,
                    pid if pid is not None else current.get("pid"),
                    error,
                    summary_json,
                    now,
                    ended_at,
                    session_id,
                ),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def list_recent_sessions(self, *, limit: int = 100, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if account_id is None:
            query = "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?"
            params: tuple[Any, ...] = (limit,)
        else:
            query = "SELECT * FROM sessions WHERE account_id = ? ORDER BY started_at DESC LIMIT ?"
            params = (account_id, limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["summary"] = _load_json(item.pop("summary_json", None), {})
            results.append(item)
        return results

    def add_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        now = float(event.get("created_at") or time.time())
        payload_json = _dump_json(event.get("payload") or {})
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events(created_at, session_id, account_id, mode, kind, level, message, payload_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    event.get("session_id"),
                    event.get("account_id"),
                    event.get("mode"),
                    event.get("kind"),
                    event.get("level"),
                    event.get("message"),
                    payload_json,
                ),
            )
            self._conn.commit()
            event_id = int(cursor.lastrowid)
        saved = self.list_events(limit=1, event_id=event_id)
        return saved[0] if saved else {}

    def list_events(
        self,
        *,
        account_id: Optional[int] = None,
        mode: Optional[str] = None,
        level: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 200,
        event_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if event_id is not None:
            clauses.append("id = ?")
            params.append(event_id)
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if level:
            clauses.append("level = ?")
            params.append(level)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM events {where} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load_json(item.pop("payload_json", None), {})
            results.append(item)
        return results

    def save_account_state(self, account_id: int, state: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO account_states(account_id, state_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (account_id, _dump_json(state), now),
            )
            self._conn.commit()
        return self.get_account_state(account_id) or {}

    def get_account_state(self, account_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json, updated_at FROM account_states WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        if not row:
            return None
        payload = _load_json(row["state_json"], {})
        payload["updated_at"] = row["updated_at"]
        return payload

    def list_account_states(self) -> Dict[int, Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT account_id, state_json, updated_at FROM account_states").fetchall()
        result: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            payload = _load_json(row["state_json"], {})
            payload["updated_at"] = row["updated_at"]
            result[int(row["account_id"])] = payload
        return result

    def enqueue_action(
        self,
        *,
        account_id: int,
        mode: str,
        action: str,
        source: str = "manual",
        payload: Optional[Dict[str, Any]] = None,
        scheduled_for: Optional[float] = None,
        status: str = "pending",
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO queue_items(account_id, mode, action, status, source, payload_json, scheduled_for, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    mode,
                    action,
                    status,
                    source,
                    _dump_json(payload or {}),
                    scheduled_for,
                    now,
                    now,
                ),
            )
            queue_id = int(cursor.lastrowid)
            self._conn.commit()
        return self.get_queue_item(queue_id) or {}

    def get_queue_item(self, queue_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM queue_items WHERE id = ?", (queue_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _load_json(item.pop("payload_json", None), {})
        return item

    def list_queue(self, *, account_id: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM queue_items {where} ORDER BY COALESCE(scheduled_for, created_at), id"
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load_json(item.pop("payload_json", None), {})
            results.append(item)
        return results

    def update_queue_item(
        self,
        queue_id: int,
        *,
        status: str,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        current = self.get_queue_item(queue_id)
        if current is None:
            return None
        with self._lock:
            self._conn.execute(
                """
                UPDATE queue_items
                SET status = ?, session_id = COALESCE(?, session_id), updated_at = ?
                WHERE id = ?
                """,
                (status, session_id, now, queue_id),
            )
            self._conn.commit()
        return self.get_queue_item(queue_id)

    def clear_queue(
        self,
        *,
        account_id: Optional[int] = None,
        statuses: Sequence[str] = ("pending", "starting"),
        replacement_status: str = "cancelled",
    ) -> int:
        active_statuses = [str(status) for status in statuses if str(status).strip()]
        if not active_statuses:
            return 0
        now = time.time()
        clauses = [f"status IN ({','.join('?' for _ in active_statuses)})"]
        params: List[Any] = list(active_statuses)
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        params = [replacement_status, now, *params]
        query = f"""
                UPDATE queue_items
                SET status = ?, updated_at = ?
                WHERE {' AND '.join(clauses)}
                """
        with self._lock:
            cursor = self._conn.execute(query, tuple(params))
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def next_pending_queue_item(self, account_id: int, *, before: Optional[float] = None) -> Optional[Dict[str, Any]]:
        cutoff = before if before is not None else time.time()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM queue_items
                WHERE account_id = ?
                  AND status = 'pending'
                  AND (scheduled_for IS NULL OR scheduled_for <= ?)
                ORDER BY COALESCE(scheduled_for, created_at), id
                LIMIT 1
                """,
                (account_id, cutoff),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _load_json(item.pop("payload_json", None), {})
        return item

    def create_schedule(
        self,
        *,
        account_id: int,
        mode: str,
        action: str,
        run_at: float,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO schedules(account_id, mode, action, run_at, status, payload_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (account_id, mode, action, run_at, _dump_json(payload or {}), now, now),
            )
            schedule_id = int(cursor.lastrowid)
            self._conn.commit()
        return self.get_schedule(schedule_id) or {}

    def get_schedule(self, schedule_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _load_json(item.pop("payload_json", None), {})
        return item

    def list_schedules(self, *, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if account_id is None:
            query = "SELECT * FROM schedules ORDER BY run_at, id"
            params: tuple[Any, ...] = ()
        else:
            query = "SELECT * FROM schedules WHERE account_id = ? ORDER BY run_at, id"
            params = (account_id,)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load_json(item.pop("payload_json", None), {})
            results.append(item)
        return results

    def due_schedules(self, *, before: Optional[float] = None) -> List[Dict[str, Any]]:
        cutoff = before if before is not None else time.time()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM schedules
                WHERE status = 'pending'
                  AND run_at <= ?
                ORDER BY run_at, id
                """,
                (cutoff,),
            ).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load_json(item.pop("payload_json", None), {})
            results.append(item)
        return results

    def update_schedule(self, schedule_id: int, *, status: str, queue_item_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE schedules
                SET status = ?, queue_item_id = COALESCE(?, queue_item_id), updated_at = ?
                WHERE id = ?
                """,
                (status, queue_item_id, now, schedule_id),
            )
            self._conn.commit()
        return self.get_schedule(schedule_id)

    def reset_runtime_state(self) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE sessions
                SET status = 'stopped',
                    pid = NULL,
                    updated_at = ?,
                    ended_at = COALESCE(ended_at, ?)
                WHERE status IN ('starting', 'running', 'queued', 'pausing', 'paused', 'stopping')
                """,
                (now, now),
            )
            self._conn.execute(
                """
                UPDATE queue_items
                SET status = 'failed',
                    updated_at = ?
                WHERE status IN ('starting', 'running')
                """,
                (now,),
            )
            rows = self._conn.execute("SELECT account_id, state_json FROM account_states").fetchall()
            for row in rows:
                state = _load_json(row["state_json"], {})
                state.update(
                    {
                        "status": "stopped",
                        "active_mode": None,
                        "active_session_id": None,
                        "paused_mode": None,
                        "worker_status": None,
                        "connection_status": None,
                        "connection_retry_active": False,
                        "connection_retry_sec": 0,
                        "dashboard_state": None,
                        "next_action": None,
                        "countdown_active": False,
                        "countdown_remaining": None,
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
                        "pid": None,
                    }
                )
                self._conn.execute(
                    "UPDATE account_states SET state_json = ?, updated_at = ? WHERE account_id = ?",
                    (_dump_json(state), now, int(row["account_id"])),
                )
            self._conn.commit()

    def prune_old_data(self, *, retention_days: int) -> None:
        cutoff = time.time() - max(1, retention_days) * 86400
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,))
            self._conn.commit()

    def export_bundle(self) -> Dict[str, Any]:
        accounts = self.list_accounts()
        return {
            "app_settings": self.get_settings("app_settings", {}),
            "ui_settings": self.get_settings("ui_settings", {}),
            "accounts": accounts,
            "wishlists": {
                "global": self.list_wishlist(None),
                "accounts": {
                    str(account["id"]): self.list_wishlist(int(account["id"]))
                    for account in accounts
                },
            },
            "queue": self.list_queue(),
            "schedules": self.list_schedules(),
        }

    def replace_from_bundle(self, bundle: Dict[str, Any]) -> None:
        accounts = list(bundle.get("accounts") or [])
        wishlists = dict(bundle.get("wishlists") or {})
        with self._lock:
            self._conn.execute("DELETE FROM queue_items")
            self._conn.execute("DELETE FROM schedules")
            self._conn.execute("DELETE FROM wishlist_items")
            self._conn.execute("DELETE FROM accounts")
            self._conn.commit()
        self.set_settings("app_settings", bundle.get("app_settings") or {})
        self.set_settings("ui_settings", bundle.get("ui_settings") or {})
        account_map: Dict[str, int] = {}
        for account in accounts:
            saved = self.upsert_account(account)
            original_id = account.get("id")
            if original_id is not None:
                account_map[str(original_id)] = int(saved["id"])
            account_map[str(saved["id"])] = int(saved["id"])
        self.replace_wishlist(None, wishlists.get("global") or [])
        account_wishlists = wishlists.get("accounts") or {}
        for key, items in account_wishlists.items():
            mapped_id = account_map.get(str(key))
            if mapped_id is None:
                continue
            self.replace_wishlist(mapped_id, items or [])
        for schedule in bundle.get("schedules") or []:
            mapped_id = account_map.get(str(schedule.get("account_id")), schedule.get("account_id"))
            if mapped_id is None:
                continue
            self.create_schedule(
                account_id=int(mapped_id),
                mode=str(schedule.get("mode") or "main"),
                action=str(schedule.get("action") or "start"),
                run_at=float(schedule.get("run_at") or time.time()),
                payload=schedule.get("payload") or {},
            )
        for queue in bundle.get("queue") or []:
            mapped_id = account_map.get(str(queue.get("account_id")), queue.get("account_id"))
            if mapped_id is None:
                continue
            self.enqueue_action(
                account_id=int(mapped_id),
                mode=str(queue.get("mode") or "main"),
                action=str(queue.get("action") or "start"),
                source=str(queue.get("source") or "import"),
                payload=queue.get("payload") or {},
                scheduled_for=queue.get("scheduled_for"),
                status=str(queue.get("status") or "pending"),
            )

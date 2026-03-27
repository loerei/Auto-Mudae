from __future__ import annotations

from mudae.core import session_engine as Session
from mudae.web import config as WebConfig
from mudae.web.db import WebDB
from mudae.web.supervisor import WebSupervisor, WorkerHandle


def test_webui_normalize_wishlist_items_supports_structured_rows() -> None:
    items = WebConfig.normalize_wishlist_items(
        [
            {"name": "Rem", "priority": 1, "is_star": False},
            {"value": "Emilia", "priority": 2, "star": True},
            "Ram",
            {"name": "  ", "priority": 3},
        ]
    )

    assert items == [
        {"name": "Rem", "priority": 1, "is_star": False},
        {"name": "Emilia", "priority": 3, "is_star": True},
        {"name": "Ram", "priority": 2, "is_star": False},
    ]


def test_webui_normalize_ui_settings_adds_theme_and_sanitizes_values() -> None:
    settings = WebConfig.normalize_ui_settings(
        {
            "bind_host": "0.0.0.0",
            "bind_port": "9001",
            "retention_days": 0,
            "auto_open_browser": "false",
            "theme": "neon",
        }
    )

    assert settings["bind_host"] == "0.0.0.0"
    assert settings["bind_port"] == 9001
    assert settings["retention_days"] == 1
    assert settings["auto_open_browser"] is False
    assert settings["theme"] == "system"


def test_webui_reset_runtime_state_marks_live_records_stopped(tmp_path) -> None:
    db = WebDB(tmp_path / "webui.db")
    account = db.upsert_account({"name": "Alpha", "token": "token-alpha", "max_power": 111})
    session = db.create_session(session_id="sess-1", account_id=int(account["id"]), mode="main", status="running", pid=4242)
    queue = db.enqueue_action(account_id=int(account["id"]), mode="main", action="start", source="manual", status="running")
    db.save_account_state(
        int(account["id"]),
        {
            "account_id": int(account["id"]),
            "status": "running",
            "active_mode": "main",
            "active_session_id": session["id"],
            "paused_mode": None,
            "worker_status": "running",
            "connection_status": "Connected",
            "dashboard_state": "RUNNING",
            "next_action": "Next rolls at 12:00:00",
            "countdown_active": True,
            "countdown_remaining": 120,
            "pid": 4242,
        },
    )

    db.reset_runtime_state()

    session_after = db.get_session("sess-1")
    queue_after = db.get_queue_item(int(queue["id"]))
    state_after = db.get_account_state(int(account["id"]))

    assert session_after is not None
    assert session_after["status"] == "stopped"
    assert session_after["ended_at"] is not None
    assert queue_after is not None
    assert queue_after["status"] == "failed"
    assert state_after is not None
    assert state_after["status"] == "stopped"
    assert state_after["active_mode"] is None
    assert state_after["active_session_id"] is None
    assert state_after["paused_mode"] is None
    assert state_after["worker_status"] is None
    assert state_after["connection_status"] is None
    assert state_after["dashboard_state"] is None
    assert state_after["countdown_active"] is False
    assert state_after["countdown_remaining"] is None
    assert state_after["pid"] is None

    db.close()


def test_webui_upsert_account_preserves_explicit_imported_id(tmp_path) -> None:
    db = WebDB(tmp_path / "webui.db")

    account = db.upsert_account(
        {
            "id": 7,
            "name": "Imported Alpha",
            "discord_user_id": "123",
            "discordusername": "alpha-user",
            "token": "token-alpha",
            "max_power": 111,
        }
    )

    assert account["id"] == 7
    assert db.get_account(7)["name"] == "Imported Alpha"

    db.close()


def test_webui_clear_queue_marks_pending_items_cancelled(tmp_path) -> None:
    db = WebDB(tmp_path / "webui.db")
    account = db.upsert_account({"name": "Alpha", "token": "token-alpha", "max_power": 111})

    pending = db.enqueue_action(account_id=int(account["id"]), mode="main", action="start", source="manual", status="pending")
    starting = db.enqueue_action(account_id=int(account["id"]), mode="oq", action="start", source="manual", status="starting")
    running = db.enqueue_action(account_id=int(account["id"]), mode="oh", action="start", source="manual", status="running")

    cleared = db.clear_queue(account_id=int(account["id"]))

    assert cleared == 2
    assert db.get_queue_item(int(pending["id"]))["status"] == "cancelled"
    assert db.get_queue_item(int(starting["id"]))["status"] == "cancelled"
    assert db.get_queue_item(int(running["id"]))["status"] == "running"

    db.close()


def test_webui_force_stop_clears_pending_queue_and_marks_active_run_cancelled(tmp_path) -> None:
    db = WebDB(tmp_path / "webui.db")
    account = db.upsert_account({"name": "Alpha", "token": "token-alpha", "max_power": 111})
    supervisor = WebSupervisor(db)

    active_queue = db.enqueue_action(account_id=int(account["id"]), mode="oq", action="start", source="manual", status="running")
    pending_queue = db.enqueue_action(account_id=int(account["id"]), mode="main", action="start", source="manual", status="pending")
    session = db.create_session(
        session_id="sess-oq-force-stop",
        account_id=int(account["id"]),
        mode="oq",
        status="running",
        pid=4242,
        queue_item_id=int(active_queue["id"]),
    )

    class DummyProcess:
        def __init__(self) -> None:
            self.returncode = 1
            self.terminated = False

        def poll(self):
            return self.returncode if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self) -> int:
            self.terminated = True
            return self.returncode

    process = DummyProcess()
    handle = WorkerHandle(
        account_id=int(account["id"]),
        mode="oq",
        worker_id="worker-oq",
        session_id=str(session["id"]),
        config_path=tmp_path / "worker-config.json",
        control_path=tmp_path / "worker-control.json",
        process=process,
        queue_item_id=int(active_queue["id"]),
    )
    supervisor._workers[int(account["id"])] = handle

    snapshot = supervisor.force_stop_account(int(account["id"]), clear_queue=True)

    assert process.terminated is True
    assert snapshot["status"] == "stopping"
    assert db.get_queue_item(int(pending_queue["id"]))["status"] == "cancelled"

    supervisor._wait_for_worker(handle)

    session_after = db.get_session("sess-oq-force-stop")
    active_queue_after = db.get_queue_item(int(active_queue["id"]))
    snapshot_after = supervisor.get_account_snapshot(int(account["id"]))

    assert session_after is not None
    assert session_after["status"] == "stopped"
    assert active_queue_after is not None
    assert active_queue_after["status"] == "cancelled"
    assert snapshot_after["status"] == "stopped"
    assert snapshot_after["queue"] == []

    db.close()


def test_webui_dashboard_roll_feeds_emit_when_terminal_dashboard_disabled(monkeypatch) -> None:
    emitted = []
    original_rolls = list(Session._dashboard_state.get("rolls", []))
    original_others = list(Session._dashboard_state.get("others_rolls", []))

    monkeypatch.setattr(Session, "DASHBOARD_ENABLED", False)
    monkeypatch.setattr(Session, "emit_state", lambda state_type, value: emitted.append((state_type, value)))

    Session._dashboard_state["rolls"] = []
    Session._dashboard_state["rolls_total"] = 0
    Session._dashboard_state["rolls_target"] = None
    Session._dashboard_state["rolls_remaining"] = None
    Session._dashboard_state["others_rolls"] = []
    try:
        Session._dashboard_add_roll({"name": "Rem", "series": "Re:Zero", "kakera": 500})
        Session._dashboard_mark_last_roll("candidate", True)
        Session._dashboard_add_other_roll({"roller": "ally", "name": "Emilia", "series": "Re:Zero"})

        roll_events = [value for state_type, value in emitted if state_type == "rolls"]
        other_events = [value for state_type, value in emitted if state_type == "others_rolls"]

        assert Session._dashboard_state["rolls"][0]["candidate"] is True
        assert roll_events[-1]["items"][0]["candidate"] is True
        assert roll_events[-1]["total"] == 1
        assert Session._dashboard_state["others_rolls"][0]["roller"] == "ally"
        assert other_events[-1]["items"][0]["name"] == "Emilia"
    finally:
        Session._dashboard_state["rolls"] = original_rolls
        Session._dashboard_state["rolls_total"] = len(original_rolls)
        Session._dashboard_state["others_rolls"] = original_others


def test_webui_supervisor_state_enrichment_supports_live_dashboard_panels(tmp_path) -> None:
    db = WebDB(tmp_path / "webui.db")
    account = db.upsert_account({"name": "Alpha", "token": "token-alpha", "max_power": 111})
    supervisor = WebSupervisor(db)

    supervisor._apply_state_event(
        int(account["id"]),
        {
            "state_type": "session_status",
            "value": {
                "rolls": 7,
                "can_claim_now": True,
                "current_power": 88,
                "oh_left": 2,
                "oc_left": 1,
                "oq_left": 0,
                "sphere_balance": 15,
            },
        },
    )
    supervisor._apply_state_event(
        int(account["id"]),
        {
            "state_type": "session_meta",
            "value": {"session_start": "2026-03-25 12:34:56", "session_start_ts": 1711366496.0},
        },
    )
    supervisor._apply_state_event(
        int(account["id"]),
        {
            "state_type": "rolls",
            "value": {"items": [{"name": "Rem"}], "total": 1, "target": 10, "remaining": 9},
        },
    )
    supervisor._apply_state_event(
        int(account["id"]),
        {
            "state_type": "others_rolls",
            "value": {"items": [{"roller": "ally", "name": "Emilia"}]},
        },
    )
    supervisor._apply_state_event(
        int(account["id"]),
        {
            "state_type": "connection_retry",
            "value": {"active": True, "remaining": 27},
        },
    )

    snapshot = supervisor.get_account_snapshot(int(account["id"]))

    assert snapshot["session_status"]["rolls"] == 7
    assert snapshot["session_start"] == "2026-03-25 12:34:56"
    assert snapshot["rolls"][0]["name"] == "Rem"
    assert snapshot["rolls_total"] == 1
    assert snapshot["rolls_remaining"] == 9
    assert snapshot["others_rolls"][0]["roller"] == "ally"
    assert snapshot["connection_retry_active"] is True
    assert snapshot["connection_retry_sec"] == 27
    assert snapshot["oh_left"] == 2
    assert snapshot["sphere_balance"] == 15

    db.close()

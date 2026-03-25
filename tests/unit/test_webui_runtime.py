from __future__ import annotations

from mudae.web import config as WebConfig
from mudae.web.db import WebDB


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

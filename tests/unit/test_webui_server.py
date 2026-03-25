from __future__ import annotations

from fastapi.testclient import TestClient

from mudae.web.db import WebDB
from mudae.web.supervisor import WebSupervisor
from mudae.web import server as WebServer


def test_webui_overview_bootstraps_seeded_account(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)
    bundle = {
        "app_settings": {"MACHINE_TAG": "test-webui"},
        "ui_settings": {"bind_host": "127.0.0.1", "bind_port": 8765, "retention_days": 30, "auto_open_browser": False},
        "accounts": [
            {
                "name": "Alpha",
                "discord_user_id": "123",
                "discordusername": "alpha-user",
                "token": "token-alpha",
                "max_power": 111,
            }
        ],
        "wishlists": {
            "global": [{"name": "Rem", "priority": 3, "is_star": True}],
            "accounts": {},
        },
    }

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: bundle)

    with TestClient(WebServer.app) as client:
        overview = client.get("/api/overview")
        wishlist = client.get("/api/wishlist")
        settings = client.get("/api/settings")

    overview_payload = overview.json()
    wishlist_payload = wishlist.json()
    settings_payload = settings.json()

    assert overview.status_code == 200
    assert overview_payload["running_count"] == 0
    assert len(overview_payload["accounts"]) == 1
    assert overview_payload["accounts"][0]["account"]["name"] == "Alpha"
    assert wishlist.status_code == 200
    assert len(wishlist_payload["global"]) == 1
    assert wishlist_payload["global"][0]["name"] == "Rem"
    assert wishlist_payload["global"][0]["priority"] == 3
    assert wishlist_payload["global"][0]["is_star"] is True
    assert settings.status_code == 200
    assert settings_payload["app_settings"]["MACHINE_TAG"] == "test-webui"

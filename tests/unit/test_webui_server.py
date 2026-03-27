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
        "ui_settings": {"bind_host": "127.0.0.1", "bind_port": 8765, "retention_days": 30, "auto_open_browser": False, "theme": "dark"},
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
    assert settings_payload["ui_settings"]["theme"] == "dark"


def test_webui_force_stop_and_clear_queue_endpoints_are_available(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)
    account = temp_db.upsert_account({"name": "Alpha", "token": "token-alpha", "max_power": 111})

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: {"accounts": [], "wishlists": {"global": [], "accounts": {}}})

    with TestClient(WebServer.app) as client:
        clear_response = client.delete(f"/api/accounts/{int(account['id'])}/queue")
        stop_response = client.post(f"/api/accounts/{int(account['id'])}/force-stop")

    assert clear_response.status_code == 200
    assert clear_response.json()["cleared"] == 0
    assert stop_response.status_code == 200
    assert stop_response.json()["snapshot"]["status"] == "stopped"


def test_webui_put_settings_normalizes_theme_defaults(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: {"accounts": [], "wishlists": {"global": [], "accounts": {}}})

    with TestClient(WebServer.app) as client:
        response = client.put(
            "/api/settings",
            json={"app_settings": {"MACHINE_TAG": "theme-test"}, "ui_settings": {"theme": "light", "retention_days": 0}},
        )

    payload = response.json()

    assert response.status_code == 200
    assert payload["app_settings"]["MACHINE_TAG"] == "theme-test"
    assert payload["ui_settings"]["theme"] == "light"
    assert payload["ui_settings"]["retention_days"] == 1


def test_webui_get_settings_schema_lists_sections_and_unknown_keys(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)
    temp_db.set_settings("app_settings", {"rollCommand": "wx", "UNKNOWN_FLAG": "mystery"})

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: {"accounts": [], "wishlists": {"global": [], "accounts": {}}})

    with TestClient(WebServer.app) as client:
        response = client.get("/api/settings/schema")

    payload = response.json()

    assert response.status_code == 200
    assert any(section["id"] == "appearance" for section in payload["sections"])
    assert any(item["key"] == "UNKNOWN_FLAG" for item in payload["unknown_app_settings"])


def test_webui_patch_settings_preserves_unknown_keys(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)
    temp_db.set_settings("app_settings", {"rollCommand": "wx", "UNKNOWN_FLAG": "keep-me"})
    temp_db.set_settings("ui_settings", {"theme": "system"})

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: {"accounts": [], "wishlists": {"global": [], "accounts": {}}})

    with TestClient(WebServer.app) as client:
        response = client.patch("/api/settings", json={"app_settings": {"rollCommand": "wa"}, "ui_settings": {"theme": "dark"}})

    payload = response.json()

    assert response.status_code == 200
    assert payload["app_settings"]["rollCommand"] == "wa"
    assert payload["app_settings"]["UNKNOWN_FLAG"] == "keep-me"
    assert payload["ui_settings"]["theme"] == "dark"


def test_webui_patch_settings_returns_field_errors_for_invalid_values(tmp_path, monkeypatch) -> None:
    temp_db = WebDB(tmp_path / "webui.db")
    temp_supervisor = WebSupervisor(temp_db)

    monkeypatch.setattr(WebServer, "db", temp_db)
    monkeypatch.setattr(WebServer, "supervisor", temp_supervisor)
    monkeypatch.setattr(WebServer, "build_initial_import_bundle", lambda: {"accounts": [], "wishlists": {"global": [], "accounts": {}}})

    with TestClient(WebServer.app) as client:
        response = client.patch("/api/settings", json={"ui_settings": {"theme": "neon"}, "app_settings": {"ROLLS_PER_RESET": "oops"}})

    payload = response.json()

    assert response.status_code == 422
    assert len(payload["field_errors"]) == 2
    assert any(item["key"] == "theme" for item in payload["field_errors"])
    assert any(item["key"] == "ROLLS_PER_RESET" for item in payload["field_errors"])

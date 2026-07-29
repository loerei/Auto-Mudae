from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mudae.web.config import DEFAULT_UI_SETTINGS, WEB_DB_PATH, build_initial_import_bundle, ensure_web_dirs, normalize_ui_settings
from mudae.web.db import WebDB
from mudae.web.settings_schema import apply_settings_patch, build_settings_schema
from mudae.web.supervisor import SUPPORTED_MODES, WebSupervisor


class AccountPayload(BaseModel):
    id: Optional[int] = None
    name: str
    discord_user_id: Optional[str] = None
    discordusername: Optional[str] = None
    token: str
    max_power: int = 110


class WishlistItemPayload(BaseModel):
    name: str
    priority: int = 2
    is_star: bool = False


class SettingsPayload(BaseModel):
    app_settings: Dict[str, Any] = Field(default_factory=dict)
    ui_settings: Dict[str, Any] = Field(default_factory=dict)


class SettingsPatchPayload(BaseModel):
    app_settings: Optional[Dict[str, Any]] = None
    ui_settings: Optional[Dict[str, Any]] = None


class QueuePayload(BaseModel):
    account_id: int
    mode: str
    action: str = "start"
    scheduled_for: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class SchedulePayload(BaseModel):
    account_id: int
    mode: str
    action: str = "start"
    run_at: datetime
    payload: Dict[str, Any] = Field(default_factory=dict)


class ImportPayload(BaseModel):
    app_settings: Dict[str, Any] = Field(default_factory=dict)
    ui_settings: Dict[str, Any] = Field(default_factory=dict)
    accounts: List[Dict[str, Any]] = Field(default_factory=list)
    wishlists: Dict[str, Any] = Field(default_factory=dict)
    queue: List[Dict[str, Any]] = Field(default_factory=list)
    schedules: List[Dict[str, Any]] = Field(default_factory=list)


ensure_web_dirs()
db = WebDB(WEB_DB_PATH)
supervisor = WebSupervisor(db)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DIST_DIR = PROJECT_ROOT / "webui" / "dist"
SPA_HTML_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
_CLOSE_PARENT_CONSOLE_ENV = "MUDAE_WEBUI_CLOSE_PARENT_CONSOLE"
_uvicorn_server: Optional[uvicorn.Server] = None


def _bootstrap_defaults() -> None:
    current_settings = db.get_settings("app_settings", {})
    current_ui = db.get_settings("ui_settings", {})
    bundle = build_initial_import_bundle()
    if not current_settings:
        db.set_settings("app_settings", bundle.get("app_settings") or {})
    if not current_ui:
        db.set_settings("ui_settings", normalize_ui_settings(bundle.get("ui_settings") or DEFAULT_UI_SETTINGS))
    else:
        db.set_settings("ui_settings", normalize_ui_settings(current_ui))
    if not db.list_accounts():
        for account in bundle.get("accounts") or []:
            db.upsert_account(account)
        db.replace_wishlist(None, bundle.get("wishlists", {}).get("global") or [])


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_defaults()
    db.reset_runtime_state()
    supervisor.hub.attach_loop(asyncio.get_running_loop())
    supervisor.start()
    yield
    supervisor.shutdown()
    db.close()


app = FastAPI(title="Mudae WebUI", version="0.1.0", lifespan=lifespan)

if (DIST_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


def _require_mode(mode: str) -> str:
    value = str(mode or "").strip().lower()
    if value not in SUPPORTED_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {mode}")
    return value


def _running_count() -> int:
    return sum(1 for snapshot in supervisor.list_account_snapshots() if snapshot.get("status") in {"running", "queued", "pausing", "stopping"})


def _signal_server_shutdown() -> None:
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True


def _schedule_parent_console_close_if_configured() -> None:
    if os.name != "nt":
        return
    if os.environ.get(_CLOSE_PARENT_CONSOLE_ENV) != "1":
        return
    parent_pid = os.getppid()
    if parent_pid <= 1:
        return
    script = (
        "import subprocess, time; "
        "time.sleep(0.75); "
        f"subprocess.run(['taskkill', '/PID', '{parent_pid}', '/T', '/F'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )
    creationflags = 0
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-c", script],
        creationflags=creationflags,
        close_fds=True,
    )


@app.get("/api/overview")
def get_overview() -> Dict[str, Any]:
    return {
        "accounts": supervisor.list_account_snapshots(),
        "queue": db.list_queue(),
        "recent_sessions": db.list_recent_sessions(limit=20),
        "running_count": _running_count(),
    }


@app.get("/api/accounts")
def list_accounts() -> Dict[str, Any]:
    accounts = db.list_accounts()
    snapshots = {item["account"]["id"]: item for item in supervisor.list_account_snapshots()}
    return {
        "items": [
            {
                **account,
                "snapshot": snapshots.get(account["id"]),
            }
            for account in accounts
        ]
    }


@app.post("/api/accounts")
def create_account(payload: AccountPayload) -> Dict[str, Any]:
    account = db.upsert_account(payload.model_dump())
    snapshot = supervisor.get_account_snapshot(int(account["id"]))
    return {"account": account, "snapshot": snapshot}


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, payload: AccountPayload) -> Dict[str, Any]:
    account = db.upsert_account({**payload.model_dump(), "id": account_id})
    snapshot = supervisor.get_account_snapshot(account_id)
    return {"account": account, "snapshot": snapshot}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int) -> Dict[str, Any]:
    supervisor.stop_account(account_id)
    db.delete_account(account_id)
    return {"ok": True}


@app.get("/api/accounts/{account_id}")
def get_account(account_id: int) -> Dict[str, Any]:
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    snapshot = supervisor.get_account_snapshot(account_id)
    history = db.list_recent_sessions(account_id=account_id, limit=25)
    return {"account": account, "snapshot": snapshot, "history": history}


@app.get("/api/accounts/{account_id}/history")
def get_account_history(account_id: int) -> Dict[str, Any]:
    return {
        "sessions": db.list_recent_sessions(account_id=account_id, limit=50),
        "events": db.list_events(account_id=account_id, limit=200),
    }


@app.post("/api/accounts/{account_id}/modes/{mode}/{action}")
def control_mode(account_id: int, mode: str, action: str) -> Dict[str, Any]:
    normalized_mode = _require_mode(mode)
    action_norm = str(action or "").strip().lower()
    if action_norm == "start":
        return supervisor.start_mode(account_id, normalized_mode)
    if action_norm == "restart":
        return supervisor.restart_account(account_id, normalized_mode)
    if action_norm == "stop":
        return {"snapshot": supervisor.stop_account(account_id)}
    if action_norm == "pause":
        return {"snapshot": supervisor.pause_account(account_id)}
    if action_norm == "resume":
        return supervisor.resume_account(account_id)
    raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")


@app.post("/api/accounts/{account_id}/force-stop")
def force_stop_account(account_id: int) -> Dict[str, Any]:
    return {"snapshot": supervisor.force_stop_account(account_id, clear_queue=True)}


@app.get("/api/wishlist")
def list_all_wishlist() -> Dict[str, Any]:
    return {
        "global": db.list_wishlist(None),
        "accounts": {str(account["id"]): db.list_wishlist(int(account["id"])) for account in db.list_accounts()},
    }


@app.get("/api/wishlist/global")
def get_global_wishlist() -> Dict[str, Any]:
    return {"items": db.list_wishlist(None)}


@app.put("/api/wishlist/global")
def put_global_wishlist(items: List[WishlistItemPayload]) -> Dict[str, Any]:
    saved = db.replace_wishlist(None, [item.model_dump() for item in items])
    return {"items": saved}


@app.get("/api/accounts/{account_id}/wishlist")
def get_account_wishlist(account_id: int) -> Dict[str, Any]:
    return {"items": db.list_wishlist(account_id)}


@app.put("/api/accounts/{account_id}/wishlist")
def put_account_wishlist(account_id: int, items: List[WishlistItemPayload]) -> Dict[str, Any]:
    saved = db.replace_wishlist(account_id, [item.model_dump() for item in items])
    return {"items": saved}


@app.get("/api/logs")
def get_logs(
    account_id: Optional[int] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    level: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> Dict[str, Any]:
    return {
        "items": db.list_events(
            account_id=account_id,
            mode=mode,
            level=level.upper() if level else None,
            session_id=session_id,
            limit=limit,
        )
    }


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    return {
        "app_settings": db.get_settings("app_settings", {}),
        "ui_settings": normalize_ui_settings(db.get_settings("ui_settings", DEFAULT_UI_SETTINGS)),
    }


@app.get("/api/settings/schema")
def get_settings_schema() -> Dict[str, Any]:
    return build_settings_schema(
        app_settings=db.get_settings("app_settings", {}),
        ui_settings=db.get_settings("ui_settings", DEFAULT_UI_SETTINGS),
    )


@app.put("/api/settings")
def put_settings(payload: SettingsPayload) -> Dict[str, Any]:
    app_settings = db.set_settings("app_settings", payload.app_settings)
    merged_ui = normalize_ui_settings({**db.get_settings("ui_settings", DEFAULT_UI_SETTINGS), **payload.ui_settings})
    ui_settings = db.set_settings("ui_settings", merged_ui)
    return {"app_settings": app_settings, "ui_settings": ui_settings}


@app.patch("/api/settings")
def patch_settings(payload: SettingsPatchPayload) -> Response:
    current_app = db.get_settings("app_settings", {})
    current_ui = db.get_settings("ui_settings", DEFAULT_UI_SETTINGS)
    app_settings, ui_settings, field_errors = apply_settings_patch(
        current_app_settings=current_app,
        current_ui_settings=current_ui,
        app_patch=payload.app_settings,
        ui_patch=payload.ui_settings,
    )
    if field_errors:
        return JSONResponse(
            status_code=422,
            content={"message": "Validation failed.", "field_errors": field_errors},
        )
    saved_app = db.set_settings("app_settings", app_settings)
    saved_ui = db.set_settings("ui_settings", ui_settings)
    return JSONResponse(status_code=200, content={"app_settings": saved_app, "ui_settings": saved_ui})


@app.post("/api/shutdown")
def shutdown_webui(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    if _uvicorn_server is None:
        raise HTTPException(status_code=503, detail="Shutdown is unavailable in this server context")
    background_tasks.add_task(_signal_server_shutdown)
    background_tasks.add_task(_schedule_parent_console_close_if_configured)
    return {"ok": True, "message": "Shutdown requested"}


@app.get("/api/queue")
def get_queue(account_id: Optional[int] = Query(default=None)) -> Dict[str, Any]:
    return {"items": db.list_queue(account_id=account_id)}


@app.post("/api/queue")
def post_queue(payload: QueuePayload) -> Dict[str, Any]:
    item = supervisor.enqueue_action(
        account_id=payload.account_id,
        mode=_require_mode(payload.mode),
        action=payload.action,
        scheduled_for=payload.scheduled_for.timestamp() if payload.scheduled_for else None,
        payload=payload.payload,
    )
    return {"item": item, "queue": db.list_queue(account_id=payload.account_id)}


@app.delete("/api/accounts/{account_id}/queue")
def delete_account_queue(account_id: int) -> Dict[str, Any]:
    return supervisor.clear_queue(account_id)


@app.get("/api/schedules")
def get_schedules(account_id: Optional[int] = Query(default=None)) -> Dict[str, Any]:
    return {"items": db.list_schedules(account_id=account_id)}


@app.post("/api/schedules")
def post_schedule(payload: SchedulePayload) -> Dict[str, Any]:
    schedule = db.create_schedule(
        account_id=payload.account_id,
        mode=_require_mode(payload.mode),
        action=payload.action,
        run_at=payload.run_at.timestamp(),
        payload=payload.payload,
    )
    return {"item": schedule}


@app.get("/api/export")
def export_bundle() -> Dict[str, Any]:
    return db.export_bundle()


@app.post("/api/import")
def import_bundle(payload: ImportPayload) -> Dict[str, Any]:
    if _running_count() > 0:
        raise HTTPException(status_code=409, detail="Stop running sessions before importing data")
    db.replace_from_bundle(payload.model_dump())
    return {"ok": True, "overview": get_overview()}


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await ws.accept()
    queue = await supervisor.hub.register()
    try:
        await ws.send_json({"kind": "bootstrap", "overview": get_overview()})
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        await supervisor.hub.unregister(queue)


@app.get("/", include_in_schema=False, response_model=None)
def spa_root() -> Response:
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers=SPA_HTML_HEADERS)
    return HTMLResponse(
        "<h1>Mudae WebUI</h1><p>Frontend build not found. Run the WebUI build first.</p>",
        headers=SPA_HTML_HEADERS,
    )


@app.get("/{full_path:path}", include_in_schema=False, response_model=None)
def spa_fallback(full_path: str) -> Response:
    if full_path.startswith("api") or full_path.startswith("ws"):
        raise HTTPException(status_code=404, detail="Not found")
    candidate = DIST_DIR / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers=SPA_HTML_HEADERS)
    return HTMLResponse(
        "<h1>Mudae WebUI</h1><p>Frontend build not found. Run the WebUI build first.</p>",
        headers=SPA_HTML_HEADERS,
    )


def main() -> None:
    global _uvicorn_server
    ui_settings = normalize_ui_settings(db.get_settings("ui_settings", DEFAULT_UI_SETTINGS))
    host = str(ui_settings.get("bind_host") or DEFAULT_UI_SETTINGS["bind_host"])
    port = int(ui_settings.get("bind_port") or DEFAULT_UI_SETTINGS["bind_port"])
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, reload=False))
    _uvicorn_server = server
    server.run()


if __name__ == "__main__":
    main()

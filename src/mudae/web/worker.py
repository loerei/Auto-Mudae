from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from mudae.paths import LOGS_DIR, ensure_runtime_dirs
from mudae.web.bridge import emit_state, emit_worker_status
from mudae.web.config import apply_runtime_configuration


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PAUSED = 20
EXIT_STOPPED = 30


class ControlReader:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._stop = threading.Event()
        self._desired = "run"
        self._thread = threading.Thread(target=self._watch, name="mudae-web-control", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def desired(self) -> str:
        return self._desired

    def _watch(self) -> None:
        from mudae.core.session_engine import setStopRequested

        while not self._stop.is_set():
            desired = self._read_desired()
            self._desired = desired
            if desired == "stop":
                setStopRequested(True)
            self._stop.wait(0.25)

    def _read_desired(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._desired
        desired = str(payload.get("desired") or "run").strip().lower()
        return desired if desired in {"run", "pause", "stop"} else "run"


def _format_next_action_time(seconds: int) -> str:
    if seconds <= 0:
        return "now"
    target = time.localtime(time.time() + seconds)
    return time.strftime("%H:%M:%S", target)


def _no_reset_retry_delay_seconds(failures: int) -> int:
    from mudae.config import vars as Vars

    attempts = max(1, int(failures))
    base_delay = min(300, 30 * (2 ** (attempts - 1)))
    try:
        jitter_pct = float(getattr(Vars, "NO_RESET_RETRY_JITTER_PCT", 0.15) or 0.15)
    except (TypeError, ValueError):
        jitter_pct = 0.15
    jitter_pct = max(0.0, min(0.15, jitter_pct))
    if base_delay <= 0 or jitter_pct <= 0:
        return base_delay
    jittered = int(round(base_delay * (1.0 + random.uniform(0.0, jitter_pct))))
    return min(300, max(1, jittered))


def _sleep_with_control(seconds: float, control: ControlReader, *, token: Optional[str] = None) -> str:
    from mudae.core.session_engine import pollExternalRolls

    end_time = time.time() + max(0.0, seconds)
    last_poll = 0.0
    while time.time() < end_time:
        desired = control.desired()
        if desired in {"pause", "stop"}:
            return desired
        if token and time.time() - last_poll >= 5.0:
            last_poll = time.time()
            try:
                pollExternalRolls(token)
            except Exception:
                pass
        time.sleep(0.25)
    return "run"


def _countdown(seconds: int, control: ControlReader, *, token: Optional[str] = None) -> str:
    remaining = max(0, int(seconds))
    while remaining > 0:
        emit_state("countdown", {"active": True, "remaining": remaining})
        desired = _sleep_with_control(1.0, control, token=token)
        if desired in {"pause", "stop"}:
            emit_state("countdown", {"active": False, "remaining": remaining})
            return desired
        remaining -= 1
    emit_state("countdown", {"active": False, "remaining": 0})
    return "run"


def _set_session_context(user_name: str, session_id: str, mode: str) -> None:
    from mudae.core.session_engine import setCurrentUser, setSessionLogFile, setStopRequested

    ensure_runtime_dirs()
    setStopRequested(False)
    setCurrentUser(user_name)
    session_ms = int(time.time() * 1000)
    log_path = LOGS_DIR / f"WebSession.{mode}.{user_name}.sid{session_id[:8]}.{session_ms}.log"
    setSessionLogFile(os.fspath(log_path))


def _ensure_connected(token: str, user_name: str, control: ControlReader) -> tuple[Optional[str], str]:
    from mudae.core.session_engine import log_error, log_success, log_warn, probe_token_status, setConnectionStatus

    had_issue = False
    while True:
        desired = control.desired()
        if desired in {"pause", "stop"}:
            return (None, desired)
        result = probe_token_status(token)
        status = result.get("status")
        if status == "OK":
            setConnectionStatus("Connected")
            if had_issue:
                log_success(f"Connection restored for {user_name}")
            return (token, "run")
        had_issue = True
        if status == "TOKEN_MISSING":
            setConnectionStatus("Token Missing")
            log_error(f"Token missing for {user_name}. Retrying in 5 seconds...")
        elif status == "INVALID_TOKEN":
            setConnectionStatus("Invalid Token")
            code = result.get("status_code")
            log_error(f"Invalid token for {user_name}" + (f" (HTTP {code})" if code else "") + ". Retrying in 5 seconds...")
        else:
            setConnectionStatus("Connection Loss")
            message = result.get("error") or result.get("status_code") or "unknown"
            log_warn(f"Connection loss detected for {user_name}: {message}. Retrying in 5 seconds...")
        desired = _countdown(5, control)
        if desired in {"pause", "stop"}:
            return (None, desired)


def _run_main_mode(payload: Dict[str, Any], control: ControlReader) -> int:
    from mudae.config import vars as Vars
    from mudae.core.session_engine import (
        calculateFixedResetSeconds,
        enhancedRoll,
        getLastTuFetchReason,
        initializeSession,
        log,
        log_error,
        log_warn,
        predictStatusAfterCountdown,
        run_ouro_auto,
        setConnectionStatus,
        setDashboardState,
    )

    account = payload["account"]
    user_name = str(account["name"])
    token = str(account["token"])
    session_id = str(payload["session_id"])
    _set_session_context(user_name, session_id, "main")

    setConnectionStatus("Connecting")
    emit_worker_status("starting", mode="main")
    current_token, state = _ensure_connected(token, user_name, control)
    if not current_token:
        return EXIT_PAUSED if state == "pause" else EXIT_STOPPED

    initializeSession(current_token, user_name)
    setDashboardState("READY", last_action="Session initialized")
    emit_worker_status("running", mode="main")
    tu_retry_failures = 0

    while True:
        desired = control.desired()
        if desired == "pause":
            return EXIT_PAUSED
        if desired == "stop":
            return EXIT_STOPPED
        try:
            current_token, state = _ensure_connected(token, user_name, control)
            if not current_token:
                return EXIT_PAUSED if state == "pause" else EXIT_STOPPED

            setConnectionStatus("Running")
            setDashboardState("RUNNING", last_action="Starting roll session")
            tu_info = enhancedRoll(current_token)

            desired = control.desired()
            if desired == "pause":
                return EXIT_PAUSED
            if desired == "stop":
                return EXIT_STOPPED

            if tu_info and tu_info.get("next_reset_min", 0) >= 0:
                tu_retry_failures = 0
                next_roll_sec, next_claim_sec = calculateFixedResetSeconds()
                next_roll_min = (next_roll_sec + 59) // 60 if next_roll_sec > 0 else 0
                next_claim_min = (next_claim_sec + 59) // 60 if next_claim_sec > 0 else 0
                tu_info["next_reset_sec"] = next_roll_sec
                tu_info["claim_reset_sec"] = next_claim_sec
                tu_info["next_reset_min"] = next_roll_min
                tu_info["claim_reset_min"] = next_claim_min
                wait_seconds = next_roll_sec

                if getattr(Vars, "AUTO_OURO_AFTER_ROLL", True):
                    updated = run_ouro_auto(current_token, tu_info, user_name, time_budget_sec=wait_seconds)
                    if updated is not None:
                        tu_info = updated
                    next_roll_sec, next_claim_sec = calculateFixedResetSeconds()
                    next_roll_min = (next_roll_sec + 59) // 60 if next_roll_sec > 0 else 0
                    next_claim_min = (next_claim_sec + 59) // 60 if next_claim_sec > 0 else 0
                    tu_info["next_reset_sec"] = next_roll_sec
                    tu_info["claim_reset_sec"] = next_claim_sec
                    tu_info["next_reset_min"] = next_roll_min
                    tu_info["claim_reset_min"] = next_claim_min
                    wait_seconds = next_roll_sec

                log(f"Next rolls available in {max(0, int(round(wait_seconds / 60)))} minutes")
                predicted_status = predictStatusAfterCountdown(tu_info, max(0, int(round(wait_seconds / 60))))
                emit_state("predicted", {"status": predicted_status, "minutes_to_wait": max(0, int(round(wait_seconds / 60)))})

                next_action_time = _format_next_action_time(int(wait_seconds))
                setDashboardState(
                    "WAITING",
                    last_action="Roll session complete",
                    next_action=f"Next rolls at {next_action_time}",
                )
                setConnectionStatus("Connected")
                desired = _countdown(wait_seconds, control, token=current_token)
                if desired == "pause":
                    return EXIT_PAUSED
                if desired == "stop":
                    return EXIT_STOPPED
            else:
                tu_retry_failures += 1
                wait_seconds = _no_reset_retry_delay_seconds(tu_retry_failures)
                tu_reason = getLastTuFetchReason(current_token)
                if tu_reason == "same_account_action_busy":
                    retry_label = "Same-account action gate busy"
                    log_warn(
                        f"Same-account action gate busy, waiting {wait_seconds} seconds before retry without "
                        f"sending extra commands (attempt {tu_retry_failures}, capped at 300 seconds)..."
                    )
                elif tu_reason == "network_fail":
                    retry_label = "Network error while refreshing /tu"
                    log_warn(
                        f"Network error while refreshing /tu, waiting {wait_seconds} seconds before retry "
                        f"(attempt {tu_retry_failures}, capped at 300 seconds)..."
                    )
                else:
                    retry_label = "No fresh /tu state available"
                    log_warn(
                        f"No fresh /tu state available, waiting {wait_seconds} seconds before retry "
                        f"(attempt {tu_retry_failures}, capped at 300 seconds)..."
                    )
                next_action_time = _format_next_action_time(wait_seconds)
                setDashboardState(
                    "WAITING",
                    last_action=retry_label,
                    next_action=f"Retry at {next_action_time}",
                )
                setConnectionStatus("Connected")
                desired = _countdown(wait_seconds, control, token=current_token)
                if desired == "pause":
                    return EXIT_PAUSED
                if desired == "stop":
                    return EXIT_STOPPED
        except Exception as exc:
            log_error(f"Error during roll cycle: {exc}")
            setDashboardState("ERROR", last_action=f"Error: {exc}", next_action=f"Retry at {_format_next_action_time(300)}")
            desired = _countdown(300, control, token=current_token)
            if desired == "pause":
                return EXIT_PAUSED
            if desired == "stop":
                return EXIT_STOPPED


def _run_single_mode(payload: Dict[str, Any], mode: str) -> int:
    account = payload["account"]
    session_id = str(payload["session_id"])
    user_name = str(account["name"])
    token = str(account["token"])
    _set_session_context(user_name, session_id, mode)

    emit_state("dashboard_state", {"state": "RUNNING", "last_action": f"Starting {mode.upper()} session", "next_action": None})
    emit_worker_status("running", mode=mode)

    if mode == "oh":
        from mudae.ouro.Oh_bot import run_session
    elif mode == "oc":
        from mudae.ouro.Oc_bot import run_session
    else:
        from mudae.ouro.Oq_bot import run_session

    result = run_session(token, user_name, quiet=True)
    emit_state("summary", {"mode": mode, "result": result})
    status = str(result.get("status") or "ok")
    if status == "error":
        return EXIT_ERROR
    return EXIT_OK


def _load_payload(path: str) -> Dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    account_id = int(raw["account_id"])
    account = None
    for record in raw.get("accounts") or []:
        if int(record["id"]) == account_id:
            account = record
            break
    if account is None:
        raise RuntimeError(f"Account {account_id} not found in worker payload")
    raw["account"] = account
    return raw


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mudae WebUI worker")
    parser.add_argument("--config", required=True, help="Path to worker configuration JSON")
    args = parser.parse_args(argv)

    os.environ.setdefault("MUDAE_DASHBOARD", "0")
    ensure_runtime_dirs()

    payload = _load_payload(args.config)
    apply_runtime_configuration(
        app_settings=payload.get("app_settings") or {},
        accounts=payload.get("accounts") or [],
        global_wishlist=payload.get("global_wishlist") or [],
        account_wishlist=payload.get("account_wishlist") or [],
    )

    control = ControlReader(str(payload["control_path"]))
    control.start()
    emit_worker_status("booting", mode=str(payload["mode"]))
    try:
        mode = str(payload["mode"]).strip().lower()
        if mode == "main":
            code = _run_main_mode(payload, control)
        elif mode in {"oh", "oc", "oq"}:
            code = _run_single_mode(payload, mode)
        else:
            raise RuntimeError(f"Unsupported mode: {mode}")
        if code == EXIT_PAUSED:
            emit_worker_status("paused")
        elif code == EXIT_STOPPED:
            emit_worker_status("stopped")
        elif code == EXIT_OK:
            emit_worker_status("completed")
        return code
    except Exception as exc:
        emit_worker_status("error", error=str(exc))
        raise
    finally:
        control.close()


if __name__ == "__main__":
    sys.exit(main())

import sys
import time
import threading
import math
import random
import os
import ctypes
import importlib
from typing import Optional, Dict, Any
from mudae.config import vars as Vars
from mudae.paths import LOGS_DIR, ensure_runtime_dirs
from mudae.core.session_engine import (
    initializeSession,
    enhancedRoll,
    getLastTuFetchReason,
    log,
    log_info,
    log_success,
    log_warn,
    log_error,
    setCurrentUser,
    setSessionLogFile,
    setStopRequested,
    probe_token_status,
    setConnectionStatus,
    startConnectionRetry,
    updateConnectionRetry,
    stopConnectionRetry,
    predictStatusAfterCountdown,
    setDashboardState,
    startDashboardCountdown,
    updateDashboardCountdown,
    stopDashboardCountdown,
    run_ouro_auto,
    pollExternalRolls,
    calculateFixedResetSeconds,
    DASHBOARD_ENABLED,
)
from pynput import keyboard

# Flag to track Alt+C press
stop_requested = False
alt_pressed = False
_WINDOWS = os.name == "nt"
_WINDOW_TAG = f"[MUDAE_PID:{os.getpid()}]"
_alt_c_last_trigger_ts = 0.0
_alt_c_ignore_input_until = 0.0

ensure_runtime_dirs()

def _get_window_title(hwnd: int) -> str:
    if not _WINDOWS or not hwnd:
        return ""
    try:
        user32 = ctypes.windll.user32
        title_len = int(user32.GetWindowTextLengthW(hwnd))
        if title_len <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, buf, title_len + 1)
        return str(buf.value or "")
    except Exception:
        return ""

def _ensure_console_title_tag() -> None:
    """Attach a unique tag to this console title so focus checks are per-instance."""
    if not _WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(1024)
        kernel32.GetConsoleTitleW(buf, 1024)
        current_title = str(buf.value or "").strip()
        if _WINDOW_TAG in current_title:
            return
        base_title = current_title or "Mudae bot"
        kernel32.SetConsoleTitleW(f"{base_title} {_WINDOW_TAG}")
    except Exception:
        return

def _flush_console_input_buffer() -> None:
    """Clear pending keyboard input so Alt+C does not leak a stray 'c' into prompts."""
    if not _WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        std_input_handle = -10  # STD_INPUT_HANDLE
        handle = kernel32.GetStdHandle(std_input_handle)
        invalid_handle = ctypes.c_void_p(-1).value
        if handle and handle != invalid_handle:
            kernel32.FlushConsoleInputBuffer(handle)
    except Exception:
        pass
    try:
        import msvcrt  # type: ignore
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        return

def _alt_c_debounce_sec() -> float:
    try:
        debounce_ms = float(getattr(Vars, "ALT_C_DEBOUNCE_MS", 250) or 250)
    except (TypeError, ValueError):
        debounce_ms = 250.0
    return max(0.0, debounce_ms / 1000.0)

def _alt_c_input_guard_sec() -> float:
    try:
        guard_ms = float(getattr(Vars, "ALT_C_INPUT_GUARD_MS", 600) or 600)
    except (TypeError, ValueError):
        guard_ms = 600.0
    return max(0.0, guard_ms / 1000.0)

def _mark_alt_c_triggered() -> None:
    global _alt_c_last_trigger_ts, _alt_c_ignore_input_until
    now = time.monotonic()
    _alt_c_last_trigger_ts = now
    _alt_c_ignore_input_until = now + _alt_c_input_guard_sec()

def _sanitize_user_choice(raw_choice: str) -> str:
    choice = (raw_choice or "").strip()
    if _WINDOWS and time.monotonic() < _alt_c_ignore_input_until:
        lower_choice = choice.lower()
        # Ignore leaked 'c' from Alt+C when prompt appears right after restart.
        if lower_choice == 'c':
            return ""
        if lower_choice.startswith('c') and lower_choice[1:].isdigit():
            return lower_choice[1:]
    return choice

def _is_window_focused() -> bool:
    """Return True only for the active terminal window of this specific bot instance."""
    if not _WINDOWS:
        return True

    strict_focus = os.environ.get("MUDAE_ALT_C_STRICT_FOCUS", "1") != "0"
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        foreground_window = int(user32.GetForegroundWindow() or 0)
        if foreground_window == 0:
            return not strict_focus

        # Windows Terminal path: title contains our unique per-process tag.
        foreground_title = _get_window_title(foreground_window)
        if _WINDOW_TAG and foreground_title and _WINDOW_TAG in foreground_title:
            return True

        # Classic console host path.
        console_window = int(kernel32.GetConsoleWindow() or 0)
        if console_window and foreground_window == console_window:
            current_title = _get_window_title(console_window)
            if not _WINDOW_TAG or (_WINDOW_TAG in current_title):
                return True

        if strict_focus:
            return False

        # Non-strict fallback path only.
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(foreground_window, ctypes.byref(pid))
        if int(pid.value) == os.getpid():
            return True
        if int(pid.value) == os.getppid():
            return True
        return False
    except Exception:
        return not strict_focus

def _is_alt_key(key: Any) -> bool:
    alt_gr = getattr(keyboard.Key, "alt_gr", None)
    return key in (
        keyboard.Key.alt,
        keyboard.Key.alt_l,
        keyboard.Key.alt_r,
        alt_gr,
    )

def _is_c_key(key: Any) -> bool:
    try:
        char = getattr(key, "char", None)
        if isinstance(char, str) and char:
            return char.lower() == "c"
    except Exception:
        pass

    vk = getattr(key, "vk", None)
    return vk in (ord("C"), ord("c"))

def on_press(key):
    """Handle key presses - detect Alt+C to restart bot"""
    global stop_requested, alt_pressed
    try:
        if _is_alt_key(key):
            alt_pressed = True
            return
        if alt_pressed and _is_c_key(key):
            now = time.monotonic()
            if (now - _alt_c_last_trigger_ts) < _alt_c_debounce_sec():
                return
            if _is_window_focused() and not stop_requested:
                _flush_console_input_buffer()
                _mark_alt_c_triggered()
                stop_requested = True
                setStopRequested(True)
                log_info("Alt+C detected, restart requested")
                alt_pressed = False
    except:
        pass

def on_release(key):
    """Handle key releases - track when Alt is released"""
    global alt_pressed
    try:
        if _is_alt_key(key):
            alt_pressed = False
    except:
        pass


def main():
    global _alt_c_last_trigger_ts, _alt_c_ignore_input_until
    _alt_c_last_trigger_ts = 0.0
    _alt_c_ignore_input_until = 0.0
    _ensure_console_title_tag()
    _flush_console_input_buffer()

    # Start listening for Alt+C
    listener = None
    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
    except Exception as e:
        log_warn(f"Could not start key listener: {e} (Alt+C restart disabled)")

    current_token: Optional[str] = None

    def _format_next_action_time(seconds: int) -> str:
        if seconds <= 0:
            return "now"
        target = time.localtime(time.time() + seconds)
        return time.strftime("%H:%M:%S", target)


    def _no_reset_retry_delay_seconds(failures: int) -> int:
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

    def _scan_interval_bounds(remaining_sec: int) -> tuple[float, float]:
        if remaining_sec >= 60 * 30:
            return (0.8, 3.0)
        if remaining_sec >= 60 * 10:
            return (0.4, 2.0)
        if remaining_sec >= 60 * 3:
            return (0.2, 1.0)
        return (0.10, 0.40)

    def countdown(seconds: int, status: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> None:
        """Display a countdown timer that updates each second"""
        global stop_requested
        nonlocal current_token
        if DASHBOARD_ENABLED and status:
            startDashboardCountdown(status, seconds)
        scan_interval_sec = 0.5
        last_scan = 0.0
        scan_inflight = False
        conn_check_interval = 5.0
        last_conn_check = 0.0
        scan_lock = threading.Lock()
        scan_bounds = _scan_interval_bounds(int(seconds))
        end_time = time.monotonic() + max(0, seconds)
        last_display = None
        next_tick = time.monotonic()

        def _scan_worker() -> None:
            nonlocal scan_inflight, scan_interval_sec, last_scan
            try:
                activity = pollExternalRolls(token) if token else 0
            except Exception:
                activity = -1
            with scan_lock:
                now = time.time()
                if activity < 0:
                    scan_interval_sec = min(5.0, scan_interval_sec * 2.0)
                elif activity > 0:
                    scan_interval_sec = max(0.5, scan_interval_sec * 0.7)
                else:
                    scan_interval_sec = min(5.0, scan_interval_sec * 1.3)
                min_bound, max_bound = scan_bounds
                scan_interval_sec = max(min_bound, min(max_bound, scan_interval_sec))
                last_scan = now
                scan_inflight = False

        while True:
            if stop_requested:
                if DASHBOARD_ENABLED:
                    stopDashboardCountdown()
                else:
                    sys.stdout.write("\r" + " " * 50 + "\r")
                    sys.stdout.flush()
                return
            now = time.monotonic()
            if now >= next_tick:
                remaining = max(0, int(math.ceil(end_time - now)))
                if remaining != last_display:
                    scan_bounds = _scan_interval_bounds(remaining)
                    min_bound, max_bound = scan_bounds
                    scan_interval_sec = max(min_bound, min(max_bound, scan_interval_sec))
                    if DASHBOARD_ENABLED:
                        updateDashboardCountdown(remaining)
                    else:
                        minutes = remaining // 60
                        secs = remaining % 60
                        timer_str = f"Waiting {minutes:02d}:{secs:02d}"
                        sys.stdout.write(f"\r{timer_str}")
                        sys.stdout.flush()
                    last_display = remaining
                next_tick = now + 1.0
            if token:
                now = time.time()
                if (not scan_inflight) and (now - last_scan >= scan_interval_sec):
                    scan_inflight = True
                    threading.Thread(target=_scan_worker, daemon=True).start()
                if now - last_conn_check >= conn_check_interval:
                    last_conn_check = now
                    check = probe_token_status(token)
                    if check.get('status') != 'OK':
                        log_warn("Connection issue detected while waiting. Reconnecting...")
                        new_token = _ensure_connected(selected_user_name)
                        if not new_token:
                            stop_requested = True
                            break
                        current_token = new_token
                        token = new_token
            if last_display is not None and last_display <= 0:
                break
            for _ in range(20):
                if stop_requested:
                    if DASHBOARD_ENABLED:
                        stopDashboardCountdown()
                    else:
                        sys.stdout.write("\r" + " " * 50 + "\r")
                        sys.stdout.flush()
                    return
                sleep_for = max(0.0, min(0.05, next_tick - time.monotonic()))
                time.sleep(sleep_for)
        if not stop_requested:
            if DASHBOARD_ENABLED:
                stopDashboardCountdown()
            else:
                sys.stdout.write("\rReady to proceed!              \n")
                sys.stdout.flush()

    def _refresh_vars() -> None:
        try:
            importlib.reload(Vars)
        except Exception as exc:
            log_warn(f"Failed to reload Vars: {exc}")

    def _find_user_record(user_name: str) -> Optional[Dict[str, Any]]:
        for user in Vars.tokens:
            if user.get('name') == user_name:
                return user
        return None

    def _get_token_for_user(user_name: str) -> Optional[str]:
        record = _find_user_record(user_name)
        if not record:
            return None
        token = record.get('token')
        return str(token).strip() if token else None

    def _connection_retry_wait(seconds: int) -> None:
        startConnectionRetry(seconds)
        for remaining in range(int(seconds), 0, -1):
            if stop_requested:
                break
            updateConnectionRetry(remaining)
            time.sleep(1)
        stopConnectionRetry()

    def _ensure_connected(user_name: str, show_connecting: bool = False) -> Optional[str]:
        """Block until token + connection are valid; retry every 5 seconds."""
        last_token = None
        first_attempt = True
        had_issue = False
        while not stop_requested:
            if first_attempt and show_connecting:
                setConnectionStatus("Connecting")
            token = _get_token_for_user(user_name)
            if token != last_token and token and last_token is not None:
                log_info(f"Detected updated token for {user_name}")
            last_token = token
            result = probe_token_status(token)
            status = result.get('status')
            status_code = result.get('status_code')
            error = result.get('error')
            retry_status_override = None
            if status == 'OK':
                stopConnectionRetry()
                setConnectionStatus("Connected")
                if had_issue:
                    log_success("Connection restored")
                return token
            if status == 'TOKEN_MISSING':
                setConnectionStatus("Token Missing")
                log_error(f"Token missing for user {user_name}. Retrying in 5 seconds...")
            elif status == 'INVALID_TOKEN':
                setConnectionStatus("Invalid Token")
                if status_code:
                    log_error(f"Invalid token for user {user_name} (HTTP {status_code}). Retrying in 5 seconds...")
                else:
                    log_error(f"Invalid token for user {user_name}. Retrying in 5 seconds...")
            else:
                setConnectionStatus("Connection Loss")
                if error:
                    log_warn(f"Connection loss detected: {error}. Retrying in 5 seconds...")
                elif status_code:
                    log_warn(f"Connection loss detected (HTTP {status_code}). Retrying in 5 seconds...")
                else:
                    log_warn("Connection loss detected. Retrying in 5 seconds...")
                retry_status_override = "Retrying to Connect"
            had_issue = True
            if retry_status_override:
                setConnectionStatus(retry_status_override)
            _connection_retry_wait(5)
            _refresh_vars()
            first_attempt = False
        return None


    def selectUser():
        """Prompt user to select which account to use"""
        _flush_console_input_buffer()
        print("\n" + "="*50)
        print("Available Users:")
        print("="*50)

        for i, user in enumerate(Vars.tokens, 1):
            print(f"{i}. {user['name']}")

        while True:
            try:
                choice_raw = input("\nSelect user (enter number): ")
                choice = _sanitize_user_choice(choice_raw)
                if not choice:
                    continue
                idx = int(choice) - 1
                if 0 <= idx < len(Vars.tokens):
                    selected = Vars.tokens[idx]
                    print(f"\nSelected: {selected['name']}")
                    return selected
                else:
                    print("Invalid selection. Try again.")
            except ValueError:
                print("Please enter a valid number.")

    # Select user
    selected_user = selectUser()
    selected_user_name = selected_user['name']

    # Set current user for per-user logging
    setCurrentUser(selected_user_name)

    # Initialize per-process session log file for this session.
    log_dir = os.fspath(LOGS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    session_ms = int(time.time() * 1000)
    log_filename = os.path.join(log_dir, f"Session.{selected_user_name}.pid{os.getpid()}.{session_ms}.log")
    with open(log_filename, 'w', encoding='utf-8') as f:
        f.write(f"=== Session Log - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} ===\n")
        f.write(f"User: {selected_user_name}\n\n")
    setSessionLogFile(log_filename)

    log_info("Bot started!")
    log_info("Press Alt+C to restart bot and select a different user")

    try:
        current_token = _ensure_connected(selected_user_name, show_connecting=True)
        if not current_token:
            log_warn("Session aborted: stop requested while connecting")
            return
        initializeSession(current_token, selected_user_name)
        setDashboardState("READY", last_action="Session initialized")
    except KeyboardInterrupt:
        log_warn("\nBot stopped by user (Ctrl+C)")
        sys.exit(0)

    # Main loop - run continuously
    try:
        tu_retry_failures = 0
        while not stop_requested:
            try:
                # Check if restart was requested
                if stop_requested:
                    break

                # Ensure connection + token before running
                current_token = _ensure_connected(selected_user_name)
                if not current_token:
                    break
                setConnectionStatus("Running")

                # Run enhanced roll session
                setDashboardState("RUNNING", last_action="Starting roll session")
                tu_info = enhancedRoll(current_token)

                # Check again after roll session completes
                if stop_requested:
                    break

                # Determine when next rolls will be available
                if tu_info and tu_info.get('next_reset_min', 0) >= 0:
                    tu_retry_failures = 0
                    next_roll_sec, next_claim_sec = calculateFixedResetSeconds()
                    next_roll_min = (next_roll_sec + 59) // 60 if next_roll_sec > 0 else 0
                    next_claim_min = (next_claim_sec + 59) // 60 if next_claim_sec > 0 else 0
                    tu_info['next_reset_sec'] = next_roll_sec
                    tu_info['claim_reset_sec'] = next_claim_sec
                    tu_info['next_reset_min'] = next_roll_min
                    tu_info['claim_reset_min'] = next_claim_min
                    wait_seconds = next_roll_sec

                    if getattr(Vars, 'AUTO_OURO_AFTER_ROLL', True):
                        updated = run_ouro_auto(
                            current_token,
                            tu_info,
                            selected_user_name,
                            time_budget_sec=wait_seconds
                        )
                        if updated is not None:
                            tu_info = updated
                        next_roll_sec, next_claim_sec = calculateFixedResetSeconds()
                        next_roll_min = (next_roll_sec + 59) // 60 if next_roll_sec > 0 else 0
                        next_claim_min = (next_claim_sec + 59) // 60 if next_claim_sec > 0 else 0
                        if tu_info is not None:
                            tu_info['next_reset_sec'] = next_roll_sec
                            tu_info['claim_reset_sec'] = next_claim_sec
                            tu_info['next_reset_min'] = next_roll_min
                            tu_info['claim_reset_min'] = next_claim_min
                        wait_seconds = next_roll_sec

                    log(f"Next rolls available in {max(0, int(round(wait_seconds / 60)))} minutes")

                    # Show prediction for status after countdown
                    predicted_status = predictStatusAfterCountdown(tu_info, max(0, int(round(wait_seconds / 60))))
                    log(f"Predicted Next Session Status: {predicted_status}")

                    next_action_time = _format_next_action_time(int(wait_seconds))
                    setDashboardState(
                        "WAITING",
                        last_action="Roll session complete",
                        next_action=f"Next rolls at {next_action_time}"
                    )
                    setConnectionStatus("Connected")
                    countdown(wait_seconds, tu_info, current_token)

                    # Loop will continue and run again
                    log("="*50)
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
                        next_action=f"Retry at {next_action_time}"
                    )
                    setConnectionStatus("Connected")
                    countdown(wait_seconds, token=current_token)
                    log("="*50)

            except Exception as e:
                log_error(f"Error during roll cycle: {e}")
                token_check = probe_token_status(current_token)
                if token_check.get('status') != 'OK':
                    log_warn("Connection issue detected after error. Attempting to reconnect...")
                    current_token = _ensure_connected(selected_user_name, show_connecting=True)
                    if not current_token:
                        break
                    log("="*50)
                    continue
                log_warn("Waiting 5 minutes before retry...")
                next_action_time = _format_next_action_time(5 * 60)
                setDashboardState(
                    "ERROR",
                    last_action=f"Error: {e}",
                    next_action=f"Retry at {next_action_time}"
                )
                setConnectionStatus("Connected")
                countdown(5 * 60, token=current_token)
                log("="*50)

    except KeyboardInterrupt:
        log_warn("\nBot stopped by user (Ctrl+C)")
    except SystemExit:
        pass
    finally:
        if listener:
            listener.stop()

        if stop_requested:
            _flush_console_input_buffer()
            setDashboardState("STOPPED", last_action="Restart requested")
            log_info("Restarting bot...")
            sys.exit(0)



if __name__ == "__main__":
    main()



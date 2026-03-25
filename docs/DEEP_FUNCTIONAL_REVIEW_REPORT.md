# Deep Functional Review + Fix + Upgrade + Feature Suggestions (Discord Automation Bot)

Backup
- Backup created at `backups/20260115_183615` (full project snapshot excluding nested `backups`)
- Initial report diffs captured against `backups/20260115_152215`
- Payload snippets below are sanitized for the public repo and use placeholder IDs/usernames.

## 1) Project behavior map
- Entry point: `src/Bot.py` prompts for user selection from `src/Vars.py`, initializes session via `initializeSession`, then loops `enhancedRoll` -> compute next reset -> `countdown` -> repeat until stop.
- Stop behavior: Alt+C sets `stop_requested` and `setStopRequested(True)`; loops and sleeps check stop state to exit early.
- Countdown flow: `countdown` updates dashboard (if enabled), prints timer, and spawns a single scan worker at a time to call `pollExternalRolls` while waiting.
- `src/Function.py` responsibilities: session init, /tu parsing, wishlist fetch, eligibility checks, rolling/claim logic, kakera reactions, claim stats, dashboard rendering, manual-claim scan.
- `src/Fetch.py` responsibilities: HTTP fetch/poll logic with after_id paging, interaction/author filtering, retry/backoff, stop checks.
- Shared state: `_stop_requested`, `current_user_*`, `external_after_ids`, `processed_external_roll_ids`, dashboard fields, and per-session logs in `logs/`.

## 2) Functional issues found (ranked)
Critical
- None observed.

High
- `src/Function.py` `isSessionEligible`: Symptom: sessions proceeded with zero rolls; root cause: `or True` forced roll path; fix: compute roll eligibility based on roll count and reset timers.
- `src/Function.py` `enhancedRoll`: Symptom: already-claimed cards treated as claimable; root cause: only checked `footer.icon_url`; fix: use `_card_is_claimed` to honor footer text or icon.

Medium
- `src/Function.py` `enhancedRoll` candidate selection: Symptom: star wishlist cards could lose to lower priority; root cause: sorting ignored `priority`; fix: choose by `priority` then kakera.
- `src/Function.py` `_message_has_kakera_button`: Symptom: missed kakera buttons on multi-row components; root cause: only inspected first row; fix: iterate all rows/components.
- `src/Function.py` `useSpecialCommand` + `enhancedRoll`: Symptom: /rolls upvote prompt treated as success; root cause: no content check and result ignored; fix: detect upvote prompt and skip session when /rolls fails.
- `src/Fetch.py` `wait_for_interaction_message`/`wait_for_author_message`: Symptom: repeated polling of the same message batch; root cause: `after_id` advanced only when None; fix: always advance to latest id each attempt.
- `src/Function.py` external scan memory growth: Symptom: unbounded `processed_external_roll_ids` over long runtimes; root cause: set never evicted; fix: bounded queue with eviction.
- `src/Function.py` `saveClaimStats`: Symptom: potential data loss on interruption; root cause: non-atomic writes; fix: temp write + `os.replace`.
- `src/Function.py` `sendReport`/`fetchAndParseMudaeWishlist`: Symptom: stop/restart lag; root cause: blocking `time.sleep`; fix: `_sleep_interruptible`.

Low
- `src/Function.py` `getTuInfo`: Symptom: duplicate fallback name blocks; root cause: copy/paste; fix: single list of fallback names.
- `src/Function.py` `_render_dashboard_win32`: Symptom: Windows dashboard refresh failures; root cause: `last_lines` undefined; fix: read value from state.
- `src/Function.py` `parseMudaeTime`/`extractCardInfo`: Symptom: narrow time parsing and missing title fallback; root cause: limited regex/assumption; fix: accept spaced formats and fall back to `embed.title`.
- `src/Function.py` `render_dashboard`: Symptom: dashboard blocks stacked and required scrolling; root cause: fallback rendering appended full blocks; fix: force-clear redraw toggle to keep one interface.

## 3) Fixes and upgrades implemented
- `src/Function.py`: fixed eligibility logic, claim detection, wishlist priority selection, multi-row kakera button scanning, and safe default `messageFlags`.
- `src/Function.py`: bounded external roll de-dup, atomic stats writes, interruptible sleeps, and HTTP timeouts for identity/claim/pokeslot requests.
- `src/Fetch.py`: polling now advances `after_id` on every attempt to prevent duplicate processing.
- `src/Bot.py`: dashboard state (RUNNING/WAITING/ERROR/STOPPED) with last/next action; countdown scan cadence now adapts to remaining wait time.
- `src/Function.py` + `src/Fetch.py`: interaction id correlation with safe fallback when interaction ids are absent.
- `src/Function.py` + local runtime `config/last_seen.json`: persist last-seen message ids per channel with atomic flush and restart seeding.
- `src/Function.py`: log candidate embed image URL and expose it in the dashboard best-candidate section.
- `src/Vars.py` + `src/Function.py`: force-clear dashboard redraw toggle to prevent stacked output.
- Syntax validation run: `.\.venv\Scripts\python.exe -m py_compile src\Bot.py src\Function.py src\Fetch.py src\Vars.py`

## 4) Rawresponse.md-informed validation
Search approach
- Targeted searches only; no linear scan.
- Keys searched (within limits): `interaction`, `interaction_metadata`, `custom_id`, `Belongs to`, `description`, `Wishlist`.

Confirmed structures (path -> example snippet)
- `message.interaction.user.id`, `message.interaction.name`:
```json
..."interaction":{"id":"145500000000000001","type":2,"name":"tu","user":{"id":"345678901234567890","username":"player_one","global_name":"Player One"}}...
```
- `message.interaction_metadata.user.id`, `message.interaction_metadata.name`:
```json
..."interaction_metadata":{"id":"145500000000000001","type":2,"user":{"id":"345678901234567890","username":"player_one","global_name":"Player One"},"name":"tu","command_type":1}...
```
- `message.components[].components[].custom_id`, `message.components[].components[].emoji.name`:
```json
..."components":[{"type":1,"id":1,"components":[{"type":2,"id":2,"custom_id":"145500000000000099p234567890123456789p0","style":2,"emoji":{"name":"\\ud83e\\uddf8"}}]}]...
```
- `message.embeds[0].description`, `message.embeds[0].author.name`:
```json
..."embeds":[{"type":"rich","description":"**Nene Kusanagi** \\u2b50\\n**Frill**...","author":{"name":"player_one's Wishlist - 15/15 $wl, 1/1 $sw"}}]...
```
- `message.embeds[0].footer.text`, `message.embeds[0].footer.icon_url` (claimed card footer):
```json
..."footer":{"text":"Belongs to player_one","icon_url":"https://cdn.discordapp.com/avatars/..."}...
```

Mismatches fixed
- Claim detection now uses footer text or icon (was icon-only), aligning with raw `Belongs to` footer text.
- Kakera button detection now scans all component rows, matching the multi-row component structure.

Unconfirmed within limits
- Kakera-specific emoji names (e.g., containing "kakera") were not found within the search limit. The parser still relies on `emoji.name` substring and should be revisited if you capture kakera button emoji names or ids in Rawresponse.

## 5) New feature suggestions (based on real raw structures)
1. Multi-row component kakera detection (Implemented)
   - What: detect kakera buttons across any action row.
   - Why: avoids missed reactions when buttons are not in the first row.
   - Data: `message.components[].components[].emoji.name`, `message.components[].components[].custom_id`.
   - Outline: iterate all component rows; match kakera name; click `custom_id`.

2. Bounded message de-dup + after_id advancement (Implemented)
   - What: advance `after_id` each poll and keep LRU cache of seen ids.
   - Why: prevents reprocessing and memory growth over long runtimes.
   - Data: `message.id`, `message.channel_id`.
   - Outline: move `after_id` to latest id per poll; cap id cache size with eviction.

3. Interaction id correlation (Implemented)
   - What: tie responses to the exact interaction id.
   - Why: reduces false positives in busy channels.
   - Data: `message.interaction.id` or `message.interaction_metadata.id`, `message.interaction.user.id`.
   - Outline: store last interaction id when triggering command; filter messages by same id.

4. Persist last seen message id per channel (Implemented)
   - What: avoid reprocessing after restarts.
   - Why: stable long-runtime behavior, fewer duplicates on reboot.
   - Data: `message.channel_id`, `message.id`.
   - Outline: store latest id in the local runtime file `config/last_seen.json`; load at startup to seed `after_id`.

5. Candidate image preview (Implemented)
   - What: log or cache best-candidate image URL for verification.
   - Why: quicker manual validation or audit of claims.
   - Data: `message.embeds[0].image.url`.
   - Outline: extract URL for best candidate, log it, optionally cache thumbnail.

6. Embed title fallback for card names (Implemented)
   - What: use `embed.title` when `embed.author.name` is absent.
   - Why: resilient parsing across embed variants.
   - Data: `message.embeds[0].title`, `message.embeds[0].author.name`.
   - Outline: prefer `author.name`, fall back to `title`.

## 6) Patch output
```diff
diff --git "a/backups\\20260115_152215\\src\\Bot.py" "b/src\\Bot.py"
index 6470822..5957b47 100644
--- "a/backups\\20260115_152215\\src\\Bot.py"
+++ "b/src\\Bot.py"
@@ -11,6 +11,7 @@ from Function import (
     setCurrentUser,
     setStopRequested,
     predictStatusAfterCountdown,
+    setDashboardState,
     startDashboardCountdown,
     updateDashboardCountdown,
     stopDashboardCountdown,
@@ -55,6 +56,21 @@ try:
 except Exception as e:
     log(f"⚠️  Could not start key listener: {e} (Alt+C restart disabled)")

+def _format_next_action_time(seconds: int) -> str:
+    if seconds <= 0:
+        return "now"
+    target = time.localtime(time.time() + seconds)
+    return time.strftime("%H:%M:%S", target)
+
+def _scan_interval_bounds(remaining_sec: int) -> tuple[float, float]:
+    if remaining_sec >= 60 * 30:
+        return (2.0, 8.0)
+    if remaining_sec >= 60 * 10:
+        return (1.0, 6.0)
+    if remaining_sec >= 60 * 3:
+        return (0.6, 4.0)
+    return (0.4, 2.0)
+
 def countdown(seconds: int, status: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> None:
     """Display a countdown timer that updates each second"""
     global stop_requested
@@ -64,6 +80,7 @@ def countdown(seconds: int, status: Optional[Dict[str, Any]] = None, token: Opti
     last_scan = 0.0
     scan_inflight = False
     scan_lock = threading.Lock()
+    scan_bounds = _scan_interval_bounds(int(seconds))
     end_time = time.monotonic() + max(0, seconds)
     last_display = None
     next_tick = time.monotonic()
@@ -82,6 +99,8 @@ def countdown(seconds: int, status: Optional[Dict[str, Any]] = None, token: Opti
                 scan_interval_sec = max(0.5, scan_interval_sec * 0.7)
             else:
                 scan_interval_sec = min(5.0, scan_interval_sec * 1.3)
+            min_bound, max_bound = scan_bounds
+            scan_interval_sec = max(min_bound, min(max_bound, scan_interval_sec))
             last_scan = now
             scan_inflight = False

@@ -97,6 +116,9 @@ def countdown(seconds: int, status: Optional[Dict[str, Any]] = None, token: Opti
         if now >= next_tick:
             remaining = max(0, int(math.ceil(end_time - now)))
             if remaining != last_display:
+                scan_bounds = _scan_interval_bounds(remaining)
+                min_bound, max_bound = scan_bounds
+                scan_interval_sec = max(min_bound, min(max_bound, scan_interval_sec))
                 if DASHBOARD_ENABLED:
                     updateDashboardCountdown(remaining)
                 else:
@@ -174,6 +196,7 @@ log("💡 Press Alt+C to restart bot and select a different user")

 try:
     initializeSession(selected_user['token'], selected_user['name'])
+    setDashboardState("READY", last_action="Session initialized")
 except KeyboardInterrupt:
     log("\n🛑 Bot stopped by user (Ctrl+C)")
     sys.exit(0)
@@ -187,6 +210,7 @@ try:
                 break
             
             # Run enhanced roll session
+            setDashboardState("RUNNING", last_action="Starting roll session")
             tu_info = enhancedRoll(selected_user['token'])
             
             # Check again after roll session completes
@@ -209,6 +233,12 @@ try:
                 predicted_status = predictStatusAfterCountdown(tu_info, max(0, int(round(wait_seconds / 60))))
                 log(f"Predicted Next Session Status: {predicted_status}")

+                next_action_time = _format_next_action_time(int(wait_seconds))
+                setDashboardState(
+                    "WAITING",
+                    last_action="Roll session complete",
+                    next_action=f"Next rolls at {next_action_time}"
+                )
                 countdown(wait_seconds, tu_info, selected_user['token'])

                 # Loop will continue and run again
@@ -216,12 +246,24 @@ try:
             else:
                 log("⚠️  No reset time available, waiting 10 minutes before retry...")
                 wait_seconds = 10 * 60
+                next_action_time = _format_next_action_time(wait_seconds)
+                setDashboardState(
+                    "WAITING",
+                    last_action="No reset time available",
+                    next_action=f"Retry at {next_action_time}"
+                )
                 countdown(wait_seconds, token=selected_user['token'])
                 log("="*50)
         
         except Exception as e:
             log(f"❌ Error during roll cycle: {e}")
             log("⚠️  Waiting 5 minutes before retry...")
+            next_action_time = _format_next_action_time(5 * 60)
+            setDashboardState(
+                "ERROR",
+                last_action=f"Error: {e}",
+                next_action=f"Retry at {next_action_time}"
+            )
             countdown(5 * 60, token=selected_user['token'])
             log("="*50)

@@ -234,6 +276,7 @@ finally:
         listener.stop()
     
     if stop_requested:
+        setDashboardState("STOPPED", last_action="Restart requested")
         log("🔄 Restarting bot...")
         sys.exit(0)
```

```diff
diff --git "a/backups\\20260115_152215\\src\\Fetch.py" "b/src\\Fetch.py"
index aecfc38..67d44dd 100644
--- "a/backups\\20260115_152215\\src\\Fetch.py"
+++ "b/src\\Fetch.py"
@@ -201,10 +201,9 @@ def wait_for_interaction_message(
         )
         if matches:
             return response, messages, matches[0]
-        if base_after is None:
-            newest = get_latest_message_id(messages)
-            if newest:
-                base_after = newest
+        newest = get_latest_message_id(messages)
+        if newest and newest != base_after:
+            base_after = newest
         retry_after = _parse_retry_after(response)
@@ -248,10 +247,9 @@ def wait_for_author_message(
             candidates = [msg for msg in candidates if content_contains in msg.get('content', '')]
         if candidates:
             return response, messages, candidates[0]
-        if base_after is None:
-            newest = get_latest_message_id(messages)
-            if newest:
-                base_after = newest
+        newest = get_latest_message_id(messages)
+        if newest and newest != base_after:
+            base_after = newest
         retry_after = _parse_retry_after(response)
```

```diff
diff --git "a/backups\\20260115_152215\\src\\Function.py" "b/src\\Function.py"
index ade87b5..375f475 100644
--- "a/backups\\20260115_152215\\src\\Function.py"
+++ "b/src\\Function.py"
@@ -1,5 +1,6 @@
-from typing import Optional, Dict, Any, Tuple, List, Set, cast
+from typing import Optional, Dict, Any, Tuple, List, Set, cast, Deque
 from datetime import datetime, timezone, timedelta
+from collections import deque
@@ -35,6 +36,8 @@ current_user_ids: Dict[str, str] = {}
 current_user_names: Dict[str, str] = {}
 current_user_name: Optional[str] = None
 processed_external_roll_ids: Set[str] = set()
+_processed_external_roll_queue: Deque[str] = deque()
+EXTERNAL_ROLL_ID_CACHE = 5000
@@ -80,6 +83,9 @@ _dashboard_state: Dict[str, Any] = {
     'countdown_total': 0,
     'countdown_remaining': 0,
     'countdown_status': None,
+    'state': 'INIT',
+    'last_action': '',
+    'next_action': '',
@@ -128,7 +134,8 @@ def _ensure_user_identity(token: Optional[str]) -> Tuple[Optional[str], Optional
     try:
         response = requests.get(
             'https://discord.com/api/v9/users/@me',
-            headers={'authorization': token}
+            headers={'authorization': token},
+            timeout=Fetch.get_timeout()
         )
@@ -474,14 +481,19 @@ def _message_has_kakera_button(message: Dict[str, Any]) -> bool:
         components = message.get('components', [])
         if not components:
             return False
-        message_components = components[0].get('components', [])
-        for component in message_components:
-            emoji = component.get('emoji')
-            if not isinstance(emoji, dict):
+        for row in components:
+            if not isinstance(row, dict):
                 continue
-            name = emoji.get('name', '')
-            if isinstance(name, str) and 'kakera' in name.lower():
-                return True
+            message_components = row.get('components', [])
+            for component in message_components:
+                if not isinstance(component, dict):
+                    continue
+                emoji = component.get('emoji')
+                if not isinstance(emoji, dict):
+                    continue
+                name = emoji.get('name', '')
+                if isinstance(name, str) and 'kakera' in name.lower():
+                    return True
@@ -491,6 +503,17 @@ def _name_in_whitelist(name: Optional[str], whitelist: List[str]) -> bool:
         return False
     return name.lower() in {entry.lower() for entry in whitelist}

+def _track_external_roll_id(message_id: str) -> bool:
+    """Track a processed external roll id with bounded memory."""
+    if message_id in processed_external_roll_ids:
+        return False
+    processed_external_roll_ids.add(message_id)
+    _processed_external_roll_queue.append(message_id)
+    if len(_processed_external_roll_queue) > EXTERNAL_ROLL_ID_CACHE:
+        old_id = _processed_external_roll_queue.popleft()
+        processed_external_roll_ids.discard(old_id)
+    return True
@@ -499,10 +522,12 @@ def extractCardInfo(message: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
         embed = embeds[0]
         author = embed.get('author', {})
-        name = author.get('name')
+        name = author.get('name') or embed.get('title')
         if not name:
             return None
         description = embed.get('description', '')
+        if not isinstance(description, str):
+            description = ""
@@ -561,8 +586,10 @@ def saveClaimStats(stats: Dict[str, Any]) -> None:
         stats['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
-        with open(CLAIM_STATS_FILE, 'w', encoding='utf-8') as f:
+        temp_path = f"{CLAIM_STATS_FILE}.tmp"
+        with open(temp_path, 'w', encoding='utf-8') as f:
             json.dump(stats, f, indent=2, ensure_ascii=False)
+        os.replace(temp_path, CLAIM_STATS_FILE)
@@ -706,28 +733,25 @@ def parseMudaeTime(time_str: str) -> int:
-    time_str = time_str.strip()
+    if not time_str:
+        return 0
+    time_str = time_str.strip().lower()
@@ -775,6 +799,9 @@ def _dashboard_reset_session(session_start: Optional[str] = None) -> None:
     _dashboard_state['countdown_total'] = 0
     _dashboard_state['countdown_remaining'] = 0
     _dashboard_state['countdown_status'] = None
+    _dashboard_state['state'] = 'INIT'
+    _dashboard_state['last_action'] = ''
+    _dashboard_state['next_action'] = ''
@@ -852,6 +879,16 @@ def _dashboard_set_predicted(status: str, minutes_to_wait: int) -> None:
     _dashboard_state['predicted_at'] = time.strftime("%H:%M", next_time)
     _dashboard_state['predicted_status'] = status

+def setDashboardState(state: str, last_action: Optional[str] = None, next_action: Optional[str] = None) -> None:
+    if not DASHBOARD_ENABLED:
+        return
+    _dashboard_state['state'] = state
+    if last_action is not None:
+        _dashboard_state['last_action'] = last_action
+    if next_action is not None:
+        _dashboard_state['next_action'] = next_action
+    render_dashboard()
@@ -1021,6 +1058,7 @@ def _render_dashboard_win32(lines: List[str], width: int) -> bool:
         if kernel32.SetConsoleCursorPosition(handle, coord) == 0:
             return False

+        last_lines = int(_dashboard_state.get('last_render_lines', 0) or 0)
         extra = max(0, last_lines - len(lines))
@@ -1064,6 +1102,15 @@ def render_dashboard(clear: bool = True) -> None:
     if not status and not use_countdown:
         status_lines.append("Status unavailable")
     else:
         active_status = countdown_status if use_countdown else status
+        state_label = _dashboard_state.get('state')
+        if state_label:
+            status_lines.append(f"State: {state_label}")
+        last_action = _dashboard_state.get('last_action')
+        if last_action:
+            status_lines.append(f"Last action: {last_action}")
+        next_action = _dashboard_state.get('next_action')
+        if next_action:
+            status_lines.append(f"Next action: {next_action}")
@@ -1507,6 +1554,7 @@ def initializeSession(token: str, expected_username: str = "") -> None:
         session_start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
         _dashboard_reset_session(session_start_time)
         processed_external_roll_ids.clear()
+        _processed_external_roll_queue.clear()
@@ -1688,22 +1736,6 @@ def getTuInfo(token: str) -> Optional[Dict[str, Any]]:
-        if token_name and token_name.lower() not in {n.lower() for n in fallback_names}:
-            fallback_names.append(token_name)
-        if current_user_name and current_user_name.lower() not in {n.lower() for n in fallback_names}:
-            fallback_names.append(current_user_name)
-        fallback_names: List[str] = []
-        if user_name:
-            fallback_names.append(user_name)
-        token_name = getUserNameForToken(token)
-        if token_name and token_name.lower() not in {n.lower() for n in fallback_names}:
-            fallback_names.append(token_name)
-        if current_user_name and current_user_name.lower() not in {n.lower() for n in fallback_names}:
-            fallback_names.append(current_user_name)
-        fallback_names: List[str] = []
-        if user_name:
-            fallback_names.append(user_name)
-        token_name = getUserNameForToken(token)
         if token_name and token_name.lower() not in {n.lower() for n in fallback_names}:
             fallback_names.append(token_name)
@@ -1938,6 +1970,9 @@ def useSpecialCommand(token: str, command_name: str) -> bool:
         response_content: str = cast(str, our_response.get('content', ''))

         # Check for /rolls cooldown: "the roulette is limited to 14 uses per hour. **X** min left"
+        if command_name == 'rolls' and ('Upvote Mudae' in response_content or 'one vote per' in response_content):
+            log("/rolls requires an upvote to reset rolls (no reset applied)")
+            return False
@@ -1964,8 +1999,16 @@ def isSessionEligible(tu_info: Dict[str, Any]) -> bool:
-    has_path_to_rolls = tu_info.get('rolls', 0) > 0 or True  # We can always use /rolls if needed
+    rolls_left = tu_info.get('rolls', 0)
+    rolls_reset_min = tu_info.get('next_reset_min')
+    rolls_reset_sec = tu_info.get('next_reset_sec')
+    has_path_to_rolls = rolls_left > 0
+    if rolls_left == 0:
+        if isinstance(rolls_reset_sec, int) and rolls_reset_sec == 0:
+            has_path_to_rolls = True
+        elif isinstance(rolls_reset_min, int) and rolls_reset_min == 0:
+            has_path_to_rolls = True
@@ -2007,7 +2050,8 @@ def sendReport(token: str) -> Optional[list]:  # type: ignore[type-arg]
             before_id = Fetch.get_latest_message_id(pre_messages)
             bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=reportCommand)
-            time.sleep(1)
+            if _sleep_interruptible(1):
+                return None
@@ -2197,7 +2241,7 @@ def _try_external_kakera_react(
                 guildID=Vars.serverId,
                 messageID=message['id'],
-                messageFlags=message.get('flags'),
+                messageFlags=message.get('flags', 0),
@@ -2286,9 +2330,8 @@ def _scan_external_rolls(
         msg_id_str = str(msg_id)
-        if msg_id_str in processed_external_roll_ids:
+        if not _track_external_roll_id(msg_id_str):
             continue
-        processed_external_roll_ids.add(msg_id_str)
@@ -2367,7 +2410,8 @@ def _scan_external_rolls(
             r = requests.put(
                 f'https://discord.com/api/v8/channels/{Vars.channelId}/messages/{msg_id_str}/reactions/{claim_emoji}/%40me',
-                headers=auth
+                headers=auth,
+                timeout=Fetch.get_timeout()
@@ -2450,7 +2494,8 @@ def fetchAndParseMudaeWishlist(token: str) -> Dict[str, Any]:
         before_id = Fetch.get_latest_message_id(pre_messages)
         bot.triggerSlashCommand(botID, Vars.channelId, Vars.serverId, data=cmd)
-        time.sleep(1)
+        if _sleep_interruptible(1):
+            return {'status': 'error', 'error': 'interrupted'}
@@ -2681,15 +2726,23 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
         log("📊 No rolls left, using /rolls...")
-        useSpecialCommand(token, 'rolls')
+        used_rolls = useSpecialCommand(token, 'rolls')
         if _sleep_interruptible(1):
             return None
+        if not used_rolls:
+            log("Warning: /rolls unavailable; skipping roll session")
+            render_dashboard()
+            return tu_info
@@ -2779,7 +2832,7 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
-        is_claimed = 'footer' in card['embeds'][0] and 'icon_url' in card['embeds'][0]['footer']
+        is_claimed = _card_is_claimed(card)
@@ -2865,7 +2918,7 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
-                                messageFlags=card['flags'],
+                                messageFlags=card.get('flags', 0),
@@ -2948,8 +3001,8 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
-        best_candidate = sorted(candidates, key=lambda x: (-x['wishlist'], -x['kakera']))[0]
+        best_candidate = max(candidates, key=lambda x: (x.get('priority', 1), x.get('kakera', 0)))
@@ -2959,7 +3012,10 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
-        claim_reason = "✨ Wishlist match" if best_candidate['wishlist'] else f"💰 Kakera: {best_candidate['kakera']}"
+        if best_candidate['wishlist']:
+            claim_reason = f"✨ Wishlist match (priority {best_candidate.get('priority', 2)})"
+        else:
+            claim_reason = f"💰 Kakera: {best_candidate['kakera']}"
@@ -3000,7 +3056,8 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
             r = requests.put(
                 f'https://discord.com/api/v8/channels/{Vars.channelId}/messages/{best_candidate["id"]}/reactions/{emoji}/%40me',
-                headers=auth
+                headers=auth,
+                timeout=Fetch.get_timeout()
@@ -3119,7 +3176,7 @@ def enhancedRoll(token: str, initial_tu_info: Optional[Dict[str, Any]] = None) -
-            r = requests.post(url=url, headers=auth, data={'content': '$p'})
+            r = requests.post(url=url, headers=auth, data={'content': '$p'}, timeout=Fetch.get_timeout())
```

Additional patches (backup 20260115_183615)
- `git diff --no-index "backups\\20260115_183615\\src\\Function.py" "src\\Function.py"`
- `git diff --no-index "backups\\20260115_183615\\src\\Fetch.py" "src\\Fetch.py"`
- `git diff --no-index "backups\\20260115_183615\\src\\Vars.py" "src\\Vars.py"`
- local generated file omitted from the public repo: `config\\last_seen.json`

## 7) How to run + how to validate
Commands
- `run_bot.bat`
- `.\.venv\Scripts\python.exe src\Bot.py`

Manual validation steps
- Verify /tu parses correctly for claim/roll/power/daily/rt/dk in `logs/Session.<user>.log`.
- Confirm roll session stops when /rolls requires an upvote (no unintended rolling).
- Confirm countdown can be interrupted by Alt+C and stops scanning promptly.
- Watch dashboard state transitions: READY -> RUNNING -> WAITING, and ERROR on failures.
- Confirm steal-react and claim logic only triggers on unclaimed cards and respects whitelist.

Validation checklist (must include)
- [ ] Backup exists at `backups/20260115_183615` and contains original files
- [ ] Bot starts, selects account, initializes session
- [ ] Rolling triggers at expected times
- [ ] Countdown waits correctly
- [ ] Scanning thread runs once, exits cleanly, does not multiply
- [ ] Fetching detects new messages reliably without missing or duplicating
- [ ] Interaction id correlation filters responses correctly (fallback works when ids are absent)
- [ ] Local generated `config/last_seen.json` is created/updated and seeds `after_id` on restart
- [ ] Candidate image URL logs when present for best candidate
- [ ] Dashboard redraw stays in place without stacking (force clear)
- [ ] Parsing works on multiple raw variants confirmed in `logs/Rawresponse.md`
- [ ] New features function as described (dashboard state, de-dup, scan schedule, interaction correlation, last_seen, image preview)

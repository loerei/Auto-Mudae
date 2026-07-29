"""
Getraw.py - Standalone utility to fetch raw Discord API responses without running the bot
Saves responses to Getrawresult.json in the logs folder
"""

import os
import sys
import time
import requests
from typing import Dict, Any, Tuple

from mudae.config import vars as Vars
from mudae.paths import LOGS_DIR, ensure_runtime_dirs
from mudae.storage.json_array_log import append_json_array, ensure_json_array_file

try:
    import discum  # type: ignore[import]
except ImportError:
    print("ERROR: discum library not found. Please install it: pip install discum")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 100

ensure_runtime_dirs()


def getClientAndAuth(token: str) -> Tuple[Any, Dict[str, str]]:
    """Create bot client and auth headers for a given token"""
    auth = {'authorization': token}
    bot = discum.Client(token=token, log=False)
    return bot, auth


def getUrl() -> str:
    """Get the message URL for the configured channel"""
    return f'{Vars.DISCORD_API_BASE}/{Vars.DISCORD_API_VERSION_MESSAGES}/channels/{Vars.channelId}/messages'


def getResultFilePath() -> str:
    """Get the path to Getrawresult.json"""
    return os.fspath(LOGS_DIR / "Getrawresult.json")


def logRawResponse(label: str, response_data: Any) -> None:
    """Log raw Discord API response to Getrawresult.json file"""
    try:
        result_file = getResultFilePath()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry: Dict[str, Any] = {
            'ts': timestamp,
            'label': label,
            'source': 'Getraw.logRawResponse'
        }
        if isinstance(response_data, requests.Response):
            entry['type'] = 'http'
            entry['status_code'] = response_data.status_code
            entry['headers'] = dict(response_data.headers)
            entry['content_type'] = response_data.headers.get('Content-Type', 'N/A')
            entry['body_text'] = response_data.text
            try:
                entry['body_json'] = response_data.json()
                entry['parse_error'] = None
            except Exception as exc:
                entry['body_json'] = None
                entry['parse_error'] = str(exc)
        else:
            entry['type'] = 'data'
            entry['body_text'] = str(response_data)
            entry['body_json'] = None
            entry['parse_error'] = None

        append_json_array(result_file, entry)
        print(f"Logged: {label}")
    except Exception as e:
        print(f"ERROR: Failed to log response: {e}")


def initializeResultFile() -> None:
    """Initialize Getrawresult.json if it doesn't exist"""
    result_file = getResultFilePath()
    ensure_json_array_file(result_file)
    print(f"Using {os.path.basename(result_file)}")


def selectUser() -> Dict[str, str]:
    """Prompt user to select which account to use"""
    print("\n" + "="*50)
    print("Available Users:")
    print("="*50)

    if not Vars.tokens:
        raise RuntimeError("No tokens configured in Vars.tokens")

    for i, user in enumerate(Vars.tokens, 1):
        print(f"{i}. {user['name']}")

    while True:
        try:
            choice = input("\nSelect user (enter number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(Vars.tokens):
                selected = Vars.tokens[idx]
                print(f"Selected: {selected['name']}")
                return selected
            print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a valid number.")


def selectMessageLimit(default_limit: int = DEFAULT_MESSAGE_LIMIT) -> int:
    """Prompt user to select how many latest messages to fetch"""
    print("\n" + "="*50)
    print("Fetch Options:")
    print("="*50)
    print(f"Enter number of latest messages to fetch (1-{MAX_MESSAGE_LIMIT}).")
    print(f"Press Enter for default: {default_limit}")

    while True:
        try:
            choice = input("\nNumber of messages: ").strip()
            if choice == "":
                return default_limit
            limit = int(choice)
            if 1 <= limit <= MAX_MESSAGE_LIMIT:
                return limit
            print(f"Please enter a number between 1 and {MAX_MESSAGE_LIMIT}.")
        except ValueError:
            print("Please enter a valid number.")


def fetchRawMessages(token: str, limit: int) -> None:
    """Fetch raw message data from Discord API"""
    try:
        print("\nFetching raw messages from Discord...")
        _bot, auth = getClientAndAuth(token)
        url = getUrl()

        r = requests.get(url, headers=auth, params={"limit": limit})
        logRawResponse(f"GET /messages?limit={limit}", r)

        if r.status_code == 200:
            messages = r.json()
            print(f"Fetched {len(messages)} messages")
        else:
            print(f"Failed to fetch messages. Status: {r.status_code}")
    except Exception as e:
        print(f"ERROR: Failed to fetch raw messages: {e}")
        logRawResponse("ERROR", str(e))


def main() -> None:
    """Main function"""
    print("\n" + "="*50)
    print("Getraw - Raw Discord API Response Fetcher")
    print("="*50)

    try:
        initializeResultFile()
        selected_user = selectUser()
        limit = selectMessageLimit()

        session_start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        result_file = getResultFilePath()
        append_json_array(result_file, {
            'ts': session_start,
            'type': 'session_start',
            'user': selected_user['name'],
            'channel_id': Vars.channelId,
            'guild_id': Vars.serverId,
            'limit': limit,
            'source': 'Getraw.main'
        })

        fetchRawMessages(selected_user['token'], limit)
    except KeyboardInterrupt:
        print("\nCancelled by user.")


if __name__ == "__main__":
    main()



import json
import os
from typing import Any, Dict, Optional, cast

from mudae.parsers.time_parser import _parse_discord_timestamp
from mudae.paths import LOGS_DIR

from mudae.core import session_engine as Function


def test_detect_manual_claim_parses_kakera_not_key() -> None:
    message: Dict[str, Any] = {
        "id": "msg1",
        "timestamp": "2026-02-02T00:00:00.000000+00:00",
        "edited_timestamp": "2026-02-02T00:00:05.000000+00:00",
        "embeds": [
            {
                "author": {"name": "Yui Hirasawa"},
                "description": "K-ON!\\nClaims: #242\\nLikes: #433\\n**340**<:kakera:469835869059153940>\\n<:key:1> (**1**)",
                "footer": {"text": "Belongs to Tester"},
            }
        ],
    }
    claim = Function.detectManualClaim(message, target_username="Tester")
    assert claim is not None
    assert claim["is_ours"] is True
    assert claim["kakera_base"] == 340
    assert claim["kakera"] == 340


def test_scan_for_manual_claim_filters_old_claims_by_edited_timestamp() -> None:
    # This test writes a temporary SessionRawresponse.json at the standard log path.
    log_path = os.fspath(LOGS_DIR / "SessionRawresponse.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    backup: Optional[bytes] = None
    if os.path.exists(log_path):
        with open(log_path, "rb") as f:
            backup = f.read()

    try:
        old_claim_msg: Dict[str, Any] = {
            "id": "old",
            "timestamp": "2026-01-01T00:00:00.000000+00:00",
            "edited_timestamp": "2026-01-01T00:00:10.000000+00:00",
            "embeds": [
                {
                    "author": {"name": "Old Character"},
                    "description": "Series\\n**10**<:kakera:469835869059153940>",
                    "footer": {"text": "Belongs to Tester"},
                }
            ],
        }
        new_claim_msg: Dict[str, Any] = {
            "id": "new",
            "timestamp": "2026-02-02T00:00:00.000000+00:00",
            "edited_timestamp": "2026-02-02T00:00:10.000000+00:00",
            "embeds": [
                {
                    "author": {"name": "New Character"},
                    "description": "Series\\n**20**<:kakera:469835869059153940>",
                    "footer": {"text": "Belongs to Tester"},
                }
            ],
        }

        entry = {
            "ts": "2026-02-02 00:00:11",
            "label": "GET - /messages",
            "source": "test",
            "type": "http",
            "status_code": 200,
            "headers": {},
            "content_type": "application/json",
            "body_text": "",
            "body_json": [new_claim_msg, old_claim_msg],
            "parse_error": None,
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump([entry], f, ensure_ascii=False)
            f.write("\n")

        token = "TESTTOKEN"
        start_epoch = _parse_discord_timestamp("2026-02-01T00:00:00+00:00")
        assert start_epoch is not None
        Function._session_start_epoch[token] = cast(float, start_epoch)
        Function._processed_manual_claim_ids[token] = set()

        claims = Function.scanForManualClaims(token, "Tester", include_persistent=False)
        assert len(claims) == 1
        assert claims[0]["character_name"] == "New Character"
        assert claims[0]["message_id"] == "new"
    finally:
        if backup is not None:
            with open(log_path, "wb") as f:
                f.write(backup)
        else:
            try:
                os.remove(log_path)
            except OSError:
                pass




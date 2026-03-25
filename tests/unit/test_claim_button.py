from mudae.parsers.reactions import _find_claim_button


def test_find_claim_button_from_wish_payload() -> None:
    message = {
        "components": [
            {
                "type": 1,
                "id": 1,
                "components": [
                    {
                        "type": 2,
                        "id": 2,
                        "custom_id": "555566667777888899p234567890123456789p0",
                        "style": 2,
                        "emoji": {"name": "\u2764\ufe0f"},
                    }
                ],
            }
        ]
    }
    assert _find_claim_button(message) == "555566667777888899p234567890123456789p0"


def test_find_claim_button_none_on_normal_card() -> None:
    message = {"components": []}
    assert _find_claim_button(message) is None


def test_find_claim_button_ignores_kakera_buttons() -> None:
    message = {
        "components": [
            {
                "type": 1,
                "components": [
                    {"type": 2, "custom_id": "kakera123", "emoji": {"name": "kakera"}}
                ],
            }
        ]
    }
    assert _find_claim_button(message) is None


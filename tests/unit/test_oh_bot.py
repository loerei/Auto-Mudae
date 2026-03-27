from mudae.ouro.Oh_bot import _click_consumes_turn, _parse_reward_content, _resolve_final_click_color


def test_parse_reward_content_keeps_turns_into_resolution() -> None:
    reward = _parse_reward_content(
        "<:spD:1> turns into <:spP:2>\n<:spP:2> **+5**",
        {"spD": "DARK", "spP": "PURPLE"},
    )

    assert reward["reward_color"] == "PURPLE"
    assert reward["resolved_color"] == "PURPLE"


def test_dark_turning_into_purple_does_not_consume_turn() -> None:
    reward = {"reward_color": "PURPLE", "resolved_color": "PURPLE"}

    final_color = _resolve_final_click_color("DARK", reward)

    assert final_color == "PURPLE"
    assert _click_consumes_turn(final_color) is False


def test_nonpurple_result_still_consumes_turn() -> None:
    reward = {"reward_color": "GREEN", "resolved_color": "GREEN"}

    final_color = _resolve_final_click_color("DARK", reward)

    assert final_color == "GREEN"
    assert _click_consumes_turn(final_color) is True


def test_observed_purple_without_reward_message_keeps_turn() -> None:
    final_color = _resolve_final_click_color("PURPLE", None)

    assert final_color == "PURPLE"
    assert _click_consumes_turn(final_color) is False

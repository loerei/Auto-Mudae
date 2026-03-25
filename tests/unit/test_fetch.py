from mudae.discord import fetch as Fetch


def test_extract_interaction_user_id_prefers_interaction() -> None:
    message = {
        "interaction": {"user": {"id": "111"}, "name": "tu"},
        "interaction_metadata": {"user": {"id": "222"}, "name": "wl"},
    }
    assert Fetch.extract_interaction_user_id(message) == "111"


def test_extract_interaction_user_id_falls_back_to_interaction_metadata() -> None:
    message = {"interaction_metadata": {"user": {"id": "222"}, "name": "wl"}}
    assert Fetch.extract_interaction_user_id(message) == "222"


def test_extract_interaction_name_reads_interaction_metadata() -> None:
    message = {"interaction_metadata": {"name": "wl"}}
    assert Fetch.extract_interaction_name(message) == "wl"


def test_filter_messages_matches_user_and_command() -> None:
    messages = [
        {"interaction": {"user": {"id": "1"}, "name": "tu"}},
        {"interaction_metadata": {"user": {"id": "2"}, "name": "wl"}},
        {"interaction": {"user": {"id": "1"}, "name": "wl"}},
    ]
    filtered = Fetch.filter_messages(messages, user_id="1", command_name="wl", require_interaction=True)
    assert filtered == [{"interaction": {"user": {"id": "1"}, "name": "wl"}}]


def test_filter_messages_matches_author_id() -> None:
    messages = [
        {"author": {"id": "bot"}, "content": "a"},
        {"author": {"id": "user"}, "content": "b"},
    ]
    filtered = Fetch.filter_messages(messages, author_id="bot")
    assert filtered == [{"author": {"id": "bot"}, "content": "a"}]


def test_get_latest_message_id_handles_empty() -> None:
    assert Fetch.get_latest_message_id([]) is None


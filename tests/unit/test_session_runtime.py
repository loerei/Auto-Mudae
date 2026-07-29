from mudae.core.session_runtime import SessionRuntime


def test_session_runtime_initialization():
    runtime = SessionRuntime()
    assert runtime.state_engine is not None
    assert runtime.dashboard_renderer is not None


def test_session_runtime_set_user_identity():
    runtime = SessionRuntime()
    runtime.set_user_identity("tok_123", "usr_999", "Alice")
    snapshot = runtime.get_snapshot()
    assert snapshot["current_user_id"] == "usr_999"
    assert snapshot["current_user_names"]["tok_123"] == "Alice"


def test_session_runtime_status_and_wishlist_sync():
    runtime = SessionRuntime()
    status_data = {"user_id": "usr_999", "rolls": 10, "usables": 10}
    runtime.set_status(status_data)

    snapshot = runtime.get_snapshot()
    assert "usr_999" in snapshot["last_tu_info_cache"]
    assert snapshot["last_tu_info_cache"]["usr_999"]["rolls"] == 10

    wishlist_data = {"user_id": "usr_999", "star_wishes": ["Rem"], "regular_wishes": ["Asuka"]}
    runtime.set_wishlist(wishlist_data)

    snapshot2 = runtime.get_snapshot()
    assert "usr_999" in snapshot2["last_wishlist_cache"]
    assert snapshot2["last_wishlist_cache"]["usr_999"]["star_wishes"] == ["Rem"]


def test_session_runtime_connection_lifecycle():
    runtime = SessionRuntime()
    runtime.set_connection_status("RECONNECTING")
    runtime.start_connection_retry(15)
    runtime.update_connection_retry(10)
    runtime.stop_connection_retry()
    runtime.set_connection_status("CONNECTED")
    assert True


def test_session_runtime_reset():
    runtime = SessionRuntime()
    runtime.set_user_identity("tok_123", "usr_999", "Alice")
    runtime.reset_session()
    snapshot = runtime.get_snapshot()
    assert snapshot["current_user_id"] is None

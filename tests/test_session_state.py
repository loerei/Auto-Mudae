from mudae.core.session_state import SessionStateEngine

def test_session_state_user_info():
    engine = SessionStateEngine()
    engine.set_user_info("acc1", "12345", "TestUser")
    snapshot = engine.get_snapshot()
    assert snapshot["current_user_id"] == "12345"
    assert snapshot["current_user_ids"]["acc1"] == "12345"
    assert snapshot["current_user_names"]["acc1"] == "TestUser"

def test_session_state_tu_update_event():
    engine = SessionStateEngine()
    actions = engine.process_event("TU_UPDATE", {"user_id": "12345", "tu_data": {"rolls": 10}})
    assert len(actions) == 1
    assert actions[0].action_type == "TU_CACHED"
    assert actions[0].target_user_id == "12345"
    
    snapshot = engine.get_snapshot()
    assert snapshot["last_tu_info_cache"]["12345"]["rolls"] == 10

def test_session_state_reset():
    engine = SessionStateEngine()
    engine.set_user_info("acc1", "12345", "TestUser")
    engine.reset_state()
    snapshot = engine.get_snapshot()
    assert snapshot["current_user_id"] is None
    assert len(snapshot["current_user_ids"]) == 0

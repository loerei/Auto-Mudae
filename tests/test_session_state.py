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

def test_session_state_tu_and_wishlist_caching():
    engine = SessionStateEngine()
    engine.cache_tu_info("tok_abc", {"rolls": 5, "current_power": 80, "dk_ready": True})
    cached_tu = engine.get_cached_tu_info("tok_abc")
    assert cached_tu is not None
    assert cached_tu["rolls"] == 5

    # Test synthesis after $dk
    synth_dk = engine.synthesize_tu_after_dk("tok_abc", cached_tu, max_power=100)
    assert synth_dk["dk_ready"] is False
    assert synth_dk["current_power"] == 100

    # Test synthesis after $rt
    synth_rt = engine.synthesize_tu_after_rt("tok_abc", synth_dk)
    assert synth_rt["rt_available"] is False
    assert synth_rt["can_claim_now"] is True

    # Test wishlist caching
    engine.cache_wishlist("tok_abc", {"status": "success", "star_wishes": ["Rem"]})
    cached_wl = engine.get_cached_wishlist("tok_abc")
    assert cached_wl is not None
    assert cached_wl["star_wishes"] == ["Rem"]

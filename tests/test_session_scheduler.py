from mudae.core.session_scheduler import SessionScheduler

def test_session_scheduler_keys():
    scheduler = SessionScheduler()
    bucket = scheduler.get_hour_bucket(1700000000.0)
    key = scheduler.get_entry_key(101, "kakera", bucket)
    assert "101" in key
    assert "kakera" in key

def test_session_scheduler_seen():
    scheduler = SessionScheduler()
    key = "2026-07-30|101|kakera"
    assert not scheduler.is_seen(key)
    scheduler.mark_seen(key)
    assert scheduler.is_seen(key)

def test_session_scheduler_state(tmp_path):
    state_file = str(tmp_path / "test_auto_give_state.json")
    scheduler = SessionScheduler(state_file_path=state_file)
    state = scheduler.load_state()
    assert "entries" in state
    
    state["entries"]["key1"] = {"status": "ok"}
    scheduler.save_state(state)
    
    loaded = scheduler.load_state()
    assert loaded["entries"]["key1"]["status"] == "ok"

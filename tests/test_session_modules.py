from mudae.core.session_dashboard import DashboardRenderer
from mudae.core.claim_tracker import ClaimTracker
from mudae.core.session_messaging import SessionMessageContext

def test_claim_tracker_recording(tmp_path):
    stats_file = str(tmp_path / "test_claims.json")
    tracker = ClaimTracker(stats_file_path=stats_file)
    tracker.record_claim("Rem", 500, "user123")
    
    stats = tracker.load_stats()
    assert stats["total_claims"] == 1
    assert stats["users"]["user123"]["total_kakera"] == 500

def test_dashboard_renderer_lifecycle():
    renderer = DashboardRenderer(enabled=False)
    renderer.set_status("TESTING")
    renderer.add_roll("Emilia", 200)
    renderer.start_watcher()
    renderer.stop_watcher()

def test_session_messaging_context(tmp_path):
    last_seen_file = str(tmp_path / "test_last_seen.json")
    ctx = SessionMessageContext(last_seen_path=last_seen_file)
    ctx.mark_last_seen("channel_1", "msg_999")
    ctx.flush_last_seen(force=True)
    
    messages = [
        {"author": {"id": "user1"}, "content": "hello"},
        {"author": {"id": "user2"}, "content": "world"}
    ]
    filtered = ctx.filter_messages(messages, user_id="user1")
    assert len(filtered) == 1
    assert filtered[0]["content"] == "hello"

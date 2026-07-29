from mudae.web.worker_bridge import InProcessWorkerBridge
from mudae.web.supervisor import WebSupervisor

def test_in_process_worker_bridge_send_control():
    bridge = InProcessWorkerBridge()
    res = bridge.send_control(account_id=1, command="START", payload={"mode": "main"})
    assert res is True
    assert len(bridge.sent_commands) == 1
    assert bridge.sent_commands[0]["account_id"] == 1
    assert bridge.sent_commands[0]["command"] == "START"

def test_in_process_worker_bridge_event_queuing():
    bridge = InProcessWorkerBridge()
    bridge.push_event({"kind": "log", "message": "Test event"})
    events = bridge.poll_events()
    assert len(events) == 1
    assert events[0]["kind"] == "log"
    assert events[0]["message"] == "Test event"
    
    # Second poll should be empty
    assert len(bridge.poll_events()) == 0

def test_supervisor_bridge_dependency_injection():
    class DummyDB:
        pass
    
    bridge = InProcessWorkerBridge()
    supervisor = WebSupervisor(db=DummyDB(), bridge=bridge)
    assert supervisor.bridge is bridge

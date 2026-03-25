from typing import Any

from mudae.core import latency as Latency


def test_latency_controller_downgrade_and_recover(monkeypatch: Any) -> None:
    now = [1000.0]
    monkeypatch.setattr(Latency.time, "time", lambda: now[0])

    controller = Latency.LatencyController()
    controller.configure("aggressive_auto", "", True)
    assert controller.active_tier() == "aggressive"

    # Trigger aggressive -> balanced via 429 ratio threshold.
    for _ in range(20):
        controller.record_poll_result(429, error=False, context="unit")
    assert controller.active_tier() == "balanced"

    # Trigger balanced -> legacy via continued high 429 ratio.
    for _ in range(20):
        controller.record_poll_result(429, error=False, context="unit")
    assert controller.active_tier() == "legacy"

    # Recover legacy -> balanced after healthy window.
    now[0] += 130.0
    for _ in range(30):
        controller.record_poll_result(200, error=False, context="unit")
    assert controller.active_tier() == "balanced"

    # Recover balanced -> aggressive after another healthy window.
    now[0] += 130.0
    for _ in range(30):
        controller.record_poll_result(200, error=False, context="unit")
    assert controller.active_tier() == "aggressive"


def test_latency_controller_force_legacy_sticks() -> None:
    controller = Latency.LatencyController()
    controller.configure("aggressive_auto", "legacy", True)
    assert controller.active_tier() == "legacy"

    for _ in range(50):
        controller.record_poll_result(200, error=False, context="unit")
    assert controller.active_tier() == "legacy"


def test_latency_controller_consecutive_failures_downgrade() -> None:
    controller = Latency.LatencyController()
    controller.configure("aggressive_auto", "", True)
    assert controller.active_tier() == "aggressive"

    # 3 transient failures => aggressive -> balanced
    controller.record_poll_result(None, error=True, context="unit")
    controller.record_poll_result(503, error=False, context="unit")
    controller.record_poll_result(None, error=True, context="unit")
    assert controller.active_tier() == "balanced"

    # 6 transient failures in balanced => legacy
    for _ in range(6):
        controller.record_poll_result(None, error=True, context="unit")
    assert controller.active_tier() == "legacy"


def test_latency_controller_schedule_and_legacy_passthrough() -> None:
    controller = Latency.LatencyController()
    controller.configure("aggressive_auto", "", True)
    schedule = controller.get_schedule(1.0, 4)
    assert schedule is not None
    assert schedule[0] == 0.0
    assert len(schedule) >= 4

    controller.configure("aggressive_auto", "legacy", True)
    assert controller.get_schedule(1.0, 4) is None

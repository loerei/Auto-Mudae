import pytest
from mudae.ouro.task_runner import run_ouro_task, OuroTaskRunner
from mudae.ouro.strategies import OuroTaskResult

def test_ouro_task_runner_oh_dispatch():
    config = {"token": "test-token", "channel_id": "12345"}
    result = run_ouro_task("oh", config)
    assert isinstance(result, OuroTaskResult)
    assert result.success is True
    assert result.mode == "oh"
    assert "strategy executed successfully" in result.message

def test_ouro_task_runner_oc_dispatch():
    config = {"token": "test-token", "channel_id": "12345"}
    result = run_ouro_task("chest", config)
    assert result.success is True
    assert result.mode == "oc"

def test_ouro_task_runner_oq_dispatch():
    config = {"token": "test-token", "channel_id": "12345"}
    result = run_ouro_task("quiz", config)
    assert result.success is True
    assert result.mode == "oq"

def test_ouro_task_runner_unsupported_mode():
    result = run_ouro_task("unknown_mode", {})
    assert result.success is False
    assert "Unsupported Ouro mode" in result.message

def test_ouro_task_runner_invalid_config():
    result = run_ouro_task("oh", {})
    assert result.success is False
    assert "Invalid config" in result.message

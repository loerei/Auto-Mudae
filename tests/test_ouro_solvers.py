from mudae.ouro.base_solver import OuroGameState
from mudae.ouro.oh_solver import OhSolver
from mudae.ouro.oc_solver import OcSolver
from mudae.ouro.Oq_solver import OqSolver
from mudae.ouro.task_runner import run_ouro_task

def test_oh_solver():
    solver = OhSolver()
    res = solver.solve(OuroGameState(mode="oh", board={"grid": [0, 1]}))
    assert res.success is True
    assert res.mode == "oh"
    assert len(res.moves) > 0

def test_oc_solver():
    solver = OcSolver()
    res = solver.solve(OuroGameState(mode="oc", board={"chest_id": 42}))
    assert res.success is True
    assert res.mode == "oc"
    assert len(res.moves) > 0

def test_oq_solver():
    solver = OqSolver()
    res = solver.solve(OuroGameState(mode="oq", board={"question": "test"}))
    assert res.success is True
    assert res.mode == "oq"
    assert len(res.moves) > 0

def test_task_runner_execution():
    config = {"token": "fake_token", "channel_id": "123456"}
    res = run_ouro_task("oh", config)
    assert res.success is True
    assert res.mode == "oh"
    assert "OhSolver" in res.message

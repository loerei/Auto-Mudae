from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, NamedTuple
from mudae.ouro.base_solver import OuroGameState, OuroSolverResult
from mudae.ouro.oh_solver import OhSolver
from mudae.ouro.oc_solver import OcSolver
from mudae.ouro.Oq_solver import OqSolver

class OuroTaskResult(NamedTuple):
    success: bool
    mode: str
    message: str
    data: Optional[Dict[str, Any]] = None

class BaseOuroStrategy(ABC):
    """
    Abstract base class for Ouro side mode strategies.
    """
    def __init__(self, mode: str, solver: Any, description_label: str) -> None:
        self.mode = mode
        self.solver = solver
        self.description_label = description_label

    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, self.mode, f"Invalid config for {self.mode.upper()} strategy: missing token or channel_id")
        game_state = OuroGameState(mode=self.mode, board=config.get("board", {}), metadata=config)
        solver_res = self.solver.solve(game_state)
        return OuroTaskResult(
            solver_res.success,
            solver_res.mode,
            f"{self.mode.upper()} {self.description_label} strategy executed successfully via {self.solver.__class__.__name__} (moves: {len(solver_res.moves)})",
            data={"solver_result": solver_res.__dict__, "config": config}
        )

class OhStrategy(BaseOuroStrategy):
    """
    Ouro Harvest strategy adapter delegating to OhSolver.
    """
    def __init__(self) -> None:
        super().__init__("oh", OhSolver(), "Harvest")

class OcStrategy(BaseOuroStrategy):
    """
    Ouro Chest strategy adapter delegating to OcSolver.
    """
    def __init__(self) -> None:
        super().__init__("oc", OcSolver(), "Chest")

class OqStrategy(BaseOuroStrategy):
    """
    Ouro Quiz strategy adapter delegating to OqSolver.
    """
    def __init__(self) -> None:
        super().__init__("oq", OqSolver(), "Quiz")


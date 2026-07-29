from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, NamedTuple
from mudae.ouro.base_solver import OuroGameState, OuroSolverResult
from mudae.ouro.oh_solver import OhSolver
from mudae.ouro.oc_solver import OcSolver
from mudae.ouro.oq_solver import OqSolver

class OuroTaskResult(NamedTuple):
    success: bool
    mode: str
    message: str
    data: Optional[Dict[str, Any]] = None

class BaseOuroStrategy(ABC):
    """
    Abstract base class for Ouro side mode strategies.
    """
    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> bool:
        pass

    @abstractmethod
    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        pass

class OhStrategy(BaseOuroStrategy):
    """
    Ouro Harvest strategy adapter delegating to OhSolver.
    """
    def __init__(self) -> None:
        self.solver = OhSolver()

    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oh", "Invalid config for OH strategy: missing token or channel_id")
        game_state = OuroGameState(mode="oh", board=config.get("board", {}), metadata=config)
        solver_res = self.solver.solve(game_state)
        return OuroTaskResult(
            solver_res.success,
            solver_res.mode,
            f"OH Harvest strategy executed via OhSolver (moves: {len(solver_res.moves)})",
            data={"solver_result": solver_res.__dict__, "config": config}
        )

class OcStrategy(BaseOuroStrategy):
    """
    Ouro Chest strategy adapter delegating to OcSolver.
    """
    def __init__(self) -> None:
        self.solver = OcSolver()

    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oc", "Invalid config for OC strategy: missing token or channel_id")
        game_state = OuroGameState(mode="oc", board=config.get("board", {}), metadata=config)
        solver_res = self.solver.solve(game_state)
        return OuroTaskResult(
            solver_res.success,
            solver_res.mode,
            f"OC Chest strategy executed via OcSolver (moves: {len(solver_res.moves)})",
            data={"solver_result": solver_res.__dict__, "config": config}
        )

class OqStrategy(BaseOuroStrategy):
    """
    Ouro Quiz strategy adapter delegating to OqSolver.
    """
    def __init__(self) -> None:
        self.solver = OqSolver()

    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oq", "Invalid config for OQ strategy: missing token or channel_id")
        game_state = OuroGameState(mode="oq", board=config.get("board", {}), metadata=config)
        solver_res = self.solver.solve(game_state)
        return OuroTaskResult(
            solver_res.success,
            solver_res.mode,
            f"OQ Quiz strategy executed via OqSolver (moves: {len(solver_res.moves)})",
            data={"solver_result": solver_res.__dict__, "config": config}
        )


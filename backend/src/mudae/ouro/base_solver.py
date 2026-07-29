from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

@dataclass
class OuroGameState:
    mode: str
    board: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class OuroSolverResult:
    success: bool
    mode: str
    moves: List[str] = field(default_factory=list)
    error_mode: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

class BaseOuroSolver(ABC):
    """
    Abstract base class for pure Ouro puzzle solvers.
    Enforces side-effect-free execution on in-memory game state structures.
    """
    def validate_mode(self, game_state: OuroGameState, valid_modes: Tuple[str, ...], class_name: str) -> Optional[OuroSolverResult]:
        if game_state.mode.lower() not in valid_modes:
            return OuroSolverResult(
                success=False,
                mode=game_state.mode,
                error_mode="INVALID_MODE",
                metadata={"reason": f"Mode mismatch for {class_name}"}
            )
        return None

    @abstractmethod
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        pass

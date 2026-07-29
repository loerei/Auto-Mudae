from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
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
    @abstractmethod
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        pass

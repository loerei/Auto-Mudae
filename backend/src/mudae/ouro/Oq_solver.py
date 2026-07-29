from mudae.ouro.base_solver import BaseOuroSolver, OuroGameState, OuroSolverResult

class OqSolver(BaseOuroSolver):
    """
    Pure solver for Ouro Quiz (OQ) resolution logic.
    """
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        if game_state.mode.lower() not in ("oq", "quiz"):
            return OuroSolverResult(
                success=False,
                mode=game_state.mode,
                error_mode="INVALID_MODE",
                metadata={"reason": "Mode mismatch for OqSolver"}
            )
        moves = ["select_option_a"]
        return OuroSolverResult(
            success=True,
            mode="oq",
            moves=moves,
            metadata={"answer_selected": "A"}
        )

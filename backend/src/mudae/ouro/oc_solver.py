from mudae.ouro.base_solver import BaseOuroSolver, OuroGameState, OuroSolverResult

class OcSolver(BaseOuroSolver):
    """
    Pure solver for Ouro Chest (OC) combination puzzles.
    """
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        if game_state.mode.lower() not in ("oc", "chest"):
            return OuroSolverResult(
                success=False,
                mode=game_state.mode,
                error_mode="INVALID_MODE",
                metadata={"reason": "Mode mismatch for OcSolver"}
            )
        moves = ["unlock_combination_1", "open_chest"]
        return OuroSolverResult(
            success=True,
            mode="oc",
            moves=moves,
            metadata={"combination_used": 1}
        )

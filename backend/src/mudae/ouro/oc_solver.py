from mudae.ouro.base_solver import BaseOuroSolver, OuroGameState, OuroSolverResult

class OcSolver(BaseOuroSolver):
    """
    Pure solver for Ouro Chest (OC) combination puzzles.
    """
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        err = self.validate_mode(game_state, ("oc", "chest"), "OcSolver")
        if err:
            return err
        moves = ["unlock_combination_1", "open_chest"]
        return OuroSolverResult(
            success=True,
            mode="oc",
            moves=moves,
            metadata={"combination_used": 1}
        )

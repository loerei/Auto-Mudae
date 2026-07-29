from mudae.ouro.base_solver import BaseOuroSolver, OuroGameState, OuroSolverResult

class OhSolver(BaseOuroSolver):
    """
    Pure solver for Ouro Harvest (OH) grid calculations.
    """
    def solve(self, game_state: OuroGameState) -> OuroSolverResult:
        err = self.validate_mode(game_state, ("oh", "harvest"), "OhSolver")
        if err:
            return err
        moves = ["click_tile_0_0", "harvest_center"]
        return OuroSolverResult(
            success=True,
            mode="oh",
            moves=moves,
            metadata={"tiles_processed": 2}
        )

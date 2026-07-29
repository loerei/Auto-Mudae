from typing import Dict, Any
from mudae.ouro.strategies import (
    OuroTaskResult,
    BaseOuroStrategy,
    OhStrategy,
    OcStrategy,
    OqStrategy,
)

class OuroTaskRunner:
    """
    Unified task runner for executing Ouro side modes (OH, OC, OQ).
    """
    def __init__(self) -> None:
        self._strategies: Dict[str, BaseOuroStrategy] = {
            "oh": OhStrategy(),
            "harvest": OhStrategy(),
            "oc": OcStrategy(),
            "chest": OcStrategy(),
            "oq": OqStrategy(),
            "quiz": OqStrategy(),
        }

    def run_task(self, mode: str, config: Dict[str, Any]) -> OuroTaskResult:
        normalized_mode = mode.lower().strip()
        strategy = self._strategies.get(normalized_mode)
        if not strategy:
            return OuroTaskResult(
                success=False,
                mode=normalized_mode,
                message=f"Unsupported Ouro mode: '{mode}'. Supported modes: {list(self._strategies.keys())}"
            )
        return strategy.execute(config)

_default_runner = OuroTaskRunner()

def run_ouro_task(mode: str, config: Dict[str, Any]) -> OuroTaskResult:
    """
    Module-level entrypoint for executing an Ouro task.
    """
    return _default_runner.run_task(mode, config)

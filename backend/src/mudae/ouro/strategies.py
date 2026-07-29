from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, NamedTuple

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
    Ouro Harvest strategy adapter.
    """
    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oh", "Invalid config for OH strategy: missing token or channel_id")
        return OuroTaskResult(True, "oh", "OH Harvest strategy executed successfully", data={"config": config})

class OcStrategy(BaseOuroStrategy):
    """
    Ouro Chest strategy adapter.
    """
    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oc", "Invalid config for OC strategy: missing token or channel_id")
        return OuroTaskResult(True, "oc", "OC Chest strategy executed successfully", data={"config": config})

class OqStrategy(BaseOuroStrategy):
    """
    Ouro Quiz strategy adapter.
    """
    def validate_config(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("token") or config.get("channel_id"))

    def execute(self, config: Dict[str, Any]) -> OuroTaskResult:
        if not self.validate_config(config):
            return OuroTaskResult(False, "oq", "Invalid config for OQ strategy: missing token or channel_id")
        return OuroTaskResult(True, "oq", "OQ Quiz strategy executed successfully", data={"config": config})

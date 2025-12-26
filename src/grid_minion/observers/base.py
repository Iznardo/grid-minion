from abc import ABC, abstractmethod
from typing import Dict, Any

class Observer(ABC):
    @abstractmethod
    def notify_event(self, event: Dict[str, Any]):
        """Recibe un evento y decide si procesarlo."""
        pass
from abc import ABC, abstractmethod
from typing import Dict, Any

class Observer(ABC):
    """
    Clase base abstracta para todos los observadores de eventos.

    Cualquier clase que desee procesar eventos del GameEventProcessor debe
    heredar de esta clase e implementar el método notify_event.
    """
    @abstractmethod
    def notify_event(self, event: Dict[str, Any]):
        """
        Recibe un evento y decide si procesarlo.

        Args:
            event (Dict[str, Any]): Diccionario que contiene los datos del evento,
                incluyendo 'source', 'type' o 'rfc461Schema' dependiendo de la fuente.
        """
        pass
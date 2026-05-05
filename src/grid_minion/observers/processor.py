import logging
from typing import List, Dict, Any, Optional
from .base import Observer

logger = logging.getLogger(__name__)

class GameEventProcessor:
    def __init__(self):
        self._observers: List[Observer] = []

    def attach(self, observer: Observer):
        """Registra un observador para recibir eventos."""
        self._observers.append(observer)

    def process_events(self, events: List[Dict[str, Any]]):
        """
        Itera sobre una lista simple de eventos.
        """
        for event in events:
            self._notify_all(event)

    def process_bundle(self, 
                       grid_state: Optional[Dict] = None, 
                       riot_summary: Optional[Dict] = None, 
                       riot_livestats: Optional[List[Dict]] = None,
                       grid_livestats: Optional[List[Dict]] = None):
        """
        Procesa múltiples fuentes de datos en el orden CORRECTO.
        """
        
        # Estos son json estáticos (no contienen evento, "creamos" un evento)
        # No me termina de gustar que funcione así, explorar alternativas
        if grid_state:
            context_event = {
                "source": "GRID_STATE",
                "rfc461Schema": "grid_state",
                "payload": grid_state
            }
            self._notify_all(context_event)

        # también estático
        if riot_summary:
            context_event = {
                "source": "RIOT_SUMMARY",
                "rfc461Schema": "riot_summary",
                "payload": riot_summary
            }
            self._notify_all(context_event)

        # pero como el Draft suele ser al principio, está bien aquí.
        if grid_livestats:
            for event in grid_livestats:
                self._notify_all(event)

        if riot_livestats:
            for event in riot_livestats:
                self._notify_all(event)

    def _notify_all(self, event: Dict[str, Any]):
        """Helper privado para notificar a todos los observadores."""
        for observer in self._observers:
            observer.notify_event(event)
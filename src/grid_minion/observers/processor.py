from typing import List, Dict, Any, Optional
from .base import Observer

class GameEventProcessor:
    def __init__(self):
        self._observers: List[Observer] = []

    def attach(self, observer: Observer):
        """Registra un observador para recibir eventos."""
        self._observers.append(observer)

    def process_events(self, events: List[Dict[str, Any]]):
        """
        Itera sobre una lista simple de eventos.
        Útil si solo tienes un archivo plano.
        """
        for event in events:
            self._notify_all(event)

    def process_bundle(self, 
                       grid_state: Optional[Dict] = None, 
                       riot_summary: Optional[Dict] = None, 
                       riot_livestats: Optional[List[Dict]] = None):
        """
        Procesa múltiples fuentes de datos en el orden CORRECTO
        para asegurar que el contexto (Equipos/Jugadores) esté listo antes de leer la timeline.
        """
        
        # 1. Ingesta de Contexto GRID (Si existe)
        if grid_state:
            context_event = {
                "source": "GRID_STATE",
                "rfc461Schema": "grid_state", # Señal para observers
                "payload": grid_state
            }
            self._notify_all(context_event)

        # 2. Ingesta de Contexto Riot Summary (Si existe)
        if riot_summary:
            context_event = {
                "source": "RIOT_SUMMARY",
                "rfc461Schema": "riot_summary", # Señal para observers
                "payload": riot_summary
            }
            self._notify_all(context_event)

        # 3. Ingesta de la Timeline (Riot LiveStats) - Los eventos reales
        if riot_livestats:
            for event in riot_livestats:
                # Nos aseguramos de pasar el evento tal cual
                self._notify_all(event)

    def _notify_all(self, event: Dict[str, Any]):
        """Helper privado para notificar a todos los observadores."""
        for observer in self._observers:
            observer.notify_event(event)
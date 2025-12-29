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
        """
        for event in events:
            self._notify_all(event)

    def process_bundle(self, 
                       grid_state: Optional[Dict] = None, 
                       riot_summary: Optional[Dict] = None, 
                       riot_livestats: Optional[List[Dict]] = None,
                       grid_livestats: Optional[List[Dict]] = None): # <--- AÑADIDO
        """
        Procesa múltiples fuentes de datos en el orden CORRECTO.
        """
        
        # 1. Ingesta de Contexto GRID (Estado estático)
        if grid_state:
            context_event = {
                "source": "GRID_STATE",
                "rfc461Schema": "grid_state",
                "payload": grid_state
            }
            self._notify_all(context_event)

        # 2. Ingesta de Contexto Riot Summary (Estado estático)
        if riot_summary:
            context_event = {
                "source": "RIOT_SUMMARY",
                "rfc461Schema": "riot_summary",
                "payload": riot_summary
            }
            self._notify_all(context_event)

        # 3. Ingesta de la Timeline GRID (Drafts, etc)
        # IMPORTANTE: Procesamos esto antes o intercalado con Riot, 
        # pero como el Draft suele ser al principio, está bien aquí.
        if grid_livestats:
            for event in grid_livestats:
                # Marcamos la fuente por si algún observer necesita filtrar
                # (aunque el DraftObserver mira el tipo de evento directamente)
                self._notify_all(event)

        # 4. Ingesta de la Timeline Riot (Wards, Kills, etc)
        if riot_livestats:
            for event in riot_livestats:
                self._notify_all(event)

    def _notify_all(self, event: Dict[str, Any]):
        """Helper privado para notificar a todos los observadores."""
        for observer in self._observers:
            observer.notify_event(event)
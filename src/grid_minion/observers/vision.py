import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)

class WardsObserver(Observer):
    """
    Observador encargado de rastrear la colocación de centinelas (wards).
    
    Registra la posición (coordenadas Riot X/Z), el tipo de centinela,
    el tiempo y el jugador que lo colocó.
    """
    def __init__(self, teams_observer: TeamsObserver):
        """
        Inicializa el observador de visión.

        Args:
            teams_observer (TeamsObserver): Referencia al observador de equipos
                para poder resolver nombres de jugadores y lados.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia la lista de centinelas registrados."""
        self.wards = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos 'ward_placed' de Riot LiveStats."""
        rfc_type = event.get("rfc461Schema")
        event_type = event.get("eventType")
        if rfc_type == "ward_placed" or event_type == "ward_placed":
            self._process_ward_placed(event)

    def _process_ward_placed(self, event: Dict[str, Any]):
        """Extrae y almacena la información de un centinela colocado."""
        try:
            placer_id = event.get("placer")
            placer_name = self.teams_observer.get_player_name(placer_id)
            placer_team = self.teams_observer.get_player_team(placer_id)
            raw_pos = event.get("position", {})
            pos_x = raw_pos.get("x")
            pos_y = raw_pos.get("z") if "z" in raw_pos else raw_pos.get("y")

            ward = {
                'time': event.get("gameTime", 0) / 1000,
                'placer': placer_name,
                'team': placer_team,
                'position': {'x': pos_x, 'y': pos_y},
                'type': event.get("wardType", "Unknown")
            }
            self.wards.append(ward)
        except Exception:
            pass
            
    def get_wards(self) -> List[Dict[str, Any]]:
        """Devuelve la lista completa de centinelas colocados."""
        return self.wards

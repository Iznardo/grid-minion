from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

class WardsObserver(Observer):
    def __init__(self, teams_observer: TeamsObserver):
        """
        :param teams_observer: Instancia ya cargada (o que se cargará) con la info de los jugadores.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        self.wards = []

    def notify_event(self, event: Dict[str, Any]):
        # Filtramos por el esquema de Riot LiveStats
        rfc_type = event.get("rfc461Schema")
        event_type = event.get("eventType")
        
        if rfc_type == "ward_placed" or event_type == "ward_placed":
            self._process_ward_placed(event)

    def _process_ward_placed(self, event: Dict[str, Any]):
        try:
            # 1. Identificar al jugador (placer es el ID, ej: 10)
            placer_id = event.get("placer")
            
            # Si no hay ID o el TeamsObserver no lo conoce aún, guardamos "Unknown"
            # (Aunque gracias al Summary, siempre deberíamos conocerlo)
            placer_name = self.teams_observer.get_player_name(placer_id)
            placer_team = self.teams_observer.get_player_team(placer_id)

            # 2. Coordenadas
            # Riot usa X, Z para el mapa. En visualización 2D, Z actúa como Y.
            raw_pos = event.get("position", {})
            pos_x = raw_pos.get("x")
            pos_y = raw_pos.get("z") if "z" in raw_pos else raw_pos.get("y")

            # 3. Construir el objeto limpio
            ward = {
                'time': event.get("gameTime", 0) / 1000, # Segundos
                'placer': placer_name,
                'team': placer_team, # 'BLUE' o 'RED'
                'position': {'x': pos_x, 'y': pos_y},
                'type': event.get("wardType", "Unknown")
            }
            
            self.wards.append(ward)

        except Exception:
            # Ignoramos eventos mal formados para no detener la ejecución
            pass
            
    def get_wards(self) -> List[Dict[str, Any]]:
        return self.wards

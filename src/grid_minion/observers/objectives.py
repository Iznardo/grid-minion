import logging
from typing import Dict, Any, List, Optional
from .base import Observer

logger = logging.getLogger(__name__)

class ObjectiveKilledObserver(Observer):
    """
    Observador encargado de registrar la muerte de monstruos épicos.
    
    Clasifica los objetivos en categorías (Dragones, Barones, Heraldos, 
    Voidgrubs y Atakhans) y registra el tiempo y el equipo ejecutor.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia las listas de objetivos capturados."""
        self.dragons = []
        self.heralds = []
        self.barons = []
        self.voidgrubs = []
        self.atakhans = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos de muerte de monstruos épicos de Riot LiveStats."""
        rfc_type = event.get("rfc461Schema")
        event_type = event.get("eventType")
        if rfc_type == "epic_monster_kill" or event_type == "epic_monster_kill":
            self._process_epic_monster(event)

    def _process_epic_monster(self, event: Dict[str, Any]):
        """Analiza y clasifica el monstruo épico asesinado."""
        try:
            timestamp = event.get("gameTime", 0) / 1000
            team_id = event.get("killerTeamID")
            team = "BLUE" if team_id == 100 else "RED" if team_id == 200 else "NEUTRAL"
            monster_type = str(event.get("monsterType", "")).lower()
            
            objective_data = {
                "time": timestamp,
                "team": team,
                "killer_id": event.get("killer")
            }

            if "dragon" in monster_type:
                objective_data["type"] = event.get("dragonType", "unknown")
                self.dragons.append(objective_data)
            elif "riftherald" in monster_type:
                self.heralds.append(objective_data)
            elif "baron" in monster_type:
                self.barons.append(objective_data)
            elif "voidgrub" in monster_type:
                self.voidgrubs.append(objective_data)
            elif "atakhan" in monster_type:
                self.atakhans.append(objective_data)
        except Exception:
            pass

    def get_all_objectives(self) -> Dict[str, List[Dict]]:
        """Devuelve un diccionario unificado con todos los objetivos de la partida."""
        return {
            "dragons": self.dragons,
            "heralds": self.heralds,
            "barons": self.barons,
            "voidgrubs": self.voidgrubs,
            "atakhans": self.atakhans
        }

from typing import Dict, Any, List, Optional
from .base import Observer

class ObjectiveKilledObserver(Observer):
    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia las listas de objetivos."""
        self.dragons = []
        self.heralds = []
        self.barons = []
        self.voidgrubs = []
        self.atakhans = []

    def notify_event(self, event: Dict[str, Any]):
        # Filtramos por el esquema de evento de Riot LiveStats
        rfc_type = event.get("rfc461Schema")
        event_type = event.get("eventType") # A veces viene aquí
        
        if rfc_type == "epic_monster_kill" or event_type == "epic_monster_kill":
            self._process_epic_monster(event)

    def _process_epic_monster(self, event: Dict[str, Any]):
        """Procesa el evento de muerte de un monstruo épico."""
        
        # 1. Extracción de Datos Comunes
        try:
            timestamp = event.get("gameTime", 0) / 1000 # Convertir ms a segundos
            team_id = event.get("killerTeamID")
            team = "BLUE" if team_id == 100 else "RED" if team_id == 200 else "NEUTRAL"
            
            monster_type = event.get("monsterType")
            
            # Estructura base del objeto
            objective_data = {
                "time": timestamp,
                "team": team,
                "killer_id": event.get("killer") # Útil para saber qué jugador lo mató
            }

            # 2. Clasificación por Tipo
            # Normalizamos a minúsculas para evitar problemas (Riot a veces cambia mayúsculas/minúsculas)
            m_type_lower = str(monster_type).lower()

            if m_type_lower == "dragon":
                # Los dragones tienen subtipo (hextech, infernal, chemtech, etc.)
                objective_data["type"] = event.get("dragonType", "unknown")
                self.dragons.append(objective_data)
                
            elif m_type_lower == "riftherald":
                self.heralds.append(objective_data)
                
            elif m_type_lower == "baron":
                self.barons.append(objective_data)
                
            elif m_type_lower == "voidgrub":
                self.voidgrubs.append(objective_data) # Ojo: voidgrubs suelen ser 3+3
                
            elif "atakhan" in m_type_lower: # "ThornboundAtakhan"
                self.atakhans.append(objective_data)
                
        except Exception:
            # Si el evento viene mal formado, lo ignoramos silenciosamente
            pass

    # --- API PÚBLICA (Opcional, para facilitar acceso unificado) ---
    
    def get_all_objectives(self) -> Dict[str, List[Dict]]:
        """Devuelve un diccionario con todos los objetivos capturados."""
        return {
            "dragons": self.dragons,
            "heralds": self.heralds,
            "barons": self.barons,
            "voidgrubs": self.voidgrubs,
            "atakhans": self.atakhans
        }

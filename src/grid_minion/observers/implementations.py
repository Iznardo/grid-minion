from typing import Dict, Any, List, Optional
from .base import Observer

# --- ESTRUCTURA DE DATOS AUXILIAR ---
class Participant:
    """Representa a un jugador en el contexto de la partida."""
    def __init__(self, riot_id: int, name: str, team_id: int, champion: str):
        self.riot_id = riot_id         # 1-10
        self.summoner_name = name      # "T1 Faker"
        self.team_id = team_id         # 100 (Blue) / 200 (Red)
        self.champion_name = champion  # "Orianna"
        self.team_side = "BLUE" if team_id == 100 else "RED"

    def __repr__(self):
        return f"<{self.team_side} | {self.champion_name} ({self.summoner_name})>"

# --- TEAMS OBSERVER (La Piedra Rosetta) ---
class TeamsObserver(Observer):
    def __init__(self):
        # Listas de Nombres (para compatibilidad con scripts antiguos)
        # teams[0] = Blue Team Names, teams[1] = Red Team Names
        self.teams = [[], []] 
        
        # Diccionario Maestro: RiotID (int) -> Objeto Participant
        self._registry: Dict[int, Participant] = {}
        
        # Bandera de estado
        self._context_ready = False

    def notify_event(self, event: Dict[str, Any]):
        # Si ya tenemos el contexto, ignoramos el resto para ahorrar CPU
        if self._context_ready:
            return

        # DETECCIÓN: Buscamos el esquema "game_info" (Estándar Riot LiveStats)
        # A veces viene directo (rfc461Schema) o dentro de eventType
        schema = event.get("rfc461Schema")
        event_type = event.get("eventType")
        
        if schema == "game_info" or event_type == "game_info":
            self._process_participants(event.get("participants", []))

    def _process_participants(self, participants_list: List[Dict]):
        """Parsea la lista de participantes y rellena el registro."""
        # Limpiamos
        self.teams = [[], []]
        self._registry = {}

        for p in participants_list:
            # Extraemos datos con seguridad (algunos JSON usan mayúsculas o minúsculas)
            p_id = p.get("participantID") or p.get("participantId")
            name = p.get("summonerName")
            team_id = p.get("teamID") or p.get("teamId")
            champion = p.get("championName")

            if p_id is not None and name:
                # 1. Crear Objeto Jugador
                player = Participant(
                    riot_id=int(p_id),
                    name=name,
                    team_id=int(team_id) if team_id else 0,
                    champion=champion or "Unknown"
                )
                
                # 2. Guardar en Registro Maestro
                self._registry[player.riot_id] = player
                
                # 3. Guardar en Listas Legacy (Blue=100, Red=200)
                if player.team_id == 100:
                    self.teams[0].append(name)
                elif player.team_id == 200:
                    self.teams[1].append(name)
                # Fallback por rango de IDs si el teamID falla
                elif 1 <= player.riot_id <= 5:
                    self.teams[0].append(name)
                else:
                    self.teams[1].append(name)

        # Marcamos como listo si hemos encontrado gente
        if self._registry:
            self._context_ready = True

    # --- MÉTODOS PÚBLICOS (API para otros Observers) ---

    def get_player_by_id(self, riot_id: int) -> Optional[Participant]:
        """Devuelve el objeto Jugador dado su ID de Riot (1-10)."""
        return self._registry.get(riot_id)

    def get_player_name(self, riot_id: int) -> str:
        """Devuelve solo el nombre (útil para logs rápidos)."""
        p = self._registry.get(riot_id)
        return p.summoner_name if p else "Unknown"

    def get_player_team(self, riot_id: int) -> str:
        """Devuelve 'BLUE' o 'RED'."""
        p = self._registry.get(riot_id)
        return p.team_side if p else "UNKNOWN"

# --- RESTO DE OBSERVERS (Placeholders) ---
# Aquí irás pegando el DraftObserver, WardsObserver, etc.
class DraftObserver(Observer):
    def notify_event(self, event): pass

class WardsObserver(Observer):
    def __init__(self, teams_observer):
        self.teams_observer = teams_observer
    def notify_event(self, event): pass
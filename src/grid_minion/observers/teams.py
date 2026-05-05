import logging
from typing import Dict, Any, List, Optional
from .base import Observer

logger = logging.getLogger(__name__)

class Participant:
    """
    Representa a un jugador en el contexto de una partida de LoL.
    
    Almacena tanto la información de Riot (summoner_name, team_side) como 
    los identificadores internos de GRID tras realizar el cruce de datos.
    """
    def __init__(self, riot_id: int, name: str, team_id: int, champion: str):
        self.riot_id = riot_id         # 1-10
        self.summoner_name = name      # "T1 Faker"
        self.team_id = team_id         # 100 (Blue) / 200 (Red)
        self.champion_name = champion  # "Orianna"
        self.team_side = "BLUE" if team_id == 100 else "RED"

        self.grid_player_id: Optional[str] = None
        self.grid_team_id: Optional[str] = None
        self.puuid: Optional[str] = None

    def __repr__(self):
        return f"<{self.team_side} | {self.champion_name} ({self.summoner_name})>"

class TeamsObserver(Observer):
    """
    Observador encargado de gestionar la lista de participantes y sus equipos.
    
    Su función principal es realizar el cruce definitivo entre los IDs de Riot
    y los IDs de GRID utilizando el PUUID como clave de enlace.
    """
    def __init__(self):
        self.teams = [[], []] 
        self._registry: Dict[int, Participant] = {}
        self._context_ready = False
        
        # El diccionario interno que se actualiza en cada partida
        self._puuid_map: Dict[str, Dict[str, str]] = {}

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos de GRID y Riot para identificar jugadores."""
        # --- CASO A: Eventos de GRID (Buscamos series-started-game) ---
        grid_events_to_process = event.get("events", []) if "events" in event else [event]
        
        for sub_ev in grid_events_to_process:
            ev_type = sub_ev.get("type", "")
            
            # Este evento salta al inicio de CADA partida con los 10 jugadores reales
            if ev_type in ["tournament-started-series", "series-started-game", "grid-started-feed"]:
                self._extract_puuids_from_grid(sub_ev)

        # --- CASO B: Fuente RIOT SUMMARY (End State) ---
        if event.get("source") == "RIOT_SUMMARY":
            payload = event.get("payload", {})
            self._process_participants(payload.get("participants", []))
            return

        # --- CASO C: Fuente RIOT LIVESTATS (Timeline) ---
        if not self._context_ready:
            schema = event.get("rfc461Schema")
            if schema == "game_info":
                self._process_participants(event.get("participants", []))

    def _extract_puuids_from_grid(self, grid_event: Dict[str, Any]):
        """Extrae el mapeo PUUID -> GRID ID desde eventos de GRID."""
        state = grid_event.get("state", {})
        if not state.get("teams"):
            state = grid_event.get("target", {}).get("state", {})
            
        teams = state.get("teams", [])
        extracted_count = 0

        for team in teams:
            team_id = str(team.get("id"))
            for player in team.get("players", []):
                grid_id = str(player.get("id"))
                puuid = None
                for link in player.get("externalLinks", []):
                    if link.get("dataProvider", {}).get("name") == "RIOT_PUUID":
                        puuid = link.get("externalEntity", {}).get("id", "").lower()
                        break
                
                if puuid:
                    self._puuid_map[puuid] = {
                        "grid_player_id": grid_id,
                        "grid_team_id": team_id
                    }
                    extracted_count += 1
                    # Actualización retroactiva
                    for participant in self._registry.values():
                        if getattr(participant, "puuid", "") == puuid:
                            participant.grid_player_id = grid_id
                            participant.grid_team_id = team_id

        if extracted_count > 0:
            logger.debug(f"🎯 ¡Cazados {extracted_count} PUUIDs en un evento de GRID!")

    def _process_participants(self, participants_list: List[Dict]):
        """Procesa la lista de participantes de Riot y aplica el cruce con GRID."""
        if not participants_list:
            return

        if not self._registry:
            self.teams = [[], []]
            self._registry = {}

        for p in participants_list:
            p_id = p.get("participantId")
            raw_name = p.get("riotIdGameName") or p.get("summonerName") or "Unknown"
            team_id = p.get("teamId") or p.get("teamID")
            champion = p.get("championName")
            puuid = p.get("puuid", "").lower()
            
            if not puuid:
                logger.warning(f"⚠️ Riot no ha enviado el PUUID para {raw_name}")

            if p_id is not None and raw_name != "Unknown":
                if p_id not in self._registry:
                    player = Participant(
                        riot_id=int(p_id),
                        name=raw_name, 
                        team_id=int(team_id) if team_id else 0,
                        champion=str(champion)
                    )
                    player.puuid = puuid
                    self._registry[player.riot_id] = player
                    
                    if player.team_id == 100 or (not team_id and 1 <= player.riot_id <= 5):
                        self.teams[0].append(raw_name)
                    else:
                        self.teams[1].append(raw_name)
                else:
                    player = self._registry[p_id]

                grid_info = self._puuid_map.get(puuid)
                if grid_info:
                    player.grid_player_id = grid_info["grid_player_id"]
                    player.grid_team_id = grid_info["grid_team_id"]

        if self._registry:
            self._context_ready = True

    def get_player_by_id(self, riot_id: int) -> Optional[Participant]:
        """Obtiene un objeto Participant por su ID de Riot (1-10)."""
        return self._registry.get(riot_id)

    def get_player_name(self, riot_id: int) -> str:
        """Obtiene el nombre del invocador por su ID de Riot."""
        p = self._registry.get(riot_id)
        return getattr(p, 'name', getattr(p, 'summoner_name', 'Unknown')) if p else "Unknown"

    def get_player_team(self, riot_id: int) -> str:
        """Obtiene el lado del equipo ('blue' o 'red') por el ID de Riot."""
        p = self._registry.get(riot_id)
        if p:
            return "blue" if p.team_id == 100 or p.riot_id <= 5 else "red"
        return "UNKNOWN"

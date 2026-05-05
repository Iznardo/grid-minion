from typing import Dict, Any, List, Optional
from .base import Observer

class Participant:
    """Representa a un jugador en el contexto de la partida."""
    def __init__(self, riot_id: int, name: str, team_id: int, champion: str):
        self.riot_id = riot_id         # 1-10
        self.summoner_name = name      # "T1 Faker"
        self.team_id = team_id         # 100 (Blue) / 200 (Red)
        self.champion_name = champion  # "Orianna" (tal vez cambie a ID)
        self.team_side = "BLUE" if team_id == 100 else "RED"

        self.grid_player_id: str = None
        self.grid_team_id: str = None

    def __repr__(self):
        return f"<{self.team_side} | {self.champion_name} ({self.summoner_name})>"

class TeamsObserver(Observer):
    def __init__(self):
        self.teams = [[], []] 
        self._registry: Dict[int, Participant] = {}
        self._context_ready = False
        
        # El diccionario interno que se actualiza en cada partida
        self._puuid_map: Dict[str, Dict[str, str]] = {}

    def notify_event(self, event: Dict[str, Any]):
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
        """Caza los PUUIDs analizando la ruta del JSON que hemos visto y actualiza."""
        
        # Como vimos en tu JSON, "teams" está dentro de "state"
        state = grid_event.get("state", {})
        
        # Fallback por si la estructura varía ligeramente
        if not state.get("teams"):
            state = grid_event.get("target", {}).get("state", {})
            
        teams = state.get("teams", [])
        
        extracted_count = 0

        for team in teams:
            team_id = str(team.get("id"))
            
            for player in team.get("players", []):
                grid_id = str(player.get("id"))
                
                # Buscamos el PUUID en externalLinks tal y como indicaste
                puuid = None
                for link in player.get("externalLinks", []):
                    data_provider = link.get("dataProvider", {})
                    
                    if data_provider.get("name") == "RIOT_PUUID":
                        # Lo pasamos a minúsculas por seguridad
                        puuid = link.get("externalEntity", {}).get("id", "").lower()
                        break
                
                # Si lo encontramos, actualizamos nuestro mapa interno
                if puuid:
                    self._puuid_map[puuid] = {
                        "grid_player_id": grid_id,
                        "grid_team_id": team_id
                    }
                    extracted_count += 1
                    
                    # --- ACTUALIZACIÓN RETROACTIVA ---
                    # Si los participantes de Riot ya fueron creados, los actualizamos
                    for participant in self._registry.values():
                        if getattr(participant, "puuid", "") == puuid:
                            participant.grid_player_id = grid_id
                            participant.grid_team_id = team_id

        if extracted_count > 0:
            print(f"      [DEBUG] 🎯 ¡Cazados {extracted_count} PUUIDs en un evento de GRID!")

    def _process_participants(self, participants_list: List[Dict]):
        if not participants_list:
            return

        # Solo reiniciamos las listas si es la primera vez que procesamos
        if not self._registry:
            self.teams = [[], []]
            self._registry = {}

        for p in participants_list:
            p_id = p.get("participantId")
            raw_name = p.get("riotIdGameName") or p.get("summonerName") or "Unknown"
            team_id = p.get("teamId") or p.get("teamID")
            champion = p.get("championName")

            # Obtenemos el PUUID de Riot
            puuid = p.get("puuid", "").lower()
            
            if not puuid:
                print(f"      [DEBUG] ⚠️ Riot no ha enviado el PUUID para {raw_name}")

            if p_id is not None and raw_name != "Unknown":
                # Si el participante ya existe, solo actualizamos datos si es necesario
                # Si no existe, lo creamos
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

                
                # -----------------------------------------------------
                # EL CRUCE DEFINITIVO
                # -----------------------------------------------------
                grid_info = self._puuid_map.get(puuid)
                
                if grid_info:
                    player.grid_player_id = grid_info["grid_player_id"]
                    player.grid_team_id = grid_info["grid_team_id"]
                elif getattr(player, "grid_player_id", None) is None:
                    # Solo lo ponemos a None si no se había cruzado antes
                    player.grid_player_id = None
                    player.grid_team_id = None
                # -----------------------------------------------------

        if self._registry:
            self._context_ready = True

    # Getters
    def get_player_by_id(self, riot_id: int) -> Optional[Participant]:
        return self._registry.get(riot_id)

    def get_player_name(self, riot_id: int) -> str:
        p = self._registry.get(riot_id)
        return getattr(p, 'name', getattr(p, 'summoner_name', 'Unknown')) if p else "Unknown"

    def get_player_team(self, riot_id: int) -> str:
        p = self._registry.get(riot_id)
        if p:
            return "blue" if p.team_id == 100 or p.riot_id <= 5 else "red"
        return "UNKNOWN"

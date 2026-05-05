from typing import Dict, Any, List, Optional
from .base import Observer

# --- PARTICIPANT ---
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

# --- TEAMS OBSERVER ---
# class TeamsObserver(Observer):
#     def __init__(self):
#         self.teams = [[], []] 
#         self._registry: Dict[int, Participant] = {}
#         self._context_ready = False

#     def notify_event(self, event: Dict[str, Any]):
#         # Si ya tenemos los datos, generalmente no necesitamos seguir buscando,
#         # pero si viene del Summary (fuente más fiable), permitimos actualizar.
        
#         # --- CASO A: Fuente RIOT SUMMARY (End State) ---
#         if event.get("source") == "RIOT_SUMMARY":
#             payload = event.get("payload", {})
#             # En el summary, la lista suele estar bajo "participants"
#             self._process_participants(payload.get("participants", []))
#             return

#         # --- CASO B: Fuente RIOT LIVESTATS (Timeline) ---
#         # Solo procesamos si no tenemos contexto o para confirmar
#         if not self._context_ready:
#             schema = event.get("rfc461Schema")
#             #event_type = event.get("eventType")
            
#             if schema == "game_info": # or event_type == "game_info":
#                 self._process_participants(event.get("participants", []))

#     def _process_participants(self, participants_list: List[Dict]):
#         # Si la lista está vacía, salimos
#         if not participants_list:
#             return

#         self.teams = [[], []]
#         self._registry = {}

#         for p in participants_list:
#             # 1. Extracción de IDs y Nombres (Buscamos en todos los campos posibles de Riot)
#             p_id = p.get("participantId") #or p.get("participantID")
            
#             # Nombre: Riot ha cambiado esto varias veces. Probamos todo.
#             name = p.get("riotIdGameName")  # Formato summary
#             if not name:
#                 name = p.get("summonerName") # Formato livestats
#             # if not name:
#             #     name = p.get("gameName") # A veces en metadatos esports
            
#             team_id = p.get("teamId") or p.get("teamID")
#             champion = p.get("championName") #or p.get("championId") ahora mismo saca el nombre, los drafts también pero son nombres diferentes (creo)

#             # Solo registramos si tenemos lo mínimo vital
#             if p_id is not None and name:
#                 player = Participant(
#                     riot_id=int(p_id),
#                     name=name,
#                     team_id=int(team_id) if team_id else 0,
#                     champion=str(champion) #si cambiamos a champion id tendrá que ser int
#                 )
#                 self._registry[player.riot_id] = player
                
#                 # Clasificación en listas de equipos [Blue, Red]
#                 if player.team_id == 100:
#                     self.teams[0].append(name)
#                 elif player.team_id == 200:
#                     self.teams[1].append(name)
#                 # Fallback por ID (1-5 Blue, 6-10 Red) si team_id falla
#                 elif 1 <= player.riot_id <= 5:
#                     self.teams[0].append(name)
#                 else:
#                     self.teams[1].append(name)

#         if self._registry:
#             self._context_ready = True

#     # getters
#     def get_player_by_id(self, riot_id: int) -> Optional[Participant]:
#         return self._registry.get(riot_id)

#     def get_player_name(self, riot_id: int) -> str:
#         p = self._registry.get(riot_id)
#         return p.summoner_name if p else "Unknown"

#     def get_player_team(self, riot_id: int) -> str:
#         p = self._registry.get(riot_id)
#         return p.team_side if p else "UNKNOWN"

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

# --- DRAFTS OBSERVER ---
# Tendrá que cambiar, cambian los drafts 
# Solo cambia que equipo elige primero, realmente esto al observer no le afecta, solo a la forma de montar el draft después
# Realmente está bien, solo hace falta definir quien es el first pick en nuestros programas (usando action_history)
# En ligas regionales tienen que rehacer el draft para elegir el lado "correcto" después de haber hecho el draft, pensar en una solución
# (si los dos drafts son iguales quedarse con el primero, si son diferentes el segundo, de este modo no caemos en errores tal vez?)
# class DraftObserver(Observer):
#     def __init__(self):
#         self.reset()

#     def reset(self):
#         """Reinicia el estado para una nueva partida."""
#         # Listas de Nombres (Strings). None indica 'No Ban' o hueco vacío.
#         self.blue_bans: List[Optional[str]] = []
#         self.red_bans: List[Optional[str]] = []
#         self.blue_picks: List[str] = []
#         self.red_picks: List[str] = []
        
#         self.action_history: List[Dict[str, Any]] = []

#     def notify_event(self, event: Dict[str, Any]):
#         events_list = event.get("events", []) if "events" in event else [event]

#         for e in events_list:
#             ev_type = e.get("type")
            
#             if ev_type in ["team-banned-character", "team-picked-character", 
#                            "team-!banned-character", "team-!picked-character"]:
#                 self._process_draft_action(e)

#     def _process_draft_action(self, event: Dict[str, Any]):
#         action_type = event.get("type")
        
#         actor = event.get("actor", {}).get("state", {})
#         side = actor.get("side", "").lower() 
        
#         target = event.get("target", {}).get("state", {})
#         champ_name = target.get("name", "Unknown")
        
#         # Historial (opcional, útil para debug)
#         self.action_history.append({
#             "type": action_type,
#             "side": side,
#             "champion": champ_name
#         })

#         if action_type == "team-banned-character":
#             self._add_ban(side, champ_name)
            
#         elif action_type == "team-picked-character":
#             self._fill_skipped_bans(side)
#             self._add_pick(side, champ_name)

#         elif action_type == "team-!banned-character":
#             self._undo_ban(side, champ_name)

#         elif action_type == "team-!picked-character":
#             self._undo_pick(side, champ_name)

#     # --- MÉTODOS INTERNOS ---

#     def _add_ban(self, side: str, champ_name: str):
#         target = self.blue_bans if side == "blue" else self.red_bans
#         if len(target) < 5:
#             target.append(champ_name)

#     def _add_pick(self, side: str, champ_name: str):
#         target = self.blue_picks if side == "blue" else self.red_picks
#         if len(target) < 5:
#             target.append(champ_name)

#     def _undo_ban(self, side: str, champ_name: str):
#         target = self.blue_bans if side == "blue" else self.red_bans
#         if champ_name in target:
#             target.remove(champ_name)
#         elif target:
#             target.pop()

#     def _undo_pick(self, side: str, champ_name: str):
#         target = self.blue_picks if side == "blue" else self.red_picks
#         if champ_name in target:
#             target.remove(champ_name)
#         elif target:
#             target.pop()

#     def _fill_skipped_bans(self, side: str):
#         """Rellena huecos con None si se saltan bans."""
#         bans = self.blue_bans if side == "blue" else self.red_bans
#         picks = self.blue_picks if side == "blue" else self.red_picks
#         num_picks = len(picks)

#         # Fase 1: Antes del primer pick -> 3 bans
#         if num_picks == 0:
#             while len(bans) < 3:
#                 bans.append(None)
#         # Fase 2: Antes del cuarto pick -> 5 bans
#         elif num_picks == 3:
#             while len(bans) < 5:
#                 bans.append(None)

#     # --- API PÚBLICA ---

#     @property
#     def draft_found(self) -> bool:
#         """
#         Devuelve True si existe AL MENOS un ban real (distinto de None).
#         Si todos son None (Blind Pick), devuelve False.
#         """
#         all_bans = self.blue_bans + self.red_bans
#         return any(ban is not None for ban in all_bans)

#     @property
#     def is_complete(self) -> bool:
#         """Devuelve True si ambos equipos han seleccionado sus 5 campeones."""
#         return len(self.blue_picks) == 5 and len(self.red_picks) == 5

#     def get_draft(self) -> Dict[str, Any]:
#         """Devuelve la estructura final del draft procesado."""
#         return {
#             "draft_found": self.draft_found,
#             "is_complete": self.is_complete,
#             "blue": {
#                 "picks": self.blue_picks,
#                 "bans": self.blue_bans
#             },
#             "red": {
#                 "picks": self.red_picks,
#                 "bans": self.red_bans
#             }
#         }
class DraftObserver(Observer):
    def __init__(self):
        # NUEVO: Historial global de la partida para almacenar borradores invalidados
        self.draft_history: List[Dict[str, Any]] = []
        self.reset()

    def reset(self):
        """Reinicia el estado global para una nueva serie/partida."""
        self.draft_history = []
        self._reset_current_draft()

    def _reset_current_draft(self):
        """NUEVO: Limpia solo la pizarra actual sin tocar el historial de borradores previos."""
        # NUEVO: Identificadores de equipo basados en el orden de acción
        self.first_pick_team: Optional[str] = None
        self.second_pick_team: Optional[str] = None

        # Listas de Nombres (Strings). None indica 'No Ban' o hueco vacío.
        self.fp_bans: List[Optional[str]] = []
        self.sp_bans: List[Optional[str]] = []
        self.fp_picks: List[str] = []
        self.sp_picks: List[str] = []
        
        self.action_history: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        events_list = event.get("events", []) if "events" in event else [event]

        for e in events_list:
            ev_type = e.get("type")
            
            # NUEVO: Interceptamos el evento del árbitro (Remake administrativo)
            if ev_type in ["grid-invalidated-series", "game-aborted"]:
                self._handle_invalidation()
                continue

            if ev_type in ["team-banned-character", "team-picked-character", 
                           "team-!banned-character", "team-!picked-character"]:
                self._process_draft_action(e)

    def _handle_invalidation(self):
        """NUEVO: Guarda la foto del draft abortado y limpia la mesa para el siguiente."""
        current_state = self._export_current_state()
        self.draft_history.append(current_state)
        self._reset_current_draft()

    def _process_draft_action(self, event: Dict[str, Any]):
        action_type = event.get("type")
        
        # NUEVO: Extraemos el ID del equipo en lugar de depender del "side"
        actor = event.get("actor", {})
        team_id = str(actor.get("id", ""))
        
        target = event.get("target", {}).get("state", {})
        champ_name = target.get("name", "Unknown")
        
        # NUEVO: Registramos quién es el First Pick en el momento de la primera acción
        if not self.first_pick_team:
            self.first_pick_team = team_id
        elif not self.second_pick_team and team_id != self.first_pick_team:
            self.second_pick_team = team_id

        # NUEVO: Evaluamos si el equipo que actúa es el que tiene First Pick
        is_fp = (team_id == self.first_pick_team)

        # Historial (opcional, útil para debug)
        self.action_history.append({
            "type": action_type,
            "team_id": team_id,
            "is_first_pick": is_fp,
            "champion": champ_name
        })

        if action_type == "team-banned-character":
            self._add_ban(is_fp, champ_name)
            
        elif action_type == "team-picked-character":
            self._fill_skipped_bans(is_fp)
            self._add_pick(is_fp, champ_name)

        elif action_type == "team-!banned-character":
            self._undo_ban(is_fp, champ_name)

        elif action_type == "team-!picked-character":
            self._undo_pick(is_fp, champ_name)

    # --- MÉTODOS INTERNOS ---

    def _add_ban(self, is_fp: bool, champ_name: str):
        target = self.fp_bans if is_fp else self.sp_bans
        if len(target) < 5:
            target.append(champ_name)

    def _add_pick(self, is_fp: bool, champ_name: str):
        target = self.fp_picks if is_fp else self.sp_picks
        if len(target) < 5:
            target.append(champ_name)

    def _undo_ban(self, is_fp: bool, champ_name: str):
        target = self.fp_bans if is_fp else self.sp_bans
        if champ_name in target:
            target.remove(champ_name)
        elif target:
            target.pop()

    def _undo_pick(self, is_fp: bool, champ_name: str):
        target = self.fp_picks if is_fp else self.sp_picks
        if champ_name in target:
            target.remove(champ_name)
        elif target:
            target.pop()

    def _fill_skipped_bans(self, is_fp: bool):
        """Rellena huecos con None si se saltan bans."""
        bans = self.fp_bans if is_fp else self.sp_bans
        picks = self.fp_picks if is_fp else self.sp_picks
        num_picks = len(picks)

        # Fase 1: Antes del primer pick -> 3 bans
        if num_picks == 0:
            while len(bans) < 3:
                bans.append(None)
        # Fase 2: Antes del cuarto pick -> 5 bans
        elif num_picks == 3:
            while len(bans) < 5:
                bans.append(None)

    def _export_current_state(self) -> Dict[str, Any]:
        """NUEVO: Genera el diccionario con la foto del draft actual (sin comparaciones)."""
        return {
            "draft_found": self.draft_found,
            "is_complete": self.is_complete,
            "fp": {
                "team_id": self.first_pick_team,
                "picks": list(self.fp_picks),
                "bans": list(self.fp_bans)
            },
            "sp": {
                "team_id": self.second_pick_team,
                "picks": list(self.sp_picks),
                "bans": list(self.sp_bans)
            }
        }

    def _get_all_champions(self, draft_state: Dict[str, Any]) -> set:
        """NUEVO: Función auxiliar para extraer el conjunto único de 20 campeones de un draft."""
        champs = set(draft_state["fp"]["picks"] + draft_state["sp"]["picks"])
        for ban in draft_state["fp"]["bans"] + draft_state["sp"]["bans"]:
            if ban is not None:
                champs.add(ban)
        return champs

    # --- API PÚBLICA ---

    @property
    def draft_found(self) -> bool:
        """
        Devuelve True si existe AL MENOS un ban real (distinto de None).
        Si todos son None (Blind Pick), devuelve False.
        """
        all_bans = self.fp_bans + self.sp_bans
        return any(ban is not None for ban in all_bans)

    @property
    def is_complete(self) -> bool:
        """Devuelve True si ambos equipos han seleccionado sus 5 campeones."""
        return len(self.fp_picks) == 5 and len(self.sp_picks) == 5

    def get_draft(self) -> Dict[str, Any]:
        """Devuelve la estructura final del draft procesado."""
        current_draft = self._export_current_state()

        # NUEVO: Lógica heurística para rescatar el Draft 1 (Original) si hubo remake administrativo
        if current_draft["is_complete"] and self.draft_history:
            curr_champs = self._get_all_champions(current_draft)

            # Buscamos de atrás hacia adelante en el historial
            for hist_draft in reversed(self.draft_history):
                if hist_draft["is_complete"]:
                    hist_champs = self._get_all_champions(hist_draft)
                    
                    # Si los 20 campeones coinciden exactamente, ignoramos el Draft 2 (teatro)
                    # y devolvemos la intención original de los equipos.
                    if len(hist_champs) == 20 and hist_champs == curr_champs:
                        return hist_draft

        # Si no hubo un remake exacto, devolvemos el draft actual
        return current_draft

# --- POST GAME OBSERVER ---
class PostGameObserver(Observer):
    def __init__(self):
        self.stats: Dict[int, Dict[str, Any]] = {}
        self.winner: Optional[str] = None
        self.game_version: str = "Unknown"
        self._has_summary = False

    def notify_event(self, event: Dict[str, Any]):
        # 1. RIOT SUMMARY (Prioridad Alta)
        if event.get("source") == "RIOT_SUMMARY":
            payload = event.get("payload", {})
            self._process_summary(payload)
            self._has_summary = True
            return

        # 2. RIOT LIVESTATS (Prioridad Media - Solo si no hay Summary)
        if not self._has_summary:
            rfc_type = event.get("rfc461Schema")
            event_type = event.get("eventType")
            
            if rfc_type == "stats_update" or event_type == "stats_update":
                self._process_live_stats_update(event)
            elif rfc_type == "game_info" or event_type == "game_info":
                self._process_live_game_info(event)

    def _process_summary(self, payload: Dict[str, Any]):
        """Procesa el End State Summary."""
        # Versión
        raw_version = payload.get("gameVersion", "")
        if raw_version:
            self.game_version = ".".join(raw_version.split(".")[:2])

        # Ganador
        teams = payload.get("teams", [])
        for team in teams:
            # A veces 'win' es boolean true, a veces string "Win"
            win_val = team.get("win")
            if win_val is True or str(win_val).lower() == "win":
                self.winner = "BLUE" if team.get("teamId") == 100 else "RED"
                break
        
        # Stats Jugadores
        self._process_participants_stats(payload.get("participants", []), source="SUMMARY")

    def _process_live_stats_update(self, event: Dict[str, Any]):
        """Procesa un update del timeline."""
        # Stats Jugadores
        self._process_participants_stats(event.get("participants", []), source="LIVESTATS")

        # Ganador (Estimación por Oro)
        teams_data = event.get("teams", [])
        blue_gold = 0
        red_gold = 0
        for t in teams_data:
            tid = t.get("teamID") or t.get("teamId")
            gold = t.get("totalGold", 0)
            if tid == 100: blue_gold = gold
            elif tid == 200: red_gold = gold
        
        self.winner = "BLUE" if blue_gold > red_gold else "RED"

    def _process_live_game_info(self, event: Dict[str, Any]):
        raw = event.get("gameVersion")
        if raw:
            self.game_version = ".".join(raw.split(".")[:2])

    def _process_participants_stats(self, participants: List[Dict], source: str):
        for p in participants:
            pid = p.get("participantId") or p.get("participantID")
            if pid is None: continue

            # --- NORMALIZACIÓN DE DATOS ---
            # Caso A: Datos planos (Summary)
            if source == "SUMMARY":
                kills = p.get("kills", p.get("championsKilled", 0))
                deaths = p.get("deaths", 0)
                assists = p.get("assists", 0)
                gold = p.get("goldEarned", 0)
                cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
                dmg = p.get("totalDamageDealtToChampions", 0)
            
            # Caso B: Lista de Stats (LiveStats)
            # LiveStats trae una lista [{"name": "MINIONS_KILLED", "value": 20}, ...]
            else:
                stats_list = p.get("stats", [])
                stats_dict = {}
                if isinstance(stats_list, list):
                    for s in stats_list:
                        stats_dict[s.get("name")] = s.get("value", 0)
                
                # Extraemos del diccionario convertido
                kills = stats_dict.get("CHAMPIONS_KILLED", 0)
                deaths = stats_dict.get("NUM_DEATHS", 0)
                assists = stats_dict.get("ASSISTS", 0)
                gold = p.get("totalGold", p.get("currentGold", 0)) # El oro suele estar fuera de 'stats'
                cs = stats_dict.get("MINIONS_KILLED", 0) + stats_dict.get("NEUTRAL_MINIONS_KILLED", 0)
                dmg = stats_dict.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0)

            self.stats[pid] = {
                "kills": int(kills),
                "deaths": int(deaths),
                "assists": int(assists),
                "gold": int(gold),
                "cs": int(cs),
                "damage_dealt": int(dmg),
                "source": source
            }

    def get_game_stats(self, teams_observer: Optional['TeamsObserver'] = None) -> Dict[str, Any]:
        result = {
            "meta": {
                "winner": self.winner,
                "version": self.game_version,
                "source": "SUMMARY" if self._has_summary else "LIVESTATS"
            },
            "players": {}
        }
        for pid, data in self.stats.items():
            entry = data.copy()
            if teams_observer:
                entry["name"] = teams_observer.get_player_name(pid)
                entry["side"] = teams_observer.get_player_team(pid)
                p_obj = teams_observer.get_player_by_id(pid)
                entry["champion"] = p_obj.champion_name if p_obj else "?"
            
            entry["kda_str"] = f"{entry['kills']}/{entry['deaths']}/{entry['assists']}"
            result["players"][pid] = entry
            
        return result

# --- OBJECTIVES OBSERVER ---    
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
    
# --- WARDS OBSERVER ---   
# Pensar en una lógica que nos permita trabajar también con los wards que "mueren" o desaparecen por tiempo
# No tenemos que pensar en una lógica aquí, solo cuando queramos representarlos, tener en cuenta el tiempo que dura cada type
class WardsObserver(Observer):
    def __init__(self, teams_observer: 'TeamsObserver'):
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
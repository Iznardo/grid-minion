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

    def __repr__(self):
        return f"<{self.team_side} | {self.champion_name} ({self.summoner_name})>"

# --- TEAMS OBSERVER ---
class TeamsObserver(Observer):
    def __init__(self):
        self.teams = [[], []] 
        self._registry: Dict[int, Participant] = {}
        self._context_ready = False

    def notify_event(self, event: Dict[str, Any]):
        # Si ya tenemos los datos, generalmente no necesitamos seguir buscando,
        # pero si viene del Summary (fuente más fiable), permitimos actualizar.
        
        # --- CASO A: Fuente RIOT SUMMARY (End State) ---
        if event.get("source") == "RIOT_SUMMARY":
            payload = event.get("payload", {})
            # En el summary, la lista suele estar bajo "participants"
            self._process_participants(payload.get("participants", []))
            return

        # --- CASO B: Fuente RIOT LIVESTATS (Timeline) ---
        # Solo procesamos si no tenemos contexto o para confirmar
        if not self._context_ready:
            schema = event.get("rfc461Schema")
            #event_type = event.get("eventType")
            
            if schema == "game_info": # or event_type == "game_info":
                self._process_participants(event.get("participants", []))

    def _process_participants(self, participants_list: List[Dict]):
        # Si la lista está vacía, salimos
        if not participants_list:
            return

        self.teams = [[], []]
        self._registry = {}

        for p in participants_list:
            # 1. Extracción de IDs y Nombres (Buscamos en todos los campos posibles de Riot)
            p_id = p.get("participantId") #or p.get("participantID")
            
            # Nombre: Riot ha cambiado esto varias veces. Probamos todo.
            name = p.get("riotIdGameName")  # Formato summary
            if not name:
                name = p.get("summonerName") # Formato livestats
            # if not name:
            #     name = p.get("gameName") # A veces en metadatos esports
            
            team_id = p.get("teamId") or p.get("teamID")
            champion = p.get("championName") #or p.get("championId") ahora mismo saca el nombre, los drafts también pero son nombres diferentes (creo)

            # Solo registramos si tenemos lo mínimo vital
            if p_id is not None and name:
                player = Participant(
                    riot_id=int(p_id),
                    name=name,
                    team_id=int(team_id) if team_id else 0,
                    champion=str(champion) #si cambiamos a champion id tendrá que ser int
                )
                self._registry[player.riot_id] = player
                
                # Clasificación en listas de equipos [Blue, Red]
                if player.team_id == 100:
                    self.teams[0].append(name)
                elif player.team_id == 200:
                    self.teams[1].append(name)
                # Fallback por ID (1-5 Blue, 6-10 Red) si team_id falla
                elif 1 <= player.riot_id <= 5:
                    self.teams[0].append(name)
                else:
                    self.teams[1].append(name)

        if self._registry:
            self._context_ready = True

    # getters
    def get_player_by_id(self, riot_id: int) -> Optional[Participant]:
        return self._registry.get(riot_id)

    def get_player_name(self, riot_id: int) -> str:
        p = self._registry.get(riot_id)
        return p.summoner_name if p else "Unknown"

    def get_player_team(self, riot_id: int) -> str:
        p = self._registry.get(riot_id)
        return p.team_side if p else "UNKNOWN"

class DraftObserver(Observer):
    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        # Listas de Nombres (Strings). None indica 'No Ban' o hueco vacío.
        self.blue_bans: List[Optional[str]] = []
        self.red_bans: List[Optional[str]] = []
        self.blue_picks: List[str] = []
        self.red_picks: List[str] = []
        
        self.action_history: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        events_list = event.get("events", []) if "events" in event else [event]

        for e in events_list:
            ev_type = e.get("type")
            
            if ev_type in ["team-banned-character", "team-picked-character", 
                           "team-!banned-character", "team-!picked-character"]:
                self._process_draft_action(e)

    def _process_draft_action(self, event: Dict[str, Any]):
        action_type = event.get("type")
        
        actor = event.get("actor", {}).get("state", {})
        side = actor.get("side", "").lower() 
        
        target = event.get("target", {}).get("state", {})
        champ_name = target.get("name", "Unknown")
        
        # Historial (opcional, útil para debug)
        self.action_history.append({
            "type": action_type,
            "side": side,
            "champion": champ_name
        })

        if action_type == "team-banned-character":
            self._add_ban(side, champ_name)
            
        elif action_type == "team-picked-character":
            self._fill_skipped_bans(side)
            self._add_pick(side, champ_name)

        elif action_type == "team-!banned-character":
            self._undo_ban(side, champ_name)

        elif action_type == "team-!picked-character":
            self._undo_pick(side, champ_name)

    # --- MÉTODOS INTERNOS ---

    def _add_ban(self, side: str, champ_name: str):
        target = self.blue_bans if side == "blue" else self.red_bans
        if len(target) < 5:
            target.append(champ_name)

    def _add_pick(self, side: str, champ_name: str):
        target = self.blue_picks if side == "blue" else self.red_picks
        if len(target) < 5:
            target.append(champ_name)

    def _undo_ban(self, side: str, champ_name: str):
        target = self.blue_bans if side == "blue" else self.red_bans
        if champ_name in target:
            target.remove(champ_name)
        elif target:
            target.pop()

    def _undo_pick(self, side: str, champ_name: str):
        target = self.blue_picks if side == "blue" else self.red_picks
        if champ_name in target:
            target.remove(champ_name)
        elif target:
            target.pop()

    def _fill_skipped_bans(self, side: str):
        """Rellena huecos con None si se saltan bans."""
        bans = self.blue_bans if side == "blue" else self.red_bans
        picks = self.blue_picks if side == "blue" else self.red_picks
        num_picks = len(picks)

        # Fase 1: Antes del primer pick -> 3 bans
        if num_picks == 0:
            while len(bans) < 3:
                bans.append(None)
        # Fase 2: Antes del cuarto pick -> 5 bans
        elif num_picks == 3:
            while len(bans) < 5:
                bans.append(None)

    # --- API PÚBLICA ---

    @property
    def draft_found(self) -> bool:
        """
        Devuelve True si existe AL MENOS un ban real (distinto de None).
        Si todos son None (Blind Pick), devuelve False.
        """
        all_bans = self.blue_bans + self.red_bans
        return any(ban is not None for ban in all_bans)

    @property
    def is_complete(self) -> bool:
        """Devuelve True si ambos equipos han seleccionado sus 5 campeones."""
        return len(self.blue_picks) == 5 and len(self.red_picks) == 5

    def get_draft(self) -> Dict[str, Any]:
        """Devuelve la estructura final del draft procesado."""
        return {
            "draft_found": self.draft_found,
            "is_complete": self.is_complete,
            "blue": {
                "picks": self.blue_picks,
                "bans": self.blue_bans
            },
            "red": {
                "picks": self.red_picks,
                "bans": self.red_bans
            }
        }
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
        event_type = event.get("eventType") #eliminar, no tiene sentido, esto lo hace la IA por que quiere
        
        if schema == "game_info" or event_type == "game_info": #aquí igual, no existe event_type, lo pone por los ejemplos de bayes
            self._process_participants(event.get("participants", []))

    def _process_participants(self, participants_list: List[Dict]):
        """Parsea la lista de participantes y rellena el registro."""
        # Limpiamos
        self.teams = [[], []]
        self._registry = {}

        for p in participants_list:
            # Extraemos datos con seguridad (algunos JSON usan mayúsculas o minúsculas)
            p_id = p.get("participantID") or p.get("participantId") #nunca debería ir en mayúsculas ID
            name = p.get("summonerName")
            team_id = p.get("teamID") or p.get("teamId") #igual, nunca va en mayusculas
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

# ... (imports anteriores)

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
            
            # if ev_type == "series-started-game":
            #     self.reset()
            
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
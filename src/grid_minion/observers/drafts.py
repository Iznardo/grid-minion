import logging
from typing import Dict, Any, List, Optional
from .base import Observer

logger = logging.getLogger(__name__)

class DraftObserver(Observer):
    def __init__(self):
        # Historial global de la partida para almacenar borradores invalidados
        self.draft_history: List[Dict[str, Any]] = []
        self.reset()

    def reset(self):
        """Reinicia el estado global para una nueva serie/partida."""
        self.draft_history = []
        self._reset_current_draft()

    def _reset_current_draft(self):
        """Limpia solo la pizarra actual sin tocar el historial de borradores previos."""
        # Identificadores de equipo basados en el orden de acción
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
            
            # Interceptamos el evento del árbitro (Remake administrativo)
            if ev_type in ["grid-invalidated-series", "game-aborted"]:
                self._handle_invalidation()
                continue

            if ev_type in ["team-banned-character", "team-picked-character", 
                           "team-!banned-character", "team-!picked-character"]:
                self._process_draft_action(e)

    def _handle_invalidation(self):
        """Guarda la foto del draft abortado y limpia la mesa para el siguiente."""
        current_state = self._export_current_state()
        self.draft_history.append(current_state)
        self._reset_current_draft()

    def _process_draft_action(self, event: Dict[str, Any]):
        action_type = event.get("type")
        
        # Extraemos el ID del equipo en lugar de depender del "side"
        actor = event.get("actor", {})
        team_id = str(actor.get("id", ""))
        
        target = event.get("target", {}).get("state", {})
        champ_name = target.get("name", "Unknown")
        
        # Registramos quién es el First Pick en el momento de la primera acción
        if not self.first_pick_team:
            self.first_pick_team = team_id
        elif not self.second_pick_team and team_id != self.first_pick_team:
            self.second_pick_team = team_id

        # Evaluamos si el equipo que actúa es el que tiene First Pick
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
        """Genera el diccionario con la foto del draft actual (sin comparaciones)."""
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
        """Función auxiliar para extraer el conjunto único de 20 campeones de un draft."""
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

        # Lógica heurística para rescatar el Draft 1 (Original) si hubo remake administrativo
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

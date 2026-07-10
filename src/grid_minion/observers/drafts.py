import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from ..champions import normalize_champion

logger = logging.getLogger(__name__)

class DraftObserver(Observer):
    """
    Observador encargado de reconstruir la fase de selección y baneo (Draft).
    
    Gestiona automáticamente remakes administrativos (borradores invalidados)
    y determina quién es el First Pick a partir del primer pick de la
    secuencia (pick1 es siempre el blind pick de FP). Los bans previos al
    primer pick se bufferean y se clasifican retroactivamente: el primer ban
    observado no es fiable porque GRID puede perder los bans iniciales de un
    equipo (ver docs/bug_draft_observer_fp_dropped_bans.md).
    """
    def __init__(self):
        # Historial global de la partida para almacenar borradores invalidados
        self.draft_history: List[Dict[str, Any]] = []
        self.reset()

    def reset(self):
        """Reinicia el estado global para una nueva serie/partida."""
        self.draft_history = []
        self._fallback_draft: Optional[Dict[str, Any]] = None
        self._fallback_source: Optional[str] = None
        self._invalidations = 0
        self._aborts = 0
        self._reset_current_draft()

    def _reset_current_draft(self):
        """Limpia solo la pizarra actual sin tocar el historial de borradores previos."""
        self.first_pick_team: Optional[str] = None
        self.second_pick_team: Optional[str] = None
        self.fp_bans: List[Optional[str]] = []
        self.sp_bans: List[Optional[str]] = []
        self.fp_picks: List[str] = []
        self.sp_picks: List[str] = []
        self.action_history: List[Dict[str, Any]] = []
        # Acciones (bans/undos) llegadas antes del primer pick. FP se decide
        # con el primer 'team-picked-character' (pick1 es siempre el blind
        # pick de FP), porque GRID a veces pierde los bans iniciales del lado
        # azul en scrims y el primer ban observado NO es fiable. Estas
        # acciones se clasifican retroactivamente al conocerse FP.
        # Ver docs/bug_draft_observer_fp_dropped_bans.md.
        self._pending_actions: List[Dict[str, Any]] = []
        # El draft queda congelado al arrancar la partida: a partir de
        # 'series-started-game' una invalidación es una reconexión de feed de
        # GRID, no un remake, y NO debe descartar el draft. Ver bug del draft
        # vacío por invalidación mid-game.
        self._game_started = False

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos relacionados con picks, bans e invalidaciones."""
        if event.get("source") in ["GRID_GAME_STATE", "TENCENT_DETAILS"]:
            payload = event.get("payload", {})
            draft = payload.get("draft")
            if draft:
                self._set_fallback_draft(draft, event.get("source"))
            return

        events_list = event.get("events", []) if "events" in event else [event]

        for e in events_list:
            ev_type = e.get("type")
            if ev_type == "series-started-game":
                # La partida arranca: el draft queda congelado.
                self._game_started = True
            if ev_type in ["grid-invalidated-series", "game-aborted"]:
                if ev_type == "grid-invalidated-series":
                    self._invalidations += 1
                else:
                    self._aborts += 1
                # Solo es un remake real si llega ANTES de empezar la partida.
                # Una invalidación mid-game es una reconexión de feed de GRID y
                # descartaría un draft válido y completo.
                if not self._game_started:
                    self._handle_invalidation()
                continue
            if ev_type in ["team-banned-character", "team-picked-character",
                           "team-!banned-character", "team-!picked-character"]:
                self._process_draft_action(e)

    def _set_fallback_draft(self, draft: Dict[str, Any], source: Optional[str]):
        """Guarda un draft auxiliar completo sin interferir con eventos GRID."""
        if self._fallback_draft and self._fallback_draft.get("is_complete"):
            return
        self._fallback_draft = draft
        self._fallback_source = source.lower() if source else None

    def _handle_invalidation(self):
        """Guarda el estado del draft actual como invalidado y reinicia la mesa."""
        current_state = self._export_current_state()
        self.draft_history.append(current_state)
        self._reset_current_draft()

    def _process_draft_action(self, event: Dict[str, Any]):
        """Registra una acción individual del draft (pick/ban/undo)."""
        action_type = event.get("type")
        actor = event.get("actor", {})
        team_id = str(actor.get("id", ""))
        target = event.get("target", {}).get("state", {})
        champ_name = target.get("name", "Unknown")
        
        if self.first_pick_team is None:
            if action_type != "team-picked-character":
                # FP aún desconocido: bufferizar y clasificar cuando llegue
                # el primer pick. No usar el primer ban como señal de FP:
                # GRID puede haber perdido los bans iniciales del otro equipo.
                self._pending_actions.append({
                    "type": action_type,
                    "team_id": team_id,
                    "champion": champ_name,
                })
                return
            # Primer pick real: pick1 es el blind pick de FP por formato.
            self.first_pick_team = team_id
            self._flush_pending_actions()
        elif not self.second_pick_team and team_id != self.first_pick_team:
            self.second_pick_team = team_id

        self._apply_action(action_type, team_id, champ_name)

    def _apply_action(self, action_type: str, team_id: str, champ_name: str):
        """Clasifica y aplica una acción de draft con FP ya conocido."""
        is_fp = (team_id == self.first_pick_team)
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

    def _flush_pending_actions(self):
        """Clasifica retroactivamente las acciones previas al primer pick."""
        for action in self._pending_actions:
            team_id = action["team_id"]
            if not self.second_pick_team and team_id != self.first_pick_team:
                self.second_pick_team = team_id
            self._apply_action(action["type"], team_id, action["champion"])
        self._pending_actions = []

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
        """Rellena con None si un equipo se salta un baneo.

        El flujo de draft competitivo de LoL es:
            3 bans (FP) + 3 bans (SP)  →  3 picks (FP) + 3 picks (SP)
            2 bans (FP) + 2 bans (SP)  →  2 picks (SP) + 2 picks (FP)

        Los únicos puntos donde un equipo puede pickear sin haber completado
        sus bans son: a 0 picks (saltarse bans del primer set) y a 3 picks
        (saltarse bans del segundo set). Por eso solo hay dos ramas aquí.
        """
        bans = self.fp_bans if is_fp else self.sp_bans
        picks = self.fp_picks if is_fp else self.sp_picks
        num_picks = len(picks)
        if num_picks == 0:
            while len(bans) < 3:
                bans.append(None)
        elif num_picks == 3:
            while len(bans) < 5:
                bans.append(None)

    def _export_current_state(self) -> Dict[str, Any]:
        """Exporta el estado actual del borrador en formato diccionario.

        Si el feed nunca trajo un pick (draft parcial roto), las acciones
        pendientes se resuelven aquí con la heurística antigua (primer actor
        observado = FP) sin mutar el estado: si luego llegara un pick, la
        clasificación real seguiría mandando.
        """
        fp_team = self.first_pick_team
        sp_team = self.second_pick_team
        fp_bans = list(self.fp_bans)
        sp_bans = list(self.sp_bans)
        if self._pending_actions and fp_team is None:
            fp_team = self._pending_actions[0]["team_id"]
            sp_team = next(
                (a["team_id"] for a in self._pending_actions
                 if a["team_id"] != fp_team),
                None,
            )
            for action in self._pending_actions:
                bans = fp_bans if action["team_id"] == fp_team else sp_bans
                if action["type"] == "team-banned-character":
                    if len(bans) < 5:
                        bans.append(action["champion"])
                elif action["type"] == "team-!banned-character":
                    if action["champion"] in bans:
                        bans.remove(action["champion"])
                    elif bans:
                        bans.pop()
        return {
            "draft_found": self.draft_found,
            "is_complete": self.is_complete,
            "fp": {
                "team_id": fp_team,
                "picks": list(self.fp_picks),
                "bans": fp_bans
            },
            "sp": {
                "team_id": sp_team,
                "picks": list(self.sp_picks),
                "bans": sp_bans
            }
        }

    def _get_all_champions(self, draft_state: Dict[str, Any]) -> set:
        """Obtiene el conjunto único de campeones involucrados en un draft."""
        champs = set(draft_state["fp"]["picks"] + draft_state["sp"]["picks"])
        for ban in draft_state["fp"]["bans"] + draft_state["sp"]["bans"]:
            if ban is not None:
                champs.add(ban)
        return champs

    @property
    def draft_found(self) -> bool:
        """True si se ha detectado al menos un baneo o pick."""
        all_bans = self.fp_bans + self.sp_bans
        return (
            any(ban is not None for ban in all_bans)
            or len(self.fp_picks) > 0
            or any(a["type"] == "team-banned-character"
                   for a in self._pending_actions)
        )

    @property
    def is_complete(self) -> bool:
        """True si ambos equipos han seleccionado sus 5 campeones."""
        return len(self.fp_picks) == 5 and len(self.sp_picks) == 5

    def _normalize_champion(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Convierte un nombre crudo de campeón en `{name: clave Riot, id: numérico}`.

        Los baneos saltados (`None`) se conservan como `None`.
        """
        if name is None:
            return None
        if isinstance(name, dict):
            raw_name = name.get("name")
            numeric_id = name.get("id")
            if raw_name:
                riot_id, resolved_id = normalize_champion(raw_name)
                return {"name": riot_id, "id": numeric_id or resolved_id}
            return {"name": None, "id": int(numeric_id) if numeric_id else None}
        riot_id, numeric_id = normalize_champion(name)
        return {"name": riot_id, "id": numeric_id}

    def _normalize_draft(self, draft_state: Dict[str, Any]) -> Dict[str, Any]:
        """Devuelve una copia del draft con picks/bans normalizados a `{name, id}`."""
        normalized = {
            "draft_found": draft_state["draft_found"],
            "is_complete": draft_state["is_complete"],
        }
        for side in ("fp", "sp"):
            data = draft_state[side]
            normalized[side] = {
                "team_id": data["team_id"],
                "picks": [self._normalize_champion(c) for c in data["picks"]],
                "bans": [self._normalize_champion(c) for c in data["bans"]],
            }
        return normalized

    def get_draft(self) -> Dict[str, Any]:
        """
        Devuelve la estructura final del draft procesado.

        Rescata automáticamente borradores previos si coinciden exactamente con el
        actual (caso side-swap: misma partida con lados invertidos, sin tecnología
        de side-pick). Si los 20 campeones no coinciden exactamente, es un remake
        real y se devuelve el draft más reciente.

        Los campeones salen normalizados a la clave de Riot (`MonkeyKing`) con su
        id numérico, vía Data Dragon. Formato de cada pick/ban:
        `{"name": <clave Riot>, "id": <int>}` (los baneos saltados son `None`).
        """
        current_draft = self._export_current_state()
        chosen = current_draft
        if current_draft["is_complete"] and self.draft_history:
            curr_champs = self._get_all_champions(current_draft)
            for hist_draft in reversed(self.draft_history):
                if hist_draft["is_complete"]:
                    hist_champs = self._get_all_champions(hist_draft)
                    if len(hist_champs) == 20 and hist_champs == curr_champs:
                        chosen = hist_draft
                        break
        elif self._fallback_draft and (
            not current_draft["draft_found"] or not current_draft["is_complete"]
        ):
            # LPL puede invalidar/partir el feed de GRID y dejar picks fuera del
            # stream. `state-grid` trae las 20 acciones finales y es un fallback
            # explícito, no un cambio del contrato público.
            chosen = self._fallback_draft
        return self._normalize_draft(chosen)

    def get_draft_status(self) -> Dict[str, Any]:
        """Devuelve diagnóstico de calidad/fuentes sin alterar `get_draft()`."""
        current = self._export_current_state()
        uses_fallback = bool(
            self._fallback_draft
            and (not current["draft_found"] or not current["is_complete"])
        )
        return {
            "current_complete": current["is_complete"],
            "current_found": current["draft_found"],
            "fallback_available": self._fallback_draft is not None,
            "fallback_complete": bool(self._fallback_draft and self._fallback_draft.get("is_complete")),
            "uses_fallback": uses_fallback,
            "fallback_source": self._fallback_source,
            "invalidations": self._invalidations,
            "aborts": self._aborts,
            "game_started": self._game_started,
        }

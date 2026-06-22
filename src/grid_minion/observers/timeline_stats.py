import logging
from typing import Dict, Any, List, Optional, Sequence
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class MidGameStatsObserver(Observer):
    """
    Observador que captura snapshots de **CS, oro y XP** por jugador en marcas de
    tiempo concretas (por defecto minuto 7 y minuto 14), a partir de los eventos
    `stats_update` de la timeline de Riot (livestats RFC461).

    Sirve para analizar el progreso temprano (laning phase) sin depender del
    end-state. Se mantiene separado de `PostGameObserver` a propósito: aquel
    reconstruye el resultado final, este muestrea instantes intermedios.

    Semántica del snapshot: para cada marca se guarda el **último** `stats_update`
    con `gameTime <= marca` (estado "a fecha de minuto X"). Si la partida termina
    antes de alcanzar una marca, esa marca queda con valores `None` (no se inventa).

    Nomenclatura RFC461 verificada contra el feed real de GRID:
      - id de jugador: `participantID` (ojo, con ID mayúscula; en `game_info` es
        `participantId`). Se normalizan ambos.
      - XP: campo `XP` a nivel de participante.
      - oro: campo `totalGold` a nivel de participante (no `currentGold`, que es el
        oro disponible para gastar).
      - CS: lista `stats` con `MINIONS_KILLED` + `NEUTRAL_MINIONS_KILLED` (este
        último puede venir como float, p.ej. `40.00000762939453`).
    """

    DEFAULT_MARKS = (7, 14)

    def __init__(self, marks_minutes: Sequence[int] = DEFAULT_MARKS):
        """
        Args:
            marks_minutes (Sequence[int]): Minutos en los que tomar snapshot.
                Default `(7, 14)`.
        """
        self._marks = sorted(int(m) for m in marks_minutes)
        # marca(min) -> gameTime límite en ms
        self._marks_ms = {m: m * 60_000 for m in self._marks}
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        # pid -> {marca(min): {"cs", "gold", "xp", "game_time_ms"}}
        self._snapshots: Dict[int, Dict[int, Dict[str, Any]]] = {}
        # Mayor gameTime visto: una marca solo es válida si la partida la alcanzó.
        self._max_game_time: int = -1

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos `stats_update` de la timeline."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema != "stats_update":
            return
        game_time = int(event.get("gameTime", 0))
        if game_time > self._max_game_time:
            self._max_game_time = game_time
        for p in event.get("participants", []):
            self._record_participant(p, game_time)

    def _record_participant(self, participant: Dict[str, Any], game_time: int):
        pid = participant.get("participantID")
        if pid is None:
            pid = participant.get("participantId")
        if pid is None:
            return
        pid = int(pid)

        snapshot = {
            "cs": self._extract_cs(participant),
            "gold": self._extract_int(participant, "totalGold"),
            "xp": self._extract_int(participant, "XP"),
            "game_time_ms": game_time,
        }

        marks = self._snapshots.setdefault(pid, {})
        for mark, limit_ms in self._marks_ms.items():
            if game_time > limit_ms:
                continue
            current = marks.get(mark)
            # Nos quedamos con el último stats_update <= marca. Los eventos llegan
            # en orden, pero comparamos game_time por robustez ante desorden.
            if current is None or game_time >= current["game_time_ms"]:
                marks[mark] = snapshot

    @staticmethod
    def _extract_cs(participant: Dict[str, Any]) -> Optional[int]:
        """CS = MINIONS_KILLED + NEUTRAL_MINIONS_KILLED desde la lista `stats`."""
        stats_list = participant.get("stats")
        if not isinstance(stats_list, list):
            return None
        stats = {s.get("name"): s.get("value", 0) for s in stats_list}
        if "MINIONS_KILLED" not in stats and "NEUTRAL_MINIONS_KILLED" not in stats:
            return None
        minions = stats.get("MINIONS_KILLED", 0) or 0
        neutrals = stats.get("NEUTRAL_MINIONS_KILLED", 0) or 0
        return int(minions) + int(neutrals)

    @staticmethod
    def _extract_int(participant: Dict[str, Any], key: str) -> Optional[int]:
        value = participant.get(key)
        return int(value) if value is not None else None

    def get_mid_game_stats(
        self, teams_observer: Optional[TeamsObserver] = None
    ) -> Dict[int, Dict[str, Any]]:
        """
        Devuelve, por `participantId`, los snapshots en cada marca de tiempo.

        Args:
            teams_observer (Optional[TeamsObserver]): Si se proporciona, añade
                nombre, lado y campeón de cada jugador.

        Returns:
            Dict[int, Dict[str, Any]]: Estructura
            `{pid: {"marks": {7: {"cs", "gold", "xp", "game_time_s"}, 14: {...}}}}`.
            Las marcas no alcanzadas tienen `cs`/`gold`/`xp`/`game_time_s` en `None`.
        """
        result: Dict[int, Dict[str, Any]] = {}
        for pid in sorted(self._snapshots):
            marks_out: Dict[int, Dict[str, Any]] = {}
            recorded = self._snapshots.get(pid, {})
            for mark in self._marks:
                snap = recorded.get(mark)
                # Marca no alcanzada por la partida: snapshot del 7:00 NO debe
                # reportarse como dato del 14:00 (sería un valor obsoleto).
                if self._max_game_time < self._marks_ms[mark]:
                    snap = None
                if snap is None:
                    marks_out[mark] = {
                        "cs": None, "gold": None, "xp": None, "game_time_s": None
                    }
                else:
                    marks_out[mark] = {
                        "cs": snap["cs"],
                        "gold": snap["gold"],
                        "xp": snap["xp"],
                        "game_time_s": snap["game_time_ms"] / 1000,
                    }
            entry: Dict[str, Any] = {"marks": marks_out}
            if teams_observer:
                entry["name"] = teams_observer.get_player_name(pid)
                entry["side"] = teams_observer.get_player_team(pid)
                p_obj = teams_observer.get_player_by_id(pid)
                entry["champion"] = p_obj.champion_name if p_obj else "?"
                entry["champion_id"] = p_obj.champion_id if p_obj else None
            result[pid] = entry
        return result

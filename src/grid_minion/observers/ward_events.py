import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class WardEventsObserver(Observer):
    """
    Observador del **ciclo de vida de la visión**: colocación (`ward_placed`) y
    destrucción (`ward_killed`) de centinelas, desde la timeline de Riot (RFC461).

    Se mantiene separado del `WardsObserver` existente (que solo registra
    colocación) para no romper su API; cuando esta capa esté consolidada se evaluará
    fusionarlos. Depende de `TeamsObserver` para resolver nombres y lados.

    Nomenclatura RFC461 verificada contra el feed real (serie `2930129`):
      - `ward_placed`: `placer`, `wardType` ∈ {sight, control, blueTrinket,
        yellowTrinket, unknown}, `position.{x,z}`.
      - `ward_killed`: `killer`, `wardType`, `position.{x,z}`.
    """

    def __init__(self, teams_observer: TeamsObserver):
        """
        Args:
            teams_observer (TeamsObserver): Observador de equipos para resolver
                nombres y lados de quien coloca/destruye.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia las listas registradas."""
        self.placements: List[Dict[str, Any]] = []
        self.kills: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa `ward_placed` y `ward_killed`."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "ward_placed":
            self._process_placed(event)
        elif schema == "ward_killed":
            self._process_killed(event)

    def _process_placed(self, event: Dict[str, Any]):
        try:
            placer = self._pid(event.get("placer"))
            self.placements.append({
                "time": event.get("gameTime", 0) / 1000,
                "player": self._name(placer),
                "player_id": placer,
                "team": self._side(placer),
                "type": event.get("wardType", "unknown"),
                "position": self._position(event),
            })
        except Exception:
            logger.warning("Error procesando ward_placed", exc_info=True)

    def _process_killed(self, event: Dict[str, Any]):
        try:
            killer = self._pid(event.get("killer"))
            self.kills.append({
                "time": event.get("gameTime", 0) / 1000,
                "killer": self._name(killer),
                "killer_id": killer,
                "team": self._side(killer),
                "type": event.get("wardType", "unknown"),
                "position": self._position(event),
            })
        except Exception:
            logger.warning("Error procesando ward_killed", exc_info=True)

    @staticmethod
    def _position(event: Dict[str, Any]) -> Dict[str, Any]:
        raw = event.get("position", {}) or {}
        x = raw.get("x")
        y = raw.get("z") if "z" in raw else raw.get("y")
        return {"x": x, "y": y}

    @staticmethod
    def _pid(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _name(self, pid: Optional[int]) -> Optional[str]:
        return self.teams_observer.get_player_name(pid) if pid is not None else None

    def _side(self, pid: Optional[int]) -> Optional[str]:
        return self.teams_observer.get_player_team(pid) if pid is not None else None

    def get_placements(self) -> List[Dict[str, Any]]:
        """Colocaciones de ward, en orden."""
        return self.placements

    def get_kills(self) -> List[Dict[str, Any]]:
        """Destrucciones de ward, en orden."""
        return self.kills

    def get_events(self) -> List[Dict[str, Any]]:
        """Colocaciones y destrucciones fusionadas y ordenadas por tiempo."""
        placed = [{**p, "action": "placed"} for p in self.placements]
        killed = [{**k, "action": "killed"} for k in self.kills]
        return sorted(placed + killed, key=lambda e: e["time"])

import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class BuildingObserver(Observer):
    """
    Observador de **estructuras**: torres, inhibidores y nexo (`building_destroyed`),
    placas de torreta (`turret_plate_destroyed`) y reaparición de inhibidores
    (`building_respawned`), desde la timeline de Riot (RFC461).

    `teamID` en estos eventos es el equipo **dueño** de la estructura destruida; el
    equipo que la derriba es el contrario. Depende opcionalmente de `TeamsObserver`
    para resolver nombre/lado del último golpe.

    Nomenclatura RFC461 verificada contra el feed real (serie `2930129`):
      - `building_destroyed`: `buildingType` ∈ {turret, inhibitor, nexus}, `lane`
        ∈ {top, mid, bot}, `turretTier` ∈ {outer, inner, base, nexus}, `teamID`
        (dueño), `lastHitter`, `assistants`, `bountyGold`, `nexusTurretName`,
        `position.{x,z}`.
      - `turret_plate_destroyed`: `lane`, `teamID` (dueño), `lastHitter`, `assistants`.
      - `building_respawned`: `buildingType` (inhibitor), `lane`.
    """

    _SIDE = {100: "BLUE", 200: "RED"}

    def __init__(self, teams_observer: Optional[TeamsObserver] = None):
        """
        Args:
            teams_observer (Optional[TeamsObserver]): Si se proporciona, resuelve el
                nombre/lado del `lastHitter`.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia las listas registradas."""
        self.buildings: List[Dict[str, Any]] = []
        self.plates: List[Dict[str, Any]] = []
        self.respawns: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos de estructuras."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "building_destroyed":
            self._process_destroyed(event)
        elif schema == "turret_plate_destroyed":
            self._process_plate(event)
        elif schema == "building_respawned":
            self._process_respawn(event)

    def _process_destroyed(self, event: Dict[str, Any]):
        try:
            owner_id = event.get("teamID")
            last_hitter = self._pid(event.get("lastHitter"))
            self.buildings.append({
                "time": event.get("gameTime", 0) / 1000,
                "building_type": event.get("buildingType"),
                "lane": event.get("lane"),
                "turret_tier": event.get("turretTier"),
                "owner_team": self._side_from_team_id(owner_id),
                "killed_by_team": self._opponent(owner_id),
                "last_hitter": self._name(last_hitter),
                "last_hitter_id": last_hitter,
                "assistants": self._pids(event.get("assistants")),
                "bounty_gold": event.get("bountyGold"),
                "nexus_turret_name": event.get("nexusTurretName"),
                "position": self._position(event),
            })
        except Exception:
            logger.warning("Error procesando building_destroyed", exc_info=True)

    def _process_plate(self, event: Dict[str, Any]):
        try:
            owner_id = event.get("teamID")
            last_hitter = self._pid(event.get("lastHitter"))
            self.plates.append({
                "time": event.get("gameTime", 0) / 1000,
                "lane": event.get("lane"),
                "owner_team": self._side_from_team_id(owner_id),
                "killed_by_team": self._opponent(owner_id),
                "last_hitter": self._name(last_hitter),
                "last_hitter_id": last_hitter,
                "assistants": self._pids(event.get("assistants")),
                "position": self._position(event),
            })
        except Exception:
            logger.warning("Error procesando turret_plate_destroyed", exc_info=True)

    def _process_respawn(self, event: Dict[str, Any]):
        try:
            self.respawns.append({
                "time": event.get("gameTime", 0) / 1000,
                "building_type": event.get("buildingType"),
                "lane": event.get("lane"),
                "owner_team": self._side_from_team_id(event.get("teamID")),
            })
        except Exception:
            logger.warning("Error procesando building_respawned", exc_info=True)

    # ---------- helpers ----------

    def _side_from_team_id(self, team_id: Any) -> str:
        try:
            return self._SIDE.get(int(team_id), "UNKNOWN") if team_id is not None else "UNKNOWN"
        except (TypeError, ValueError):
            return "UNKNOWN"

    def _opponent(self, team_id: Any) -> str:
        side = self._side_from_team_id(team_id)
        if side == "BLUE":
            return "RED"
        if side == "RED":
            return "BLUE"
        return "UNKNOWN"

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

    def _pids(self, values: Any) -> List[int]:
        if not isinstance(values, list):
            return []
        return [p for p in (self._pid(v) for v in values) if p is not None]

    def _name(self, pid: Optional[int]) -> Optional[str]:
        if pid is None or self.teams_observer is None:
            return None
        return self.teams_observer.get_player_name(pid)

    def get_buildings(self) -> List[Dict[str, Any]]:
        """Estructuras destruidas (torres/inhibidores/nexo), en orden."""
        return self.buildings

    def get_turrets(self) -> List[Dict[str, Any]]:
        """Solo torres destruidas."""
        return [b for b in self.buildings if b["building_type"] == "turret"]

    def get_inhibitors(self) -> List[Dict[str, Any]]:
        """Solo inhibidores destruidos."""
        return [b for b in self.buildings if b["building_type"] == "inhibitor"]

    def get_plates(self) -> List[Dict[str, Any]]:
        """Placas de torreta destruidas."""
        return self.plates

    def get_respawns(self) -> List[Dict[str, Any]]:
        """Reapariciones de inhibidor."""
        return self.respawns

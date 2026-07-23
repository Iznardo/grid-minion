import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class CombatObserver(Observer):
    """
    Observador de la **timeline de combate** desde `champion_kill` y
    `champion_kill_special` de la timeline de Riot (livestats RFC461).

    A diferencia de `SoloKillObserver` (que solo aísla las muertes 1v1), aquí se
    registran **todas** las muertes con su contexto: asistentes, posición, duración
    de la pelea, botín (`shutdownBounty`/`bounty`), racha y el **desglose de daño**
    de la víctima (`deathRecap`). Además marca los eventos especiales (firstBlood,
    ace, multikill).

    Depende de `TeamsObserver` (como `WardsObserver`/`SoloKillObserver`) para resolver
    nombres y lados. Respeta el orden de `attach()`.

    Nomenclatura RFC461 verificada contra el feed real (serie `2930129`):
      - `champion_kill`: `killer`, `victim`, `assistants`, `killerTeamID`,
        `victimTeamID`, `position.{x,z}`, `fightDuration`, `killStreakLength`,
        `shutdownBounty`, `bounty`, `deathRecap[]` (`source`, `casterId`, `breakdown`).
      - `champion_kill_special`: `killType` ∈ {firstBlood, ace, multi}, `killer`, `position`.
    """

    def __init__(self, teams_observer: TeamsObserver):
        """
        Args:
            teams_observer (TeamsObserver): Observador de equipos para resolver
                nombres y lados de killer/víctima/asistentes.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia las listas registradas."""
        self.kills: List[Dict[str, Any]] = []
        self.special_events: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa `champion_kill` y `champion_kill_special`."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "champion_kill":
            self._process_kill(event)
        elif schema == "champion_kill_special":
            self._process_special(event)

    def _process_kill(self, event: Dict[str, Any]):
        try:
            killer = self._pid(event.get("killer"))
            victim = self._pid(event.get("victim"))
            assistants = [self._pid(a) for a in (event.get("assistants") or [])]
            assistants = [a for a in assistants if a is not None]

            self.kills.append({
                "time": event.get("gameTime", 0) / 1000,
                "killer": self._name(killer),
                "killer_id": killer,
                "killer_side": self._side(killer),
                "victim": self._name(victim),
                "victim_id": victim,
                "victim_side": self._side(victim),
                "assistants": [
                    {"id": a, "name": self._name(a), "side": self._side(a)}
                    for a in assistants
                ],
                "position": self._position(event),
                "fight_duration": event.get("fightDuration"),
                "killstreak": event.get("killStreakLength"),
                "shutdown_bounty": event.get("shutdownBounty"),
                "bounty": event.get("bounty"),
                "damage_breakdown": self._damage_breakdown(event.get("deathRecap")),
            })
        except Exception:
            logger.warning("Error procesando champion_kill", exc_info=True)

    def _process_special(self, event: Dict[str, Any]):
        try:
            killer = self._pid(event.get("killer"))
            self.special_events.append({
                "time": event.get("gameTime", 0) / 1000,
                "type": event.get("killType"),
                "killer": self._name(killer),
                "killer_id": killer,
                "killer_side": self._side(killer),
                "killstreak": event.get("killStreakLength"),
                "position": self._position(event),
            })
        except Exception:
            logger.warning("Error procesando champion_kill_special", exc_info=True)

    @staticmethod
    def _damage_breakdown(death_recap: Any) -> List[Dict[str, Any]]:
        """Normaliza `deathRecap` a `[{source, caster_id, breakdown}]`."""
        if not isinstance(death_recap, list):
            return []
        out = []
        for entry in death_recap:
            if not isinstance(entry, dict):
                continue
            out.append({
                "source": entry.get("source"),
                "caster_id": entry.get("casterId"),
                "breakdown": entry.get("breakdown"),
            })
        return out

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

    def get_kills(self) -> List[Dict[str, Any]]:
        """Todas las muertes de campeón con contexto, en orden."""
        return self.kills

    def get_special_events(self) -> List[Dict[str, Any]]:
        """Eventos especiales (firstBlood, ace, multikill), en orden."""
        return self.special_events

    def get_kda_timeline(self) -> Dict[int, List[Dict[str, Any]]]:
        """K/D/A **acumulado** por jugador tras cada muerte.

        Devuelve `{pid: [{time, kills, deaths, assists}]}`: una entrada por cada
        muerte que afecta al jugador (como killer, víctima o asistente).
        """
        acc: Dict[int, Dict[str, int]] = {}
        timeline: Dict[int, List[Dict[str, Any]]] = {}

        def touch(pid: Optional[int], field: str, time: float):
            if pid is None:
                return
            counters = acc.setdefault(pid, {"kills": 0, "deaths": 0, "assists": 0})
            counters[field] += 1
            timeline.setdefault(pid, []).append({"time": time, **counters.copy()})

        for k in self.kills:
            touch(k["killer_id"], "kills", k["time"])
            touch(k["victim_id"], "deaths", k["time"])
            for a in k["assistants"]:
                touch(a["id"], "assists", k["time"])
        return timeline

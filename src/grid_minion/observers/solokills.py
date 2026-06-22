import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class SoloKillObserver(Observer):
    """
    Observador que detecta **solokills**: muertes de campeón en las que participa
    un solo jugador, es decir un `champion_kill` **sin asistentes**.

    Por cada solokill registra al ejecutor, la víctima, el instante y la posición
    en el mapa. Requiere `TeamsObserver` (igual que `WardsObserver`) para resolver
    nombres y lados; respeta el orden de `attach()`: el `TeamsObserver` debe
    procesar los eventos iniciales antes para no ver nombres "Unknown".

    Criterio de solokill (decidido con el usuario):
      - `assistants` vacío.
      - killer y víctima son jugadores reales (participantId 1-10). Esto excluye
        ejecuciones por torre/minion (killer fuera de rango o ausente).
    """

    def __init__(self, teams_observer: TeamsObserver):
        """
        Args:
            teams_observer (TeamsObserver): Observador de equipos para resolver
                nombres y lados de killer/víctima.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia la lista de solokills registradas."""
        self.solokills: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos `champion_kill` de Riot LiveStats."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema != "champion_kill":
            return
        self._process_champion_kill(event)

    def _process_champion_kill(self, event: Dict[str, Any]):
        try:
            assistants = event.get("assistants") or []
            if assistants:
                return  # participan varios jugadores: no es solokill

            killer = self._participant_id(event, "killer", "killerId")
            victim = self._participant_id(event, "victim", "victimId")
            if not self._is_player(killer) or not self._is_player(victim):
                return  # ejecución por torre/minion o datos incompletos

            raw_pos = event.get("position", {}) or {}
            pos_x = raw_pos.get("x")
            pos_y = raw_pos.get("z") if "z" in raw_pos else raw_pos.get("y")

            self.solokills.append({
                "time": event.get("gameTime", 0) / 1000,
                "killer": self.teams_observer.get_player_name(killer),
                "killer_id": killer,
                "killer_side": self.teams_observer.get_player_team(killer),
                "victim": self.teams_observer.get_player_name(victim),
                "victim_id": victim,
                "victim_side": self.teams_observer.get_player_team(victim),
                "position": {"x": pos_x, "y": pos_y},
            })
        except Exception:
            logger.warning("Error procesando champion_kill", exc_info=True)

    @staticmethod
    def _participant_id(event: Dict[str, Any], *keys: str) -> Optional[int]:
        """Lee el primer id de jugador presente entre `keys` y lo normaliza a int."""
        for key in keys:
            value = event.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _is_player(pid: Optional[int]) -> bool:
        """Un jugador real tiene participantId 1-10."""
        return pid is not None and 1 <= pid <= 10

    def get_solokills(self) -> List[Dict[str, Any]]:
        """Devuelve la lista de solokills detectadas, en orden de aparición."""
        return self.solokills

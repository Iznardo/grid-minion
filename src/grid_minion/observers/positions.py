import bisect
import logging
from array import array
from typing import Any, Dict, Iterator, List, Optional

from .base import Observer
from ._ticks import TickSampler, participant_id

logger = logging.getLogger(__name__)

# Rol del feed de Riot -> convención del resto del ecosistema (gridExtractor).
ROLE_MAP = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "middle": "MID",
    "mid": "MID",
    "bottom": "ADC",
    "adc": "ADC",
    "support": "SUPPORT",
    "utility": "SUPPORT",
}

# Valor de columna para "sin dato en este tick".
_NO_POS = -1
_ALIVE_UNKNOWN = -1


def side_of(team_id: Any, participant_id: Optional[int] = None) -> Optional[str]:
    """`BLUE`/`RED` desde el teamID de Riot (100/200) o, si falta, del id 1-10."""
    if str(team_id) == "100":
        return "BLUE"
    if str(team_id) == "200":
        return "RED"
    if participant_id is not None and 1 <= participant_id <= 10:
        return "BLUE" if participant_id <= 5 else "RED"
    return None


def player_meta(participant: Dict[str, Any], pid: int) -> Dict[str, Any]:
    """Metadatos de un jugador desde un participante de `stats_update`/`game_info`."""
    riot_id = participant.get("riotId")
    name = (
        participant.get("playerName")
        or participant.get("summonerName")
        or (riot_id.get("displayName") if isinstance(riot_id, dict) else riot_id)
    )
    team_id = participant.get("teamID", participant.get("teamId"))
    role = participant.get("role")
    return {
        "name": name,
        "champion": participant.get("championName"),
        "team_id": int(team_id) if team_id is not None else None,
        "side": side_of(team_id, pid),
        "role": ROLE_MAP.get(str(role).lower()) if role else None,
    }


class PositionTrack:
    """
    Trayectorias de los jugadores de una partida, en columnas compactas.

    Todos los jugadores comparten el mismo reloj (`t_ms`): la muestra `i` de
    cada jugador corresponde al instante `t_ms[i]`. Una partida de 30 minutos
    a 1 muestra por segundo ocupa ~170 KB.

    Coordenadas en unidades de mapa de Riot (0 a ~14 800 en ambos ejes), con
    `y` = el `z` del feed, como en el resto de la librería. Un jugador muerto
    conserva la posición donde murió, con `alive = False`.
    """

    def __init__(self):
        self.t_ms = array("l")
        self.x: Dict[int, array] = {}
        self.y: Dict[int, array] = {}
        self.alive: Dict[int, array] = {}
        self.players: Dict[int, Dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self.t_ms)

    @property
    def pids(self) -> List[int]:
        """Ids de jugador (1-10) con trayectoria, ordenados."""
        return sorted(self.x)

    @property
    def duration_s(self) -> float:
        return self.t_ms[-1] / 1000 if self.t_ms else 0.0

    def _sample(self, pid: int, i: int) -> Optional[Dict[str, Any]]:
        x = self.x[pid][i]
        if x == _NO_POS:
            return None
        alive = self.alive[pid][i]
        return {
            "t": self.t_ms[i] / 1000,
            "x": x,
            "y": self.y[pid][i],
            "alive": None if alive == _ALIVE_UNKNOWN else bool(alive),
        }

    def _index_at(self, t_s: float) -> int:
        """Índice de la última muestra con `t <= t_s` (-1 si no hay ninguna)."""
        return bisect.bisect_right(self.t_ms, int(round(t_s * 1000))) - 1

    def at(self, pid: int, t_s: float) -> Optional[Dict[str, Any]]:
        """Posición del jugador en `t_s`: última muestra con `t <= t_s`.

        `None` si el jugador no existe, si `t_s` es anterior a la primera
        muestra o si esa muestra no traía posición.
        """
        if pid not in self.x:
            return None
        i = self._index_at(t_s)
        return self._sample(pid, i) if i >= 0 else None

    def between(self, pid: int, t0_s: float, t1_s: float) -> List[Dict[str, Any]]:
        """Tramo de trayectoria con `t0_s <= t <= t1_s`: `[{t, x, y, alive}]`."""
        if pid not in self.x:
            return []
        lo = bisect.bisect_left(self.t_ms, int(round(t0_s * 1000)))
        hi = bisect.bisect_right(self.t_ms, int(round(t1_s * 1000)))
        samples = (self._sample(pid, i) for i in range(lo, hi))
        return [s for s in samples if s is not None]

    def path(self, pid: int) -> List[Dict[str, Any]]:
        """Trayectoria completa del jugador."""
        if pid not in self.x:
            return []
        samples = (self._sample(pid, i) for i in range(len(self.t_ms)))
        return [s for s in samples if s is not None]

    def rows(self) -> Iterator[Dict[str, Any]]:
        """Filas `{t, pid, x, y, alive}` de todos los jugadores, por tiempo."""
        for i in range(len(self.t_ms)):
            for pid in self.pids:
                sample = self._sample(pid, i)
                if sample is not None:
                    yield {"t": sample["t"], "pid": pid, "x": sample["x"],
                           "y": sample["y"], "alive": sample["alive"]}

    # ---------- serialización ----------

    def to_dict(self) -> Dict[str, Any]:
        """Forma columnar serializable a JSON. `null` = sin dato en ese tick."""
        def col(values, missing):
            return [None if v == missing else v for v in values]
        return {
            "t_ms": list(self.t_ms),
            "x": {str(pid): col(self.x[pid], _NO_POS) for pid in self.pids},
            "y": {str(pid): col(self.y[pid], _NO_POS) for pid in self.pids},
            "alive": {str(pid): col(self.alive[pid], _ALIVE_UNKNOWN) for pid in self.pids},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any],
                  players: Optional[Dict[int, Dict[str, Any]]] = None) -> "PositionTrack":
        track = cls()
        track.t_ms = array("l", data.get("t_ms", []))
        for key, values in (data.get("x") or {}).items():
            pid = int(key)
            track.x[pid] = array("l", (_NO_POS if v is None else int(v) for v in values))
            ys = data["y"][key]
            track.y[pid] = array("l", (_NO_POS if v is None else int(v) for v in ys))
            alive = (data.get("alive") or {}).get(key) or [None] * len(values)
            track.alive[pid] = array(
                "b", (_ALIVE_UNKNOWN if v is None else int(bool(v)) for v in alive))
        track.players = dict(players or {})
        return track


class PositionObserver(Observer):
    """
    Observador que registra la **posición de cada jugador** a lo largo de la
    partida, y nada más, a partir de los `stats_update` de Riot LiveStats.

    El feed da una muestra por segundo (no hay posición a mayor frecuencia).
    Se guarda en columnas compactas (`PositionTrack`), no en un diccionario
    por tick. No depende de ningún otro observer: nombre, campeón, lado y rol
    de cada jugador salen del propio `stats_update`.

    Es opt-in: solo actúa si se engancha al procesador.
    """

    schemas = frozenset({"stats_update"})

    def __init__(self, sample_every_ms: Optional[int] = None,
                 t_from_s: Optional[float] = None, t_to_s: Optional[float] = None):
        """
        Args:
            sample_every_ms: Separación mínima entre muestras guardadas. `None`
                (default) guarda todas las del feed (~1 por segundo).
            t_from_s / t_to_s: Si se indican, solo se guardan muestras dentro
                de esa ventana de tiempo de juego (segundos).
        """
        self._sampler = TickSampler(sample_every_ms, t_from_s, t_to_s)
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        self._track = PositionTrack()
        self._sampler.reset()

    @property
    def dropped_ticks(self) -> int:
        """Ticks descartados por llegar repetidos o desordenados."""
        return self._sampler.dropped

    def notify_event(self, event: Dict[str, Any]):
        t = self._sampler.accept(event)
        if t is not None:
            self._record_tick(t, event.get("participants") or [])

    def _record_tick(self, t: int, participants: List[Dict[str, Any]]):
        track = self._track
        index = len(track.t_ms)
        track.t_ms.append(t)

        seen = set()
        for p in participants:
            pid = participant_id(p)
            if pid is None:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in track.x:
                # Jugador visto por primera vez: rellenar los ticks anteriores.
                track.x[pid] = array("l", [_NO_POS] * index)
                track.y[pid] = array("l", [_NO_POS] * index)
                track.alive[pid] = array("b", [_ALIVE_UNKNOWN] * index)
                track.players[pid] = player_meta(p, pid)

            x, y = _NO_POS, _NO_POS
            pos = p.get("position")
            if isinstance(pos, dict):
                raw_x = pos.get("x")
                raw_y = pos.get("z") if "z" in pos else pos.get("y")
                if raw_x is not None and raw_y is not None:
                    x, y = int(raw_x), int(raw_y)
            alive = p.get("alive")
            track.x[pid].append(x)
            track.y[pid].append(y)
            track.alive[pid].append(_ALIVE_UNKNOWN if alive is None else int(bool(alive)))

        # Jugadores conocidos que faltan en este tick: mantener la alineación.
        for pid in track.x:
            if pid not in seen:
                track.x[pid].append(_NO_POS)
                track.y[pid].append(_NO_POS)
                track.alive[pid].append(_ALIVE_UNKNOWN)

    def get_track(self) -> PositionTrack:
        """Devuelve las trayectorias registradas."""
        return self._track

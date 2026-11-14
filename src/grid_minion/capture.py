"""Captura compacta de una partida: posiciones + registro de eventos.

Una `GameCapture` es lo que queda de una partida después de una sola pasada
por su Riot LiveStats (~100 MB): las trayectorias de los jugadores y un
registro ligero de eventos, unos cientos de KB en total. Es la base sobre la
que se calculan los paquetes de scouting (nivel 1, pathing de jungla,
openings tras recall) sin volver a descargar nada de GRID.

    capture = capture_game(client, "2922522", 1)
    capture.position_at(2, 90)                 # dónde estaba el jugador 2 en 1:30
    capture.get_events("monster_kill", pid=2)  # sus campamentos
    capture.save("2922522_g1.capture.json.gz")
"""
import gzip
import json
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, List, Optional, Union

from .exceptions import GridDataError
from .observers.game_events import GameEventsObserver, filter_events
from .observers.positions import PositionObserver, PositionTrack
from .observers.processor import GameEventProcessor
from .observers.stats_timeline import (
    STATS_GROUPS, ColumnTimeline, CooldownsObserver, StatsTimelineObserver,
)

CAPTURE_SCHEMA = "grid-minion/capture/v1"

# Secciones posibles de una captura y las que se incluyen por defecto.
SECTIONS = ("positions", "events", "stats", "cooldowns")
DEFAULT_SECTIONS = ("positions", "events")


class GameCapture:
    """Posiciones y eventos de una partida, con consultas y exportación."""

    def __init__(
        self,
        positions: Optional[PositionTrack] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        players: Optional[Dict[int, Dict[str, Any]]] = None,
        game: Optional[Dict[str, Any]] = None,
        stats: Optional[ColumnTimeline] = None,
        cooldowns: Optional[ColumnTimeline] = None,
    ):
        self.positions = positions
        self.events = events
        self.stats = stats
        self.cooldowns = cooldowns
        self.players: Dict[int, Dict[str, Any]] = dict(players or {})
        self.game: Dict[str, Any] = dict(game or {})

    # ---------- construcción ----------

    @classmethod
    def from_observers(
        cls,
        positions: Optional[PositionObserver] = None,
        events: Optional[GameEventsObserver] = None,
        series_id: Optional[str] = None,
        game_number: Optional[int] = None,
        stats: Optional[StatsTimelineObserver] = None,
        cooldowns: Optional[CooldownsObserver] = None,
    ) -> "GameCapture":
        """Reúne lo recogido por los observers tras procesar la partida."""
        track = positions.get_track() if positions is not None else None
        event_list = events.get_events() if events is not None else None

        players: Dict[int, Dict[str, Any]] = {}
        if events is not None:
            players.update(events.players)
        if track is not None:
            for pid, meta in track.players.items():
                merged = dict(players.get(pid, {}))
                merged.update({k: v for k, v in meta.items() if v is not None})
                players[pid] = merged

        duration = None
        if event_list:
            end = next((e for e in reversed(event_list) if e["kind"] == "game_end"), None)
            duration = end["t"] if end else None
        if duration is None and track is not None and len(track):
            duration = track.duration_s
        if duration is None and event_list:
            duration = event_list[-1]["t"]
        for observer in (stats, cooldowns):
            if duration is None and observer is not None and len(observer.get_timeline()):
                duration = observer.get_timeline().t_ms[-1] / 1000

        game = {
            "series_id": str(series_id) if series_id is not None else None,
            "game_number": game_number,
            "game_version": events.game_version if events is not None else None,
            "duration_s": duration,
        }
        return cls(positions=track, events=event_list, players=players, game=game,
                   stats=stats.get_timeline() if stats is not None else None,
                   cooldowns=cooldowns.get_timeline() if cooldowns is not None else None)

    # ---------- consultas ----------

    def players_by(self, role: Optional[str] = None, side: Optional[str] = None) -> List[int]:
        """Ids de jugador que cumplen el rol y/o el lado (`"JUNGLE"`, `"BLUE"`)."""
        return sorted(
            pid for pid, meta in self.players.items()
            if (role is None or meta.get("role") == role)
            and (side is None or meta.get("side") == side)
        )

    def position_at(self, pid: int, t_s: float) -> Optional[Dict[str, Any]]:
        """Posición del jugador en `t_s` (última muestra con `t <= t_s`)."""
        self._require("positions")
        return self.positions.at(pid, t_s)

    def positions_between(self, pid: int, t0_s: float, t1_s: float) -> List[Dict[str, Any]]:
        """Tramo de trayectoria del jugador entre dos instantes."""
        self._require("positions")
        return self.positions.between(pid, t0_s, t1_s)

    def get_events(self, kind=None, pid: Optional[int] = None,
                   t0_s: Optional[float] = None, t1_s: Optional[float] = None
                   ) -> List[Dict[str, Any]]:
        """Eventos en orden, filtrables por tipo(s), jugador y ventana."""
        self._require("events")
        return filter_events(self.events, kind, pid, t0_s, t1_s)

    def _require(self, section: str):
        if getattr(self, section) is None:
            raise GridDataError(
                f"Esta captura no incluye '{section}': se creó sin esa sección.")

    # ---------- serialización ----------

    def to_dict(self) -> Dict[str, Any]:
        """Forma serializable a JSON, con versión de esquema."""
        data: Dict[str, Any] = {
            "schema": CAPTURE_SCHEMA,
            "game": dict(self.game),
            "players": {str(pid): meta for pid, meta in sorted(self.players.items())},
        }
        if self.positions is not None:
            data["positions"] = self.positions.to_dict()
        if self.events is not None:
            data["events"] = self.events
        if self.stats is not None:
            data["stats"] = self.stats.to_dict()
        if self.cooldowns is not None:
            data["cooldowns"] = self.cooldowns.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameCapture":
        schema = data.get("schema")
        if schema != CAPTURE_SCHEMA:
            raise GridDataError(
                f"Esquema de captura no soportado: {schema!r} (se esperaba {CAPTURE_SCHEMA!r}).")
        players = {int(pid): meta for pid, meta in (data.get("players") or {}).items()}
        positions = None
        if data.get("positions") is not None:
            positions = PositionTrack.from_dict(data["positions"], players=players)
        stats = ColumnTimeline.from_dict(data["stats"]) if data.get("stats") else None
        cooldowns = (ColumnTimeline.from_dict(data["cooldowns"])
                     if data.get("cooldowns") else None)
        return cls(positions=positions, events=data.get("events"),
                   players=players, game=data.get("game"),
                   stats=stats, cooldowns=cooldowns)

    def save(self, path: Union[str, Path]) -> Path:
        """Guarda la captura como JSON (comprimido si `path` acaba en `.gz`)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)
        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Union[str, Path]) -> "GameCapture":
        """Carga una captura guardada con `save()`."""
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                return cls.from_dict(json.load(fh))
        except json.JSONDecodeError as exc:
            raise GridDataError(f"Captura no válida en {path}: {exc}")


def build_capture(
    riot_livestats: Iterable[Dict[str, Any]],
    include: Collection[str] = DEFAULT_SECTIONS,
    series_id: Optional[str] = None,
    game_number: Optional[int] = None,
    stats_groups: Collection[str] = STATS_GROUPS,
    sample_every_ms: Optional[int] = None,
) -> GameCapture:
    """Construye una captura a partir de eventos de Riot LiveStats.

    `riot_livestats` puede ser una lista o un iterador en streaming
    (`grid_minion.io.iter_jsonl`, `GridRestClient.iter_riot_livestats`).

    Args:
        include: Secciones a capturar. Por defecto posiciones y eventos (lo
            que necesitan los paquetes de scouting, ~100 KB por partida).
            `"stats"` y `"cooldowns"` se piden aparte: pesan bastante más.
        stats_groups: Grupos del observer de stats (ver `StatsTimelineObserver`).
        sample_every_ms: Separación mínima entre muestras de posiciones,
            stats y cooldowns (las tres secciones comparten reloj). `None` =
            todas las del feed, ~1 por segundo.
    """
    unknown = set(include) - set(SECTIONS)
    if unknown:
        raise ValueError(f"Secciones desconocidas: {sorted(unknown)} (válidas: {SECTIONS})")
    positions = (PositionObserver(sample_every_ms=sample_every_ms)
                 if "positions" in include else None)
    events = GameEventsObserver() if "events" in include else None
    stats = (StatsTimelineObserver(groups=stats_groups, sample_every_ms=sample_every_ms)
             if "stats" in include else None)
    cooldowns = (CooldownsObserver(sample_every_ms=sample_every_ms)
                 if "cooldowns" in include else None)

    processor = GameEventProcessor()
    for observer in (positions, events, stats, cooldowns):
        if observer is not None:
            processor.attach(observer)
    processor.process_events(riot_livestats)
    return GameCapture.from_observers(positions, events, series_id, game_number,
                                      stats=stats, cooldowns=cooldowns)


def capture_game(
    client,
    series_id: str,
    game_number: int = 1,
    include: Collection[str] = DEFAULT_SECTIONS,
    stats_groups: Collection[str] = STATS_GROUPS,
    sample_every_ms: Optional[int] = None,
) -> Optional[GameCapture]:
    """Descarga en streaming el LiveStats de una partida y devuelve su captura.

    Solo se parsean los eventos que las secciones pedidas necesitan; el
    fichero nunca se carga entero en memoria.

    Returns:
        La captura, o `None` si GRID no tiene LiveStats de esa partida.
    """
    wanted = set()
    if {"positions", "stats", "cooldowns"} & set(include):
        wanted |= PositionObserver.schemas
    if "events" in include:
        wanted |= GameEventsObserver().schemas
    stream = client.iter_riot_livestats(series_id, game_number, schemas=wanted or None)
    if stream is None:
        return None
    return build_capture(stream, include=include, series_id=series_id,
                         game_number=game_number, stats_groups=stats_groups,
                         sample_every_ms=sample_every_ms)

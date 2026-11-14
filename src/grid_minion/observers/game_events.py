import logging
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional, Tuple

from .base import Observer
from .positions import player_meta, side_of

logger = logging.getLogger(__name__)

# Campamentos de jungla tal y como salen en `monster` (nombre en minúsculas).
JUNGLE_CAMPS = frozenset({
    "redcamp", "bluecamp", "krug", "raptor", "wolf", "gromp", "scuttlecrab",
})

_SPAWN_SIDES = {"order": "BLUE", "chaos": "RED"}


def _monster(event: Dict[str, Any]) -> Optional[str]:
    """Nombre de monstruo en minúsculas.

    El feed no es consistente entre esquemas: `redCamp` al morir, `RedCamp` al
    aparecer. En minúsculas (`redcamp`) coinciden.
    """
    name = event.get("monsterType") or event.get("monsterName")
    return str(name).lower() if name else None


def _kill(e):
    return {"victim": _int(e.get("victim")), "assistants": list(e.get("assistants") or []),
            "bounty": e.get("bounty"), "shutdown_bounty": e.get("shutdownBounty"),
            "killstreak": e.get("killStreakLength"), "fight_duration": e.get("fightDuration")}


def _monster_kill(e):
    return {"monster": _monster(e), "dragon_type": e.get("dragonType"),
            "assistants": list(e.get("assistants") or []),
            "in_enemy_jungle": e.get("inEnemyJungle"), "kill_type": e.get("killType")}


def _monster_spawn(e):
    return {"monster": _monster(e), "dragon_type": e.get("dragonType"),
            "camp_side": _SPAWN_SIDES.get(str(e.get("teamSide", "")).lower())}


def _building(e):
    return {"building_type": e.get("buildingType"), "lane": e.get("lane"),
            "tier": e.get("turretTier"), "owner_side": side_of(e.get("teamID")),
            "assistants": list(e.get("assistants") or [])}


def _plate(e):
    return {"lane": e.get("lane"), "owner_side": side_of(e.get("teamID")),
            "assistants": list(e.get("assistants") or [])}


def _channel(e):
    detail = {"type": e.get("channelingType"), "spell": e.get("summonerSpellName")}
    if "isInterrupted" in e:
        detail["interrupted"] = bool(e.get("isInterrupted"))
    return detail


def _item(e):
    detail = {"item_id": _int(e.get("itemID"))}
    if "goldGain" in e:
        detail["gold_gain"] = e.get("goldGain")
    if "reason" in e:
        detail["reason"] = e.get("reason")
    return detail


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# esquema RFC461 -> (kind, claves donde viene el jugador que actúa, detalle)
_SPECS: Dict[str, Tuple[str, Tuple[str, ...], Callable[[Dict[str, Any]], Dict[str, Any]]]] = {
    "champion_kill": ("kill", ("killer",), _kill),
    "champion_kill_special": ("kill_special", ("killer",), lambda e: {"type": e.get("killType")}),
    "ward_placed": ("ward_placed", ("placer",), lambda e: {"ward_type": e.get("wardType")}),
    "ward_killed": ("ward_killed", ("killer",), lambda e: {"ward_type": e.get("wardType")}),
    "epic_monster_kill": ("monster_kill", ("killer",), _monster_kill),
    "neutral_minion_spawn": ("monster_spawn", (), _monster_spawn),
    "epic_monster_spawn": ("monster_spawn", (), _monster_spawn),
    "building_destroyed": ("building_destroyed", ("lastHitter",), _building),
    "turret_plate_destroyed": ("plate_destroyed", ("lastHitter",), _plate),
    "channeling_started": ("channel_start", (), _channel),
    "channeling_ended": ("channel_end", (), _channel),
    "summoner_spell_used": ("summoner_used", (), lambda e: {
        "spell": e.get("summonerSpellName"), "slot": e.get("summonerSpellSlot")}),
    "item_purchased": ("item_purchased", (), _item),
    "item_sold": ("item_sold", (), _item),
    "item_destroyed": ("item_destroyed", (), _item),
    "item_undo": ("item_undo", (), _item),
    "champion_level_up": ("level_up", (), lambda e: {"level": e.get("level")}),
    "skill_level_up": ("skill_level_up", (), lambda e: {
        "slot": e.get("skillSlot"), "evolved": bool(e.get("evolved"))}),
    "pause_started": ("pause_start", (), lambda e: {}),
    "pause_ended": ("pause_end", (), lambda e: {}),
    "reconnect": ("reconnect", (), lambda e: {}),
    "quit": ("quit", (), lambda e: {}),
    "game_end": ("game_end", (), lambda e: {"winning_side": side_of(e.get("winningTeam"))}),
}

EVENT_KINDS = frozenset(spec[0] for spec in _SPECS.values())

_GENERIC_PID_KEYS = ("participantID", "participant", "participantId")


class GameEventsObserver(Observer):
    """
    Observador que construye un **registro ligero y ordenado de los eventos**
    de la partida en un formato común, a partir de Riot LiveStats.

    Cada evento es `{t, kind, pid, side, x, y, detail}`:
      - `t`: segundos de juego.
      - `kind`: tipo normalizado (ver `EVENT_KINDS`): `kill`, `kill_special`,
        `ward_placed`, `ward_killed`, `monster_kill`, `monster_spawn`,
        `building_destroyed`, `plate_destroyed`, `channel_start`,
        `channel_end` (recalls y TP), `summoner_used`, `item_purchased`,
        `item_sold`, `item_destroyed`, `item_undo`, `level_up`,
        `skill_level_up`, `pause_start`, `pause_end`, `reconnect`, `quit`,
        `game_end`.
      - `pid`: jugador que actúa (1-10) o `None`.
      - `side`: lado de ese jugador (`BLUE`/`RED`) o `None`.
      - `x`, `y`: posición si el evento la trae (`y` = el `z` del feed), o `None`.
      - `detail`: campos propios del tipo.

    Es la capa de eventos sobre la que se cruzan las posiciones
    (`PositionObserver`): pathing de jungla, recalls, wards. No sustituye a los
    observers de detalle (objetivos, builds...), que no cambian.

    Es opt-in: solo actúa si se engancha al procesador.
    """

    def __init__(self, kinds: Optional[Collection[str]] = None):
        """
        Args:
            kinds: Tipos de evento a registrar. `None` (default) = todos.
        """
        if kinds is not None:
            unknown = set(kinds) - EVENT_KINDS
            if unknown:
                raise ValueError(f"Tipos de evento desconocidos: {sorted(unknown)}")
        self._kinds = frozenset(kinds) if kinds is not None else EVENT_KINDS
        self._specs = {schema: spec for schema, spec in _SPECS.items()
                       if spec[0] in self._kinds}
        # `game_info` no genera evento: da los metadatos de jugadores y el parche.
        self.schemas = frozenset(self._specs) | {"game_info"}
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        self._events: List[Dict[str, Any]] = []
        self.players: Dict[int, Dict[str, Any]] = {}
        self.game_version: Optional[str] = None

    def notify_event(self, event: Dict[str, Any]):
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "game_info":
            self._read_game_info(event)
            return
        spec = self._specs.get(schema)
        if spec is None:
            return
        kind, pid_keys, build_detail = spec
        try:
            pid = self._pid(event, pid_keys)
            x = y = None
            pos = event.get("position")
            if isinstance(pos, dict):
                x = pos.get("x")
                y = pos.get("z") if "z" in pos else pos.get("y")
            actor_team = event.get("killerTeamID") if "killerTeamID" in event else None
            self._events.append({
                "t": (event.get("gameTime") or 0) / 1000,
                "kind": kind,
                "pid": pid,
                "side": side_of(actor_team, pid),
                "x": x,
                "y": y,
                "detail": build_detail(event),
            })
        except Exception:
            logger.warning("Error procesando %s en GameEventsObserver", schema, exc_info=True)

    def _read_game_info(self, event: Dict[str, Any]):
        version = event.get("gameVersion")
        if version:
            self.game_version = str(version)
        for p in event.get("participants") or []:
            pid = _int(p.get("participantID", p.get("participantId")))
            if pid is not None:
                self.players[pid] = player_meta(p, pid)

    @staticmethod
    def _pid(event: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
        for key in tuple(keys) + _GENERIC_PID_KEYS:
            if event.get(key) is not None:
                pid = _int(event[key])
                # 0 o fuera de 1-10: lo hizo un minion/torre, no un jugador.
                return pid if pid is not None and 1 <= pid <= 10 else None
        return None

    def get_events(self, kind: Optional[str] = None, pid: Optional[int] = None,
                   t0_s: Optional[float] = None, t1_s: Optional[float] = None
                   ) -> List[Dict[str, Any]]:
        """Eventos registrados, en orden, con filtros opcionales.

        Args:
            kind: Un tipo (`"kill"`) o varios (`("ward_placed", "ward_killed")`).
            pid: Solo eventos de ese jugador.
            t0_s / t1_s: Ventana de tiempo de juego, ambos extremos incluidos.
        """
        return filter_events(self._events, kind, pid, t0_s, t1_s)


def filter_events(events: List[Dict[str, Any]], kind=None, pid=None,
                  t0_s=None, t1_s=None) -> List[Dict[str, Any]]:
    """Filtra una lista de eventos por tipo, jugador y ventana de tiempo."""
    kinds = None
    if kind is not None:
        kinds = {kind} if isinstance(kind, str) else set(kind)
    return [
        e for e in events
        if (kinds is None or e["kind"] in kinds)
        and (pid is None or e["pid"] == pid)
        and (t0_s is None or e["t"] >= t0_s)
        and (t1_s is None or e["t"] <= t1_s)
    ]

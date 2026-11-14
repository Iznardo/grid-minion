import bisect
import logging
import math
from array import array
from typing import Any, Collection, Dict, List, Optional, Tuple

from .base import Observer
from ._ticks import TickSampler, participant_id

logger = logging.getLogger(__name__)

_NAN = float("nan")

# ---- campos por grupo: clave de salida -> campo del participante en el feed ----

ECONOMY_FIELDS = {
    "gold_total": "totalGold",
    "gold_current": "currentGold",
    "xp": "XP",
    "level": "level",
    "respawn_timer": "respawnTimer",
    "shutdown_value": "shutdownValue",
}

CHAMPION_STATS_FIELDS = {
    "ad": "attackDamage",
    "ap": "abilityPower",
    "armor": "armor",
    "mr": "magicResist",
    "attack_speed": "attackSpeed",
    "hp": "health",
    "hp_max": "healthMax",
    "hp_regen": "healthRegen",
    "resource": "primaryAbilityResource",
    "resource_max": "primaryAbilityResourceMax",
    "armor_pen": "armorPenetration",
    "armor_pen_pct": "armorPenetrationPercent",
    "magic_pen": "magicPenetration",
    "magic_pen_pct": "magicPenetrationPercent",
    "lifesteal": "lifeSteal",
    "spell_vamp": "spellVamp",
    "cdr": "cooldownReduction",
    "cc_reduction": "ccReduction",
}

# Contadores ACUMULADOS desde el inicio de la partida (lista `stats` del feed).
COMBAT_TOTALS_FIELDS = {
    "damage_to_champions": "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS",
    "magic_damage_to_champions": "MAGIC_DAMAGE_DEALT_TO_CHAMPIONS",
    "physical_damage_to_champions": "PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS",
    "true_damage_to_champions": "TRUE_DAMAGE_DEALT_TO_CHAMPIONS",
    "damage_taken": "TOTAL_DAMAGE_TAKEN",
    "damage_taken_from_champions": "TOTAL_DAMAGE_TAKEN_FROM_CHAMPIONS",
    "magic_damage_taken": "MAGIC_DAMAGE_TAKEN",
    "physical_damage_taken": "PHYSICAL_DAMAGE_TAKEN",
    "true_damage_taken": "TRUE_DAMAGE_TAKEN",
    "damage_self_mitigated": "TOTAL_DAMAGE_SELF_MITIGATED",
    "damage_shielded_on_teammates": "TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES",
    "heal_on_teammates": "TOTAL_HEAL_ON_TEAMMATES",
    "damage_to_turrets": "TOTAL_DAMAGE_DEALT_TO_TURRETS",
    "damage_to_buildings": "TOTAL_DAMAGE_DEALT_TO_BUILDINGS",
    "damage_to_objectives": "TOTAL_DAMAGE_DEALT_TO_OBJECTIVES",
    "damage_to_epic_monsters": "TOTAL_DAMAGE_DEALT_TO_EPIC_MONSTERS",
    "damage_taken_from_buildings": "TOTAL_DAMAGE_TAKEN_FROM_BUILDINGS",
    "time_ccing_others": "TIME_CCING_OTHERS",
    "cc_dealt_to_champions": "TOTAL_TIME_CROWD_CONTROL_DEALT_TO_CHAMPIONS",
    "kills": "CHAMPIONS_KILLED",
    "deaths": "NUM_DEATHS",
    "assists": "ASSISTS",
    "vision_score": "VISION_SCORE",
}

TEAM_FIELDS = {
    "gold": "totalGold",
    "kills": "championsKills",
    "deaths": "deaths",
    "assists": "assists",
    "towers": "towerKills",
    "inhibitors": "inhibKills",
    "dragons": "dragonKills",
    "barons": "baronKills",
}

COOLDOWN_FIELDS = {
    "ultimate": "ultimateCooldownRemaining",
    "ability1": "ability1CooldownRemaining",
    "ability2": "ability2CooldownRemaining",
    "ability3": "ability3CooldownRemaining",
    "ability4": "ability4CooldownRemaining",
    "summoner1": "summonerSpell1CooldownRemaining",
    "summoner2": "summonerSpell2CooldownRemaining",
}

STATS_GROUPS = ("economy", "champion_stats", "combat_totals", "teams")


def _num(value: Any) -> float:
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _out(value: float) -> Optional[float]:
    """Valor de columna -> salida: `None` si falta, entero si lo es.

    Los decimales se redondean a 2: el feed da los contadores como float32
    (`648.3837890625`) y esa falsa precisión multiplica el tamaño exportado.
    """
    if math.isnan(value):
        return None
    return int(value) if float(value).is_integer() else round(value, 2)


class ColumnTimeline:
    """
    Series por jugador (y por equipo) en columnas, sobre un reloj común.

    `t_ms[i]` es el instante de la muestra `i` de todas las columnas. Con las
    mismas opciones de muestreo, coincide con el reloj de `PositionTrack`.
    Un valor ausente en el feed es `None`, nunca `0`.
    """

    def __init__(self, fields: Collection[str] = (), team_fields: Collection[str] = ()):
        self.fields: Tuple[str, ...] = tuple(fields)
        self.team_fields: Tuple[str, ...] = tuple(team_fields)
        self.t_ms = array("l")
        self.columns: Dict[int, Dict[str, array]] = {}
        self.teams: Dict[int, Dict[str, array]] = {}
        # pid -> [[t_ms, [item_id, ...]], ...]: solo cuando cambia el inventario.
        self.items: Dict[int, List[List[Any]]] = {}
        # Datos sueltos que acompañan a las series (p. ej. nombres de hechizos).
        self.meta: Dict[str, Any] = {}

    def __len__(self) -> int:
        return len(self.t_ms)

    @property
    def pids(self) -> List[int]:
        return sorted(self.columns)

    def _index_at(self, t_s: float) -> int:
        return bisect.bisect_right(self.t_ms, int(round(t_s * 1000))) - 1

    def at(self, pid: int, t_s: float) -> Optional[Dict[str, Any]]:
        """Valores del jugador en `t_s` (última muestra con `t <= t_s`)."""
        if pid not in self.columns:
            return None
        i = self._index_at(t_s)
        if i < 0:
            return None
        row: Dict[str, Any] = {"t": self.t_ms[i] / 1000}
        for field, column in self.columns[pid].items():
            row[field] = _out(column[i])
        return row

    def value_at(self, pid: int, field: str, t_s: float) -> Optional[float]:
        """Un solo campo del jugador en `t_s`."""
        column = self.columns.get(pid, {}).get(field)
        i = self._index_at(t_s)
        return _out(column[i]) if column is not None and i >= 0 else None

    def values(self, pid: int, field: str) -> List[Optional[float]]:
        """Serie completa de un campo, alineada con `t_ms`."""
        return [_out(v) for v in self.columns[pid][field]]

    def delta(self, pid: int, field: str, t0_s: float, t1_s: float) -> Optional[float]:
        """Variación de un campo entre dos instantes.

        Pensado para los contadores acumulados: `delta(pid,
        "damage_to_champions", 600, 660)` es el daño hecho en ese minuto.
        Antes de la primera muestra el contador vale 0.
        """
        end = self.value_at(pid, field, t1_s)
        if end is None:
            return None
        start = self.value_at(pid, field, t0_s)
        return end - (start or 0)

    def team_at(self, team_id: int, t_s: float) -> Optional[Dict[str, Any]]:
        """Totales del equipo (100/200) en `t_s`."""
        if team_id not in self.teams:
            return None
        i = self._index_at(t_s)
        if i < 0:
            return None
        row: Dict[str, Any] = {"t": self.t_ms[i] / 1000}
        for field, column in self.teams[team_id].items():
            row[field] = _out(column[i])
        return row

    def items_at(self, pid: int, t_s: float) -> Optional[List[int]]:
        """Inventario del jugador en `t_s`."""
        changes = self.items.get(pid)
        if not changes:
            return None
        i = bisect.bisect_right([c[0] for c in changes], int(round(t_s * 1000))) - 1
        return list(changes[i][1]) if i >= 0 else None

    # ---------- serialización ----------

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "t_ms": list(self.t_ms),
            "fields": list(self.fields),
            "players": {
                str(pid): {field: [_out(v) for v in column]
                           for field, column in self.columns[pid].items()}
                for pid in self.pids
            },
        }
        if self.teams:
            data["team_fields"] = list(self.team_fields)
            data["teams"] = {
                str(team): {field: [_out(v) for v in column] for field, column in cols.items()}
                for team, cols in sorted(self.teams.items())
            }
        if self.items:
            data["items"] = {str(pid): changes for pid, changes in sorted(self.items.items())}
        if self.meta:
            data["meta"] = self.meta
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnTimeline":
        timeline = cls(data.get("fields", ()), data.get("team_fields", ()))
        timeline.t_ms = array("l", data.get("t_ms", []))

        def column(values):
            return array("f", (_NAN if v is None else float(v) for v in values))

        for pid, cols in (data.get("players") or {}).items():
            timeline.columns[int(pid)] = {field: column(v) for field, v in cols.items()}
        for team, cols in (data.get("teams") or {}).items():
            timeline.teams[int(team)] = {field: column(v) for field, v in cols.items()}
        for pid, changes in (data.get("items") or {}).items():
            timeline.items[int(pid)] = [[int(t), list(ids)] for t, ids in changes]
        timeline.meta = dict(data.get("meta") or {})
        return timeline


class _ColumnsObserver(Observer):
    """Base de los observers que guardan columnas por tick de `stats_update`."""

    schemas = frozenset({"stats_update"})

    def __init__(self, sample_every_ms: Optional[int] = None,
                 t_from_s: Optional[float] = None, t_to_s: Optional[float] = None):
        self._sampler = TickSampler(sample_every_ms, t_from_s, t_to_s)
        self.reset()

    # A definir por cada observer:
    def _player_fields(self) -> Tuple[str, ...]:
        raise NotImplementedError

    def _team_fields(self) -> Tuple[str, ...]:
        return ()

    def _extract(self, participant: Dict[str, Any]) -> Dict[str, float]:
        raise NotImplementedError

    def _on_participant(self, pid: int, t: int, participant: Dict[str, Any]):
        """Gancho para datos que no son columnas (inventario, nombres)."""

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        self._timeline = ColumnTimeline(self._player_fields(), self._team_fields())
        self._sampler.reset()

    @property
    def dropped_ticks(self) -> int:
        """Ticks descartados por llegar repetidos o desordenados."""
        return self._sampler.dropped

    def notify_event(self, event: Dict[str, Any]):
        t = self._sampler.accept(event)
        if t is None:
            return
        try:
            self._record_tick(t, event)
        except Exception:
            logger.warning("Error procesando stats_update en %s",
                           type(self).__name__, exc_info=True)

    def _record_tick(self, t: int, event: Dict[str, Any]):
        timeline = self._timeline
        index = len(timeline.t_ms)
        timeline.t_ms.append(t)

        seen = set()
        for p in event.get("participants") or []:
            pid = participant_id(p)
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            if pid not in timeline.columns:
                timeline.columns[pid] = {
                    field: array("f", [_NAN] * index) for field in timeline.fields}
            values = self._extract(p)
            for field, column in timeline.columns[pid].items():
                column.append(values.get(field, _NAN))
            self._on_participant(pid, t, p)
        for pid, columns in timeline.columns.items():
            if pid not in seen:
                for column in columns.values():
                    column.append(_NAN)

        if timeline.team_fields:
            seen_teams = set()
            for team in event.get("teams") or []:
                team_id = team.get("teamID", team.get("teamId"))
                if team_id is None:
                    continue
                team_id = int(team_id)
                seen_teams.add(team_id)
                if team_id not in timeline.teams:
                    timeline.teams[team_id] = {
                        field: array("f", [_NAN] * index) for field in timeline.team_fields}
                for field, column in timeline.teams[team_id].items():
                    column.append(_num(team.get(TEAM_FIELDS[field])))
            for team_id, columns in timeline.teams.items():
                if team_id not in seen_teams:
                    for column in columns.values():
                        column.append(_NAN)

    def get_timeline(self) -> ColumnTimeline:
        """Devuelve las series registradas."""
        return self._timeline


class StatsTimelineObserver(_ColumnsObserver):
    """
    Observador de **stats de jugadores y equipos** a lo largo de la partida,
    por grupos que se eligen al crearlo:

      - `economy`: oro total y disponible, XP, nivel, CS, tiempo de
        reaparición, valor de shutdown e inventario (solo cuando cambia).
      - `champion_stats`: stats de campeón ya sumadas con items y niveles
        (AD, AP, armadura, vida, maná, penetraciones...).
      - `combat_totals`: contadores **acumulados** de daño, CC, kills y
        visión. Para sacar el daño de un tramo, `ColumnTimeline.delta()`.
      - `teams`: oro, kills, torres, inhibidores, dragones y barones por equipo.

    Comparte reloj con `PositionObserver` y `CooldownsObserver`: con las
    mismas opciones de muestreo, la muestra `i` de los tres es del mismo
    instante. No incluye posiciones ni cooldowns.

    Es opt-in: solo actúa si se engancha al procesador.
    """

    def __init__(self, groups: Collection[str] = STATS_GROUPS,
                 sample_every_ms: Optional[int] = None,
                 t_from_s: Optional[float] = None, t_to_s: Optional[float] = None):
        unknown = set(groups) - set(STATS_GROUPS)
        if unknown:
            raise ValueError(f"Grupos desconocidos: {sorted(unknown)} (válidos: {STATS_GROUPS})")
        self.groups = tuple(g for g in STATS_GROUPS if g in groups)
        self._direct: Dict[str, str] = {}
        if "economy" in self.groups:
            self._direct.update(ECONOMY_FIELDS)
        if "champion_stats" in self.groups:
            self._direct.update(CHAMPION_STATS_FIELDS)
        self._from_stats: Dict[str, str] = (
            dict(COMBAT_TOTALS_FIELDS) if "combat_totals" in self.groups else {})
        self._last_items: Dict[int, List[int]] = {}
        super().__init__(sample_every_ms, t_from_s, t_to_s)

    def reset(self):
        super().reset()
        self._last_items = {}

    def _player_fields(self) -> Tuple[str, ...]:
        fields = list(self._direct)
        if "economy" in self.groups:
            fields.append("cs")
        fields.extend(self._from_stats)
        return tuple(fields)

    def _team_fields(self) -> Tuple[str, ...]:
        return tuple(TEAM_FIELDS) if "teams" in self.groups else ()

    def _extract(self, participant: Dict[str, Any]) -> Dict[str, float]:
        values = {out: _num(participant.get(src)) for out, src in self._direct.items()}
        if "economy" in self.groups or self._from_stats:
            stats_list = participant.get("stats")
            stats = ({s.get("name"): s.get("value") for s in stats_list}
                     if isinstance(stats_list, list) else {})
            if "economy" in self.groups:
                if "MINIONS_KILLED" in stats or "NEUTRAL_MINIONS_KILLED" in stats:
                    values["cs"] = float(int(stats.get("MINIONS_KILLED") or 0)
                                         + int(stats.get("NEUTRAL_MINIONS_KILLED") or 0))
            for out, src in self._from_stats.items():
                values[out] = _num(stats.get(src))
        return values

    def _on_participant(self, pid: int, t: int, participant: Dict[str, Any]):
        if "economy" not in self.groups:
            return
        raw = participant.get("items")
        if not isinstance(raw, list):
            return
        items = [int(i.get("itemID")) if isinstance(i, dict) else int(i)
                 for i in raw if (i.get("itemID") if isinstance(i, dict) else i)]
        if self._last_items.get(pid) != items:
            self._last_items[pid] = items
            self._timeline.items.setdefault(pid, []).append([t, items])


class CooldownsObserver(_ColumnsObserver):
    """
    Observador de **cooldowns** por jugador: segundos restantes de la
    definitiva, las cuatro habilidades y los dos hechizos de invocador.

    Es señal directa del feed (`0` = disponible), no una estimación: cubre
    resets y reducciones condicionales. Comparte reloj con `PositionObserver`
    y `StatsTimelineObserver`.

    Es opt-in: solo actúa si se engancha al procesador.
    """

    def reset(self):
        super().reset()
        # {"<pid>": {"summoner1": nombre, "summoner2": nombre, "ultimate": nombre}}
        # (el último visto: el nombre de un hechizo cambia si se mejora).
        self.spell_names: Dict[str, Dict[str, Optional[str]]] = {}
        self._timeline.meta["spell_names"] = self.spell_names

    def _player_fields(self) -> Tuple[str, ...]:
        return tuple(COOLDOWN_FIELDS)

    def _extract(self, participant: Dict[str, Any]) -> Dict[str, float]:
        return {out: _num(participant.get(src)) for out, src in COOLDOWN_FIELDS.items()}

    def _on_participant(self, pid: int, t: int, participant: Dict[str, Any]):
        self.spell_names[str(pid)] = {
            "summoner1": participant.get("summonerSpell1Name"),
            "summoner2": participant.get("summonerSpell2Name"),
            "ultimate": participant.get("ultimateName"),
        }

    def is_up(self, pid: int, field: str, t_s: float) -> Optional[bool]:
        """¿Está disponible `field` (`ultimate`, `summoner1`...) en `t_s`?

        `None` si no hay dato (jugador desconocido o antes de la primera muestra).
        """
        value = self._timeline.value_at(pid, field, t_s)
        return None if value is None else value <= 0

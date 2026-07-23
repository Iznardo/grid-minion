import bisect
import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class PlayerTimelineObserver(Observer):
    """
    Observador que reconstruye el **estado continuo por jugador** a máxima
    frecuencia, a partir del evento `stats_update` de la timeline de Riot
    (livestats RFC461).

    Todo el estado por-jugador-por-instante vive en un único evento `stats_update`
    (posición, oro, nivel, items, stats de campeón ya computadas con items+niveles,
    y cooldowns de ultimate/habilidades/summoners). Por eso un solo observer lee ese
    evento una vez y guarda, por `participantID`, una serie temporal de snapshots.
    Los getters proyectan vistas (posiciones, economía, stats, disponibilidad) sin
    duplicar el almacenamiento.

    Nomenclatura RFC461 verificada contra el feed real de GRID (serie `2930129`):
      - id de jugador: `participantID`.
      - posición: `position.{x,z}` (se mapea `z → y`, convención del repo).
      - vida/estado: `alive`, `respawnTimer`.
      - progresión: `level`, `XP`, `totalGold`, `currentGold`, `items`; CS en la lista
        `stats` (`MINIONS_KILLED` + `NEUTRAL_MINIONS_KILLED`, jungla incluida).
      - stats de combate ya sumadas: `attackDamage`, `abilityPower`, `armor`,
        `magicResist`, `attackSpeed`, `healthMax`, `health`, `healthRegen`,
        `armorPenetration`, `magicPenetration`, `lifeSteal`, `spellVamp`,
        `cooldownReduction`, `ccReduction`.
      - disponibilidad **directa**: `ultimateCooldownRemaining`,
        `ability{1..4}CooldownRemaining`, `summonerSpell{1,2}CooldownRemaining`
        (`== 0` ⇒ disponible). No se estima nada.

    Semántica de consulta puntual: `snapshot_at(pid, t)` devuelve el último snapshot
    con `t_s <= t` (mismo criterio que `MidGameStatsObserver`).
    """

    # Campos de stats de combate del participante → clave de salida (snake_case).
    _COMBAT_STATS = {
        "attackDamage": "ad",
        "abilityPower": "ap",
        "armor": "armor",
        "magicResist": "mr",
        "attackSpeed": "attack_speed",
        "health": "hp",
        "healthMax": "hp_max",
        "healthRegen": "hp_regen",
        "armorPenetration": "armor_pen",
        "armorPenetrationPercent": "armor_pen_pct",
        "magicPenetration": "magic_pen",
        "magicPenetrationPercent": "magic_pen_pct",
        "lifeSteal": "lifesteal",
        "spellVamp": "spell_vamp",
        "cooldownReduction": "cdr",
        "ccReduction": "cc_reduction",
    }

    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        # pid -> [snapshot, ...] en orden cronológico
        self._series: Dict[int, List[Dict[str, Any]]] = {}
        # pid -> [t_s, ...] paralelo a _series, para bisect en snapshot_at
        self._times: Dict[int, List[float]] = {}
        # teamID -> [{"t", "gold"}] (oro por equipo)
        self._team_gold: Dict[int, List[Dict[str, Any]]] = {}

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos `stats_update` de la timeline."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema != "stats_update":
            return
        try:
            t_s = int(event.get("gameTime", 0)) / 1000
            for p in event.get("participants", []):
                self._record_participant(p, t_s)
            for team in event.get("teams", []):
                self._record_team_gold(team, t_s)
        except Exception:
            logger.warning("Error procesando stats_update en PlayerTimelineObserver",
                           exc_info=True)

    def _record_participant(self, p: Dict[str, Any], t_s: float):
        pid = p.get("participantID")
        if pid is None:
            pid = p.get("participantId")
        if pid is None:
            return
        pid = int(pid)

        snapshot = {
            "t": t_s,
            "position": self._extract_position(p),
            "alive": p.get("alive"),
            "respawn_timer": self._num(p.get("respawnTimer")),
            "gold_total": self._int(p.get("totalGold")),
            "gold_current": self._int(p.get("currentGold")),
            "xp": self._int(p.get("XP")),
            "level": self._int(p.get("level")),
            "cs": self._extract_cs(p),
            "items": list(p.get("items", []) or []),
            "champion_stats": self._extract_combat_stats(p),
            "cooldowns": self._extract_cooldowns(p),
        }
        self._series.setdefault(pid, []).append(snapshot)
        self._times.setdefault(pid, []).append(t_s)

    def _record_team_gold(self, team: Dict[str, Any], t_s: float):
        tid = team.get("teamID")
        if tid is None:
            tid = team.get("teamId")
        gold = team.get("totalGold")
        if tid is None or gold is None:
            return
        self._team_gold.setdefault(int(tid), []).append({"t": t_s, "gold": self._int(gold)})

    # ---------- extractores ----------

    @staticmethod
    def _extract_position(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raw = p.get("position")
        if not isinstance(raw, dict):
            return None
        x = raw.get("x")
        y = raw.get("z") if "z" in raw else raw.get("y")
        if x is None and y is None:
            return None
        return {"x": x, "y": y}

    @staticmethod
    def _extract_cs(p: Dict[str, Any]) -> Optional[int]:
        """CS = MINIONS_KILLED + NEUTRAL_MINIONS_KILLED desde la lista `stats`."""
        stats_list = p.get("stats")
        if not isinstance(stats_list, list):
            return None
        stats = {s.get("name"): s.get("value", 0) for s in stats_list}
        if "MINIONS_KILLED" not in stats and "NEUTRAL_MINIONS_KILLED" not in stats:
            return None
        minions = stats.get("MINIONS_KILLED", 0) or 0
        neutrals = stats.get("NEUTRAL_MINIONS_KILLED", 0) or 0
        return int(minions) + int(neutrals)

    def _extract_combat_stats(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {out: self._num(p.get(src)) for src, out in self._COMBAT_STATS.items()}

    def _extract_cooldowns(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ultimate": self._num(p.get("ultimateCooldownRemaining")),
            "abilities": {
                slot: self._num(p.get(f"ability{slot}CooldownRemaining"))
                for slot in (1, 2, 3, 4)
            },
            "summoner1": self._num(p.get("summonerSpell1CooldownRemaining")),
            "summoner2": self._num(p.get("summonerSpell2CooldownRemaining")),
        }

    @staticmethod
    def _int(value: Any) -> Optional[int]:
        return int(value) if value is not None else None

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        """Coerción numérica tolerante (el feed mezcla int y float)."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ---------- getters / vistas ----------

    def get_players(self) -> List[int]:
        """Ids de jugador (1-10) con serie registrada, ordenados."""
        return sorted(self._series)

    def get_series(self, pid: int) -> List[Dict[str, Any]]:
        """Serie completa de snapshots de un jugador (referencia, no copiar)."""
        return self._series.get(pid, [])

    def get_positions(self, pid: int) -> List[Dict[str, Any]]:
        """Trayectoria del jugador: `[{t, x, y}]` (omite ticks sin posición)."""
        out = []
        for s in self._series.get(pid, []):
            pos = s["position"]
            if pos is not None:
                out.append({"t": s["t"], "x": pos["x"], "y": pos["y"]})
        return out

    def get_all_positions(self) -> Dict[int, List[Dict[str, Any]]]:
        """Trayectorias de todos los jugadores: `{pid: [{t, x, y}]}`."""
        return {pid: self.get_positions(pid) for pid in self._series}

    def get_economy(self, pid: int) -> List[Dict[str, Any]]:
        """Serie económica/progresión: `[{t, gold_total, gold_current, xp, level, cs, items}]`."""
        return [
            {
                "t": s["t"], "gold_total": s["gold_total"],
                "gold_current": s["gold_current"], "xp": s["xp"],
                "level": s["level"], "cs": s["cs"], "items": s["items"],
            }
            for s in self._series.get(pid, [])
        ]

    def get_champion_stats(self, pid: int) -> List[Dict[str, Any]]:
        """Serie de stats de combate (power spikes): `[{t, ...stats}]`."""
        return [{"t": s["t"], **s["champion_stats"]} for s in self._series.get(pid, [])]

    def get_ability_availability(self, pid: int) -> List[Dict[str, Any]]:
        """Serie de cooldowns restantes: `[{t, ...cooldowns}]`."""
        return [{"t": s["t"], **s["cooldowns"]} for s in self._series.get(pid, [])]

    def get_team_gold_series(self) -> Dict[int, List[Dict[str, Any]]]:
        """Oro por equipo en el tiempo: `{teamID: [{t, gold}]}`."""
        return self._team_gold

    def snapshot_at(self, pid: int, t_s: float) -> Optional[Dict[str, Any]]:
        """Último snapshot del jugador con `t <= t_s`. `None` si no hay ninguno antes."""
        times = self._times.get(pid)
        if not times:
            return None
        idx = bisect.bisect_right(times, t_s) - 1
        if idx < 0:
            return None
        return self._series[pid][idx]

    def is_ultimate_up(self, pid: int, t_s: float) -> Optional[bool]:
        """¿Está la ultimate disponible en `t_s`? Señal directa (`cooldown == 0`).

        `None` si no hay dato (jugador desconocido o antes del primer snapshot).
        """
        snap = self.snapshot_at(pid, t_s)
        if snap is None:
            return None
        cd = snap["cooldowns"]["ultimate"]
        return cd is not None and cd <= 0

    def is_summoner_up(self, pid: int, slot: int, t_s: float) -> Optional[bool]:
        """¿Está el summoner (slot 1 o 2) disponible en `t_s`? (`cooldown == 0`)."""
        snap = self.snapshot_at(pid, t_s)
        if snap is None:
            return None
        cd = snap["cooldowns"].get(f"summoner{slot}")
        return cd is not None and cd <= 0

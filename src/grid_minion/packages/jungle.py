"""Paquete de pathing de jungla: trayectoria del jungla cruzada con eventos."""
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

from .. import spatial
from ..capture import GameCapture
from ..observers.game_events import JUNGLE_CAMPS
from ._common import (
    annotated_path, level_at, player_info, require_sections, runs, slim_event,
    zone_at, zone_sequence,
)

SCHEMA = "grid-minion/package/jungle_pathing/v1"

_ROLE_LANE = {"TOP": "top", "MID": "mid", "ADC": "bot", "SUPPORT": "bot"}
_OWN_KINDS = (
    "monster_kill", "ward_placed", "ward_killed", "kill", "kill_special",
    "summoner_used", "channel_start", "channel_end", "level_up", "item_purchased",
)
# Hueco máximo (s) para unir dos tramos del mismo tipo.
_GAP_S = 3


def jungle_pathing(
    capture: GameCapture,
    until: Union[str, float] = "first_recall",
    max_s: float = 600,
    gank_radius: float = 1500,
    min_near_s: float = 2,
    min_lane_s: float = 6,
    lane_enemy_radius: float = 3500,
    min_invade_s: float = 3,
    outcome_grace_s: float = 5,
    ward_radius: float = 2500,
) -> Dict[str, Any]:
    """
    Recorrido de cada jungla cruzado, segundo a segundo, con los eventos.

    Guarda el cruce crudo (trayectoria con zona y distancias, campamentos y
    línea temporal de eventos) y, encima, etiquetas derivadas: candidatos a
    gank e invade. Las etiquetas son **candidatos**, no veredictos: cada una
    lleva los datos que la justifican, para poder cambiar los umbrales
    después sin reprocesar la partida.

    Args:
        capture: Captura con posiciones y eventos.
        until: Fin de la ventana de cada jungla.
            `"first_recall"` (default): hasta que completa su primer recall
            o muere, lo que ocurra antes, con tope `max_s`.
            Un número: hasta ese segundo de juego.
        max_s: Tope de la ventana con `until="first_recall"`.
        gank_radius: Distancia a un rival de línea por debajo de la cual el
            jungla "está encima" (unidades de mapa).
        min_near_s: Segundos seguidos dentro de `gank_radius` para contar.
        min_lane_s: Segundos seguidos dentro de una línea para contar aunque
            no llegue a estar encima de un rival (cruzar la línea central
            lleva ~4 s).
        lane_enemy_radius: Un tramo en línea solo cuenta si en algún momento
            hay un rival de línea a menos de esta distancia. Descarta salir
            de base por la franja de una línea, sin nadie cerca.
        min_invade_s: Segundos seguidos en jungla enemiga para contar.
        outcome_grace_s: Margen tras salir de un tramo en el que una kill o
            un summoner todavía se atribuyen a ese tramo.
        ward_radius: Un ward enemigo colocado a menos de esta distancia del
            jungla entra en su línea temporal.

    Returns:
        ```
        {schema, game, params, junglers: [{
            pid, side, role, champion, name, enemy_jungler_pid,
            window: {t0, t1, reason},        # first_recall | first_death | max_s | fixed
            start_camp, start_side, first_clear: [monstruo, ...],
            camps: [{order, t, monster, x, y, zone, in_enemy_jungle}],
            path: [{t, x, y, alive, zone, enemy_jungler_d,
                    nearest_enemy_pid, nearest_enemy_d}],
            timeline: [evento + {relation, jungler_pos}],
            levels: [{t, level}],
            first_recall: {t_start, t_end, level} | None,
            river_crossings: [{t, zone}],
            ganks: [{lane, t_enter, t_exit, duration, in_lane_s, near_enemy_s,
                     closest: {pid, role, champion, distance, t},
                     result, kills, assists, deaths, enemy_summoners}],
            invades: [{zone, t_enter, t_exit, duration, camps_taken,
                       wards_placed, min_enemy_jungler_d,
                       result, kills, assists, deaths}],
        }]}
        ```
        `relation` en la línea temporal: `self` (lo hace el jungla),
        `victim`/`assist` (kills en las que participa) o `enemy_ward_nearby`.
    """
    require_sections(capture, "positions", "events")
    params = {
        "until": until, "max_s": max_s, "gank_radius": gank_radius,
        "min_near_s": min_near_s, "min_lane_s": min_lane_s,
        "lane_enemy_radius": lane_enemy_radius,
        "min_invade_s": min_invade_s, "outcome_grace_s": outcome_grace_s,
        "ward_radius": ward_radius,
    }
    return {
        "schema": SCHEMA,
        "game": dict(capture.game),
        "params": params,
        "junglers": [_one_jungler(capture, pid, params)
                     for pid in capture.players_by(role="JUNGLE")],
    }


def _one_jungler(capture: GameCapture, pid: int, params: Dict[str, Any]) -> Dict[str, Any]:
    info = player_info(capture, pid)
    side = info["side"]
    enemies = [p for p, meta in capture.players.items()
               if meta.get("side") not in (None, side)]
    enemy_junglers = [p for p in enemies if capture.players[p].get("role") == "JUNGLE"]
    enemy_jungler = enemy_junglers[0] if enemy_junglers else None
    enemy_laners = [p for p in enemies if p != enemy_jungler]

    recall = _first_recall(capture, pid, params["max_s"])
    t1, reason = _window_end(capture, pid, params, recall)

    path = annotated_path(capture, pid, 0, t1)
    for sample in path:
        _annotate_distances(capture, sample, enemy_jungler, enemy_laners)

    camps = []
    for event in capture.get_events("monster_kill", pid=pid, t1_s=t1):
        monster = event["detail"].get("monster")
        if monster not in JUNGLE_CAMPS:
            continue
        camps.append({
            "order": len(camps) + 1, "t": event["t"], "monster": monster,
            "x": event["x"], "y": event["y"],
            "zone": (spatial.zone_of(event["x"], event["y"])
                     if event["x"] is not None and event["y"] is not None else None),
            "in_enemy_jungle": event["detail"].get("in_enemy_jungle"),
        })

    start_zone = camps[0]["zone"] if camps else None
    return {
        **info,
        "enemy_jungler_pid": enemy_jungler,
        "window": {"t0": 0, "t1": t1, "reason": reason},
        "start_camp": camps[0]["monster"] if camps else None,
        "start_side": start_zone.rsplit("_", 1)[-1] if start_zone else None,
        "first_clear": [c["monster"] for c in camps],
        "camps": camps,
        "path": path,
        "timeline": _timeline(capture, pid, enemies, t1, params["ward_radius"]),
        "levels": [{"t": e["t"], "level": e["detail"].get("level")}
                   for e in capture.get_events("level_up", pid=pid, t1_s=t1)],
        "first_recall": recall,
        "river_crossings": [{"t": z["t_enter"], "zone": z["zone"]}
                            for z in zone_sequence(path) if z["zone"].startswith("river_")],
        "ganks": _ganks(capture, pid, enemies, path, params),
        "invades": _invades(capture, pid, enemies, side, path, camps, params),
    }


# ---------- ventana ----------

def _first_recall(capture: GameCapture, pid: int, max_s: float) -> Optional[Dict[str, Any]]:
    """Primer recall completado del jugador dentro de `max_s`."""
    for end in capture.get_events("channel_end", pid=pid, t1_s=max_s):
        if end["detail"].get("type") != "recall" or end["detail"].get("interrupted"):
            continue
        starts = [e for e in capture.get_events("channel_start", pid=pid, t1_s=end["t"])
                  if e["detail"].get("type") == "recall"]
        return {
            "t_start": starts[-1]["t"] if starts else None,
            "t_end": end["t"],
            "level": level_at(capture, pid, end["t"]),
        }
    return None


def _window_end(capture: GameCapture, pid: int, params: Dict[str, Any],
                recall: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    duration = capture.game.get("duration_s")
    until = params["until"]
    if until != "first_recall":
        t1 = float(until)
        return (min(t1, duration) if duration else t1), "fixed"

    candidates = [(params["max_s"], "max_s")]
    if recall is not None:
        candidates.append((recall["t_end"], "first_recall"))
    deaths = [e for e in capture.get_events("kill", t1_s=params["max_s"])
              if e["detail"].get("victim") == pid]
    if deaths:
        candidates.append((deaths[0]["t"], "first_death"))
    if duration:
        candidates.append((duration, "game_end"))
    return min(candidates, key=lambda c: c[0])


# ---------- cruce de posiciones ----------

def _annotate_distances(capture: GameCapture, sample: Dict[str, Any],
                        enemy_jungler: Optional[int], enemy_laners: List[int]):
    """Añade a la muestra la distancia al jungla rival y al rival de línea más
    cercano (vivo) en ese mismo instante."""
    sample["enemy_jungler_d"] = None
    if enemy_jungler is not None:
        other = capture.positions.at(enemy_jungler, sample["t"])
        if other is not None:
            sample["enemy_jungler_d"] = round(spatial.distance(sample, other))

    nearest_pid, nearest_d = None, None
    for enemy in enemy_laners:
        other = capture.positions.at(enemy, sample["t"])
        if other is None or other["alive"] is False:
            continue
        d = spatial.distance(sample, other)
        if nearest_d is None or d < nearest_d:
            nearest_pid, nearest_d = enemy, d
    sample["nearest_enemy_pid"] = nearest_pid
    sample["nearest_enemy_d"] = round(nearest_d) if nearest_d is not None else None


def _outcome(capture: GameCapture, pid: int, enemies: List[int],
             t0: float, t1: float, radius: float) -> Dict[str, Any]:
    """Qué pasó en `[t0, t1]`: kills, asistencias y muertes del jungla, y
    summoners usados por rivales que estaban cerca de él."""
    kills, assists, deaths = [], [], []
    for event in capture.get_events("kill", t0_s=t0, t1_s=t1):
        detail = event["detail"]
        if event["pid"] == pid:
            kills.append(slim_event(event))
        elif pid in (detail.get("assistants") or []):
            assists.append(slim_event(event))
        elif detail.get("victim") == pid:
            deaths.append(slim_event(event))

    summoners = []
    for event in capture.get_events("summoner_used", t0_s=t0, t1_s=t1):
        if event["pid"] not in enemies:
            continue
        me = capture.positions.at(pid, event["t"])
        them = capture.positions.at(event["pid"], event["t"])
        if me and them and spatial.distance(me, them) <= radius:
            summoners.append({"t": event["t"], "pid": event["pid"],
                              "spell": event["detail"].get("spell"),
                              "distance": round(spatial.distance(me, them))})

    if kills:
        result = "kill"
    elif assists:
        result = "assist"
    elif deaths:
        result = "death"
    elif summoners:
        result = "summoner_burned"
    else:
        result = "none"
    return {"result": result, "kills": kills, "assists": assists,
            "deaths": deaths, "enemy_summoners": summoners}


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    merged: List[Tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start - merged[-1][1] <= _GAP_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _ganks(capture: GameCapture, pid: int, enemies: List[int],
           path: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    radius = params["gank_radius"]

    def near(sample):
        d = sample["nearest_enemy_d"]
        return sample["alive"] is not False and d is not None and d < radius

    def in_lane(sample):
        return sample["alive"] is not False and spatial.lane_of(sample["zone"]) is not None

    intervals = [(run[0]["t"], run[-1]["t"])
                 for run in runs(path, near, params["min_near_s"], _GAP_S)]
    for run in runs(path, in_lane, params["min_lane_s"], _GAP_S):
        distances = [s["nearest_enemy_d"] for s in run if s["nearest_enemy_d"] is not None]
        if distances and min(distances) < params["lane_enemy_radius"]:
            intervals.append((run[0]["t"], run[-1]["t"]))

    ganks = []
    for t_enter, t_exit in _merge_intervals(intervals):
        samples = [s for s in path if t_enter <= s["t"] <= t_exit]
        lane_samples = [s for s in samples if in_lane(s)]
        with_enemy = [s for s in samples if s["nearest_enemy_d"] is not None]
        closest = min(with_enemy, key=lambda s: s["nearest_enemy_d"]) if with_enemy else None

        closest_info = None
        if closest is not None:
            meta = capture.players.get(closest["nearest_enemy_pid"], {})
            closest_info = {
                "pid": closest["nearest_enemy_pid"], "role": meta.get("role"),
                "champion": meta.get("champion"),
                "distance": closest["nearest_enemy_d"], "t": closest["t"],
            }
        if lane_samples:
            lanes = Counter(spatial.lane_of(s["zone"]) for s in lane_samples)
            lane = lanes.most_common(1)[0][0]
        else:
            lane = _ROLE_LANE.get((closest_info or {}).get("role"))

        ganks.append({
            "lane": lane,
            "t_enter": t_enter, "t_exit": t_exit,
            "duration": round(t_exit - t_enter, 3),
            "in_lane_s": len(lane_samples),
            "near_enemy_s": sum(1 for s in samples if near(s)),
            "closest": closest_info,
            **_outcome(capture, pid, enemies, t_enter,
                       t_exit + params["outcome_grace_s"], 2 * radius),
        })
    return ganks


def _invades(capture: GameCapture, pid: int, enemies: List[int], side: Optional[str],
             path: List[Dict[str, Any]], camps: List[Dict[str, Any]],
             params: Dict[str, Any]) -> List[Dict[str, Any]]:
    def in_enemy_jungle(sample):
        return sample["alive"] is not False and spatial.is_enemy_jungle(sample["zone"], side)

    invades = []
    for run in runs(path, in_enemy_jungle, params["min_invade_s"], _GAP_S):
        t_enter, t_exit = run[0]["t"], run[-1]["t"]
        zones = Counter(s["zone"] for s in run if in_enemy_jungle(s))
        distances = [s["enemy_jungler_d"] for s in run if s["enemy_jungler_d"] is not None]
        outcome = _outcome(capture, pid, enemies, t_enter,
                           t_exit + params["outcome_grace_s"], 2 * params["gank_radius"])
        outcome.pop("enemy_summoners")
        invades.append({
            "zone": zones.most_common(1)[0][0],
            "t_enter": t_enter, "t_exit": t_exit,
            "duration": round(t_exit - t_enter, 3),
            "camps_taken": [c["monster"] for c in camps if t_enter <= c["t"] <= t_exit + 1],
            "wards_placed": len(capture.get_events("ward_placed", pid=pid,
                                                   t0_s=t_enter, t1_s=t_exit)),
            "min_enemy_jungler_d": min(distances) if distances else None,
            **outcome,
        })
    return invades


# ---------- línea temporal ----------

def _timeline(capture: GameCapture, pid: int, enemies: List[int], t1: float,
              ward_radius: float) -> List[Dict[str, Any]]:
    entries = []
    for event in capture.get_events(t1_s=t1):
        relation = None
        if event["pid"] == pid and event["kind"] in _OWN_KINDS:
            relation = "self"
        elif event["kind"] == "kill":
            if event["detail"].get("victim") == pid:
                relation = "victim"
            elif pid in (event["detail"].get("assistants") or []):
                relation = "assist"
        elif event["kind"] == "ward_placed" and event["pid"] in enemies:
            me = capture.positions.at(pid, event["t"])
            if (me is not None and event["x"] is not None and event["y"] is not None
                    and spatial.distance(me, event) <= ward_radius):
                relation = "enemy_ward_nearby"
        if relation is None:
            continue
        entry = slim_event(event)
        entry["relation"] = relation
        entry["jungler_pos"] = zone_at(capture, pid, event["t"])
        entries.append(entry)
    return entries

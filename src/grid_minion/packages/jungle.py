"""Paquete de pathing de jungla: recorrido del jungla por celdas y áreas,
cruzado con los eventos."""
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

from .. import spatial
from ..capture import GameCapture
from ..observers.game_events import JUNGLE_CAMPS
from ..rift import RiftMap
from ._common import (
    annotated_path, level_at, player_info, require_sections, route, slim_event, zone_at,
)

SCHEMA = "grid-minion/package/jungle_pathing/v2"

_LANE_OF_ROLE = {"TOP": "top", "MID": "mid", "ADC": "bot", "SUPPORT": "bot"}
_OWN_KINDS = (
    "monster_kill", "ward_placed", "ward_killed", "kill", "kill_special",
    "summoner_used", "channel_start", "channel_end", "level_up", "item_purchased",
)
# Hueco máximo (s) entre muestras para seguir considerándolas el mismo tramo
# (una muestra por segundo; en el borde de un área se parpadea).
_GAP_S = 3


def jungle_pathing(
    capture: GameCapture,
    until: Union[str, float] = "first_recall",
    max_s: float = 600,
    min_invade_s: float = 3,
    min_enemy_s: float = 2,
    outcome_grace_s: float = 5,
    ward_radius: float = 2500,
) -> Dict[str, Any]:
    """
    Recorrido de cada jungla por el mapa de celdas, cruzado con los eventos.

    Todo se razona por **áreas** del mapa (`grid_minion.rift`), no por
    distancias:

      - **Visitas a línea**: cada vez que el jungla entra en una línea queda
        registrado, haya rivales o no (cruzar mid, bajar a una línea, pasar por
        una línea camino de otro sitio). Es parte del pathing.
      - **Gank**: una visita a línea durante la que hay un rival de línea vivo
        en esa misma área de línea al menos `min_enemy_s` segundos. Es una
        etiqueta sobre la visita. Cada visita dice además dónde ocurre respecto
        a las torres (`lane_position`): `defensive` (bajo las torres propias),
        `laning` (entre las torres exteriores) o `dive` (bajo las rivales).
      - **Invade**: tramo en áreas de jungla del rival.

    Args:
        capture: Captura con posiciones y eventos.
        until: Fin de la ventana de cada jungla. `"first_recall"` (default):
            hasta su primer recall completado o su primera muerte, con tope
            `max_s`. Un número: hasta ese segundo de juego.
        max_s: Tope de la ventana con `until="first_recall"`.
        min_invade_s: Segundos seguidos en jungla rival para contar un invade.
        min_enemy_s: Segundos que un rival de línea debe estar en la misma área
            durante la visita para que cuente como gank.
        outcome_grace_s: Margen tras salir de un tramo en el que una kill o un
            summoner todavía se atribuyen a ese tramo.
        ward_radius: Un ward rival colocado a menos de esta distancia del
            jungla entra en su línea temporal.

    Returns:
        ```
        {schema, game, params, junglers: [{
            pid, side, role, champion, name, enemy_jungler_pid,
            window: {t0, t1, reason},       # first_recall | first_death | max_s | fixed | game_end
            start_camp, start_side, first_clear: [monstruo, ...],
            camps: [{order, t, monster, x, y, cell, area, in_enemy_jungle}],
            path: [{t, x, y, alive, zone, cell, area, enemy_jungler_d}],
            route: [{cell, area, kind, t_enter, t_exit}],     # celdas, en orden
            area_route: [{area, kind, t_enter, t_exit}],      # áreas, en orden
            timeline: [evento + {relation, jungler_pos}],
            levels: [{t, level}],
            river_crossings: [{t, area}],                     # entradas al río o a un foso
            first_recall: {t_start, t_end, level} | None,
            lane_visits: [{lane, lane_position, areas, cells, t_enter, t_exit,
                           duration, came_from, went_to, crossed,
                           enemies_present: [{pid, role, champion, area, seconds}],
                           gank, result, kills, assists, deaths, enemy_summoners}],
            ganks: [...],          # las visitas con gank=True (misma forma)
            invades: [{areas, cells, t_enter, t_exit, duration, camps_taken,
                       wards_placed, min_enemy_jungler_d,
                       result, kills, assists, deaths}],
        }]}
        ```
        `crossed` es `True` cuando el jungla entra a la línea por un lado del
        mapa y sale por el otro (p. ej. cruza mid de su jungla de arriba a la
        de abajo). `relation` en la línea temporal: `self`, `victim`, `assist`
        o `enemy_ward_nearby`.
    """
    require_sections(capture, "positions", "events")
    params = {
        "until": until, "max_s": max_s, "min_invade_s": min_invade_s,
        "min_enemy_s": min_enemy_s,
        "outcome_grace_s": outcome_grace_s, "ward_radius": ward_radius,
        "rift_version": RiftMap.default().version,
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
    enemies = [p for p, meta in capture.players.items() if meta.get("side") not in (None, side)]
    enemy_junglers = [p for p in enemies if capture.players[p].get("role") == "JUNGLE"]
    enemy_jungler = enemy_junglers[0] if enemy_junglers else None
    enemy_laners = [p for p in enemies if p != enemy_jungler]

    recall = _first_recall(capture, pid, params["max_s"])
    t1, reason = _window_end(capture, pid, params, recall)

    path = annotated_path(capture, pid, 0, t1)
    for sample in path:
        sample["enemy_jungler_d"] = None
        if enemy_jungler is not None:
            other = capture.positions.at(enemy_jungler, sample["t"])
            if other is not None:
                sample["enemy_jungler_d"] = round(spatial.distance(sample, other))

    camps = []
    for event in capture.get_events("monster_kill", pid=pid, t1_s=t1):
        monster = event["detail"].get("monster")
        if monster not in JUNGLE_CAMPS:
            continue
        where = slim_event(event)
        in_enemy = event["detail"].get("in_enemy_jungle")
        camps.append({
            "order": len(camps) + 1, "t": event["t"], "monster": monster,
            "x": event["x"], "y": event["y"],
            # Celda donde murió el monstruo (puede estar lejos si se arrastró).
            "cell": where.get("cell"),
            # Área del campamento: por su identidad, no por dónde murió.
            "area": _camp_area(monster, side, in_enemy) or where.get("area"),
            "in_enemy_jungle": in_enemy,
        })

    start_side = None
    if camps and camps[0]["x"] is not None:
        start_side = spatial.map_side(camps[0]["x"], camps[0]["y"])

    visits = _lane_visits(capture, pid, side, enemies, enemy_laners, path, params)
    return {
        **info,
        "enemy_jungler_pid": enemy_jungler,
        "window": {"t0": 0, "t1": t1, "reason": reason},
        "start_camp": camps[0]["monster"] if camps else None,
        "start_side": start_side,
        "first_clear": [c["monster"] for c in camps],
        "camps": camps,
        "path": path,
        "route": route(path, "cell"),
        "area_route": route(path, "area"),
        "river_crossings": [{"t": step["t_enter"], "area": step["area"]}
                            for step in route(path, "area") if step["kind"] in ("river", "pit")],
        "timeline": _timeline(capture, pid, enemies, t1, params["ward_radius"]),
        "levels": [{"t": e["t"], "level": e["detail"].get("level")}
                   for e in capture.get_events("level_up", pid=pid, t1_s=t1)],
        "first_recall": recall,
        "lane_visits": visits,
        "ganks": [v for v in visits if v["gank"]],
        "invades": _invades(capture, pid, enemies, side, path, camps, params),
    }


_CAMP_AREA = {"redcamp": "red_buff", "bluecamp": "blue_buff", "krug": "krugs",
              "raptor": "raptors", "wolf": "wolves", "gromp": "gromp"}


def _camp_area(monster: str, side: Optional[str], in_enemy_jungle: Optional[bool]
               ) -> Optional[str]:
    """Área de un campamento a partir de qué monstruo es y de quién es la
    jungla (el feed lo dice con `inEnemyJungle`). Los monstruos se arrastran
    antes de morir, así que la posición de la muerte no siempre cae en su área."""
    camp = _CAMP_AREA.get(monster)
    if camp is None or side not in ("BLUE", "RED") or in_enemy_jungle is None:
        return None
    owner = side if not in_enemy_jungle else ("RED" if side == "BLUE" else "BLUE")
    return f"jungle_{owner.lower()}_{camp}"


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


# ---------- tramos ----------

def _segments(path: List[Dict[str, Any]], key_of) -> List[Tuple[Any, List[Dict[str, Any]]]]:
    """Tramos consecutivos de la trayectoria con la misma clave (`None` =
    fuera de cualquier tramo). Un hueco de hasta `_GAP_S` con otra clave no
    corta el tramo si se vuelve a la misma."""
    segments: List[Tuple[Any, List[Dict[str, Any]]]] = []
    for sample in path:
        if sample.get("alive") is False:
            continue
        key = key_of(sample)
        if key is None:
            continue
        if segments and segments[-1][0] == key and sample["t"] - segments[-1][1][-1]["t"] <= _GAP_S + 1:
            segments[-1][1].append(sample)
        else:
            segments.append((key, [sample]))
    return segments


def _neighbour_area(path: List[Dict[str, Any]], t: float, before: bool) -> Optional[Dict[str, Any]]:
    """Primera posición fuera del tramo, antes o después de `t`."""
    candidates = [s for s in path if (s["t"] < t if before else s["t"] > t)
                  and s.get("alive") is not False]
    if not candidates:
        return None
    sample = candidates[-1] if before else candidates[0]
    return sample


def _outcome(capture: GameCapture, pid: int, enemies: List[int],
             t0: float, t1: float, areas: Optional[set] = None) -> Dict[str, Any]:
    """Qué pasó en `[t0, t1]`: kills, asistencias y muertes del jungla, y
    summoners usados por rivales que estaban en las mismas áreas que él."""
    rift = RiftMap.default()
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
        them = capture.positions.at(event["pid"], event["t"])
        if them is None:
            continue
        area = rift.area_at(them["x"], them["y"])
        if areas is None or area in areas:
            summoners.append({"t": event["t"], "pid": event["pid"],
                              "spell": event["detail"].get("spell"), "area": area})

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


def _lane_position(segments: List[Optional[str]], side: Optional[str]) -> Optional[str]:
    """`dive` si pisa la parte de la línea tras las torres rivales; si no,
    `laning` si pisa la zona de línea; si no, `defensive`."""
    own_back = {"BLUE": "blue_back", "RED": "red_back"}.get(side)
    enemy_back = {"BLUE": "red_back", "RED": "blue_back"}.get(side)
    if enemy_back in segments:
        return "dive"
    if "laning" in segments:
        return "laning"
    if own_back in segments:
        return "defensive"
    return None


def _lane_visits(capture: GameCapture, pid: int, side: Optional[str], enemies: List[int],
                 enemy_laners: List[int], path: List[Dict[str, Any]],
                 params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rift = RiftMap.default()

    def lane_of(sample):
        cell = rift.cell(sample["cell"])
        return cell["lane"] if cell["kind"] == "lane" else None

    visits = []
    for lane, samples in _segments(path, lane_of):
        t_enter, t_exit = samples[0]["t"], samples[-1]["t"]
        areas = list(dict.fromkeys(s["area"] for s in samples))
        before = _neighbour_area(path, t_enter, before=True)
        after = _neighbour_area(path, t_exit, before=False)

        crossed = False
        if before is not None and after is not None:
            if lane == "mid":
                crossed = spatial.map_side(before["x"], before["y"]) != spatial.map_side(after["x"], after["y"])
            else:
                crossed = spatial.map_half(before["x"], before["y"]) != spatial.map_half(after["x"], after["y"])

        # Rivales de línea vivos en las mismas áreas de línea durante la visita.
        present: Counter = Counter()
        where: Dict[int, str] = {}
        for sample in samples:
            for enemy in enemy_laners:
                pos = capture.positions.at(enemy, sample["t"])
                if pos is None or pos["alive"] is False:
                    continue
                area = rift.area_at(pos["x"], pos["y"])
                if area == sample["area"]:
                    present[enemy] += 1
                    where[enemy] = area
        enemies_present = [
            {"pid": enemy, "role": capture.players.get(enemy, {}).get("role"),
             "champion": capture.players.get(enemy, {}).get("champion"),
             "area": where[enemy], "seconds": seconds}
            for enemy, seconds in present.most_common()]
        segments = [rift.cell(s["cell"])["segment"] for s in samples]

        visits.append({
            "lane": lane,
            "lane_position": _lane_position(segments, side),
            "areas": areas,
            "cells": list(dict.fromkeys(s["cell"] for s in samples)),
            "t_enter": t_enter, "t_exit": t_exit,
            "duration": round(t_exit - t_enter, 3),
            "came_from": before["area"] if before else None,
            "went_to": after["area"] if after else None,
            "crossed": crossed,
            "enemies_present": enemies_present,
            "gank": any(e["seconds"] >= params["min_enemy_s"] for e in enemies_present),
            **_outcome(capture, pid, enemies, t_enter,
                       t_exit + params["outcome_grace_s"], set(areas)),
        })
    return visits


def _invades(capture: GameCapture, pid: int, enemies: List[int], side: Optional[str],
             path: List[Dict[str, Any]], camps: List[Dict[str, Any]],
             params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rift = RiftMap.default()

    def enemy_jungle(sample):
        cell = rift.cell(sample["cell"])
        if cell["kind"] == "jungle" and side in ("BLUE", "RED") and cell["owner"] != side:
            return "enemy_jungle"
        return None

    invades = []
    for _, samples in _segments(path, enemy_jungle):
        t_enter, t_exit = samples[0]["t"], samples[-1]["t"]
        if t_exit - t_enter < params["min_invade_s"]:
            continue
        distances = [s["enemy_jungler_d"] for s in samples if s["enemy_jungler_d"] is not None]
        areas = list(dict.fromkeys(s["area"] for s in samples))
        outcome = _outcome(capture, pid, enemies, t_enter, t_exit + params["outcome_grace_s"])
        outcome.pop("enemy_summoners")
        invades.append({
            "areas": areas,
            "cells": list(dict.fromkeys(s["cell"] for s in samples)),
            "t_enter": t_enter, "t_exit": t_exit,
            "duration": round(t_exit - t_enter, 3),
            "camps_taken": [c["monster"] for c in camps if t_enter - 1 <= c["t"] <= t_exit + 1],
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

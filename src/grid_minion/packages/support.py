"""Paquete de openings del support: nivel 1, llegada a línea, roams y primer
recall."""
from collections import Counter
from typing import Any, Dict, List, Optional

from ..capture import GameCapture
from ..rift import RiftMap
from ._common import annotated_path, player_info, require_sections, slim_event
from .recalls import recall_openings

SCHEMA = "grid-minion/package/support_openings/v1"

_GAP_S = 3


def support_openings(
    capture: GameCapture,
    until_s: float = 240,
    marks_s=(30, 60, 85),
    min_roam_s: float = 8,
    settle_s: float = 15,
    recall_until_s: float = 600,
) -> Dict[str, Any]:
    """
    Qué hace cada support al principio de la partida.

    Args:
        capture: Captura con posiciones y eventos.
        until_s: Fin de la ventana para los roams (por defecto 4:00).
        marks_s: Instantes de nivel 1 en los que se da su posición.
        min_roam_s: Segundos seguidos fuera de su línea para contar un roam.
        settle_s: Segundos seguidos en su línea para darla por "llegada". Así
            pasar por la línea al salir de base o ayudar al jungla en el nivel
            1 (leash) no cuentan como llegada ni como roam.
        recall_until_s: Hasta cuándo se busca su primer recall.

    Returns:
        ```
        {schema, game, params, supports: [{
            pid, side, role, champion, name,
            own_lane,                        # línea de su ADC (normalmente bot)
            level_one: {"30": {x, y, cell, area}, ...},
            lane_arrival: {t, area} | None,  # cuando se asienta en su línea
            roams: [{type, t_enter, t_exit, duration, target_area, target_kind,
                     target_lane, areas, with_jungler_s, kills, assists,
                     deaths, wards, result}],
            path: [{t, x, y, alive, zone, cell, area}],   # hasta `until_s`
            wards: [evento],                 # wards hasta `until_s`
            first_recall: {...} | None,      # mismo formato que recall_openings
        }]}
        ```
        Un **roam** es un tramo de al menos `min_roam_s` segundos, tras
        llegar a línea, fuera del área de su línea y fuera de su base (volver a
        base andando no es un roam). `type`: `river` si el destino es el río
        (o el foso) del lado de su línea, normalmente para visión u objetivo;
        `roam` si va a otra línea, a la jungla o al otro río. `result`:
        `kill`, `assist`, `death`, `ward` o `none`.
    """
    require_sections(capture, "positions", "events")
    rift = RiftMap.default()
    recalls = recall_openings(capture, until_s=recall_until_s)["recalls"]
    supports = []
    for pid in capture.players_by(role="SUPPORT"):
        info = player_info(capture, pid)
        side = info["side"]
        mates = {capture.players[p].get("role"): p for p in capture.players_by(side=side)}
        jungler = mates.get("JUNGLE")
        own_lane = _own_lane(capture, rift, mates.get("ADC")) or "bot"
        path = annotated_path(capture, pid, 0, until_s)

        level_one = {}
        for mark in marks_s:
            pos = capture.positions.at(pid, mark)
            if pos is not None:
                cell = rift.cell_at(pos["x"], pos["y"])
                level_one[str(int(mark))] = {"x": pos["x"], "y": pos["y"],
                                             "cell": cell["name"], "area": cell["area"]}

        arrival = _lane_arrival(rift, path, own_lane, settle_s)

        roams = []
        if arrival is not None:
            roams = _roams(capture, rift, pid, side, jungler, own_lane,
                           [s for s in path if s["t"] >= arrival["t"]], min_roam_s)

        first_recall = next((r for r in recalls if r["pid"] == pid), None)
        supports.append({
            **info,
            "own_lane": own_lane,
            "level_one": level_one,
            "lane_arrival": {"t": arrival["t"], "area": arrival["area"]} if arrival else None,
            "roams": roams,
            "path": path,
            "wards": [slim_event(e) for e in capture.get_events("ward_placed", pid=pid,
                                                                 t1_s=until_s)],
            "first_recall": first_recall,
        })
    return {
        "schema": SCHEMA,
        "game": dict(capture.game),
        "params": {"until_s": until_s, "min_roam_s": min_roam_s, "settle_s": settle_s,
                   "recall_until_s": recall_until_s, "rift_version": rift.version},
        "supports": supports,
    }


def _lane_arrival(rift, path, own_lane, settle_s):
    """Primera muestra de un tramo de al menos `settle_s` segundos en su línea."""
    start = None
    for sample in path:
        cell = rift.cell(sample["cell"])
        in_lane = (sample.get("alive") is not False and cell["kind"] == "lane"
                   and cell["lane"] == own_lane)
        if not in_lane:
            start = None
            continue
        if start is None:
            start = sample
        if sample["t"] - start["t"] >= settle_s:
            return start
    return None


# Río y foso del lado de cada línea lateral.
_LANE_RIVER = {"bot": {"river_bot", "dragon_pit"}, "top": {"river_top", "baron_pit"}}


def _own_lane(capture, rift, adc: Optional[int]) -> Optional[str]:
    """Línea en la que juega el ADC entre 1:30 y 3:00."""
    if adc is None:
        return None
    lanes = Counter()
    for sample in capture.positions_between(adc, 90, 180):
        if sample["alive"] is False:
            continue
        cell = rift.cell_at(sample["x"], sample["y"])
        if cell["kind"] == "lane":
            lanes[cell["lane"]] += 1
    return lanes.most_common(1)[0][0] if lanes else None


def _roams(capture, rift, pid, side, jungler, own_lane, path, min_roam_s) -> List[Dict[str, Any]]:
    def away(sample):
        cell = rift.cell(sample["cell"])
        return not (cell["kind"] == "lane" and cell["lane"] == own_lane)

    runs: List[List[Dict[str, Any]]] = []
    for sample in path:
        if sample.get("alive") is False or not away(sample):
            continue
        if runs and sample["t"] - runs[-1][-1]["t"] <= _GAP_S + 1:
            runs[-1].append(sample)
        else:
            runs.append([sample])

    roams = []
    for run in runs:
        t_enter, t_exit = run[0]["t"], run[-1]["t"]
        if t_exit - t_enter < min_roam_s:
            continue
        cells = [rift.cell(s["cell"]) for s in run]
        if any(c["kind"] == "base" and c["owner"] == side for c in cells):
            continue   # vuelta a base, no roam
        areas = list(dict.fromkeys(c["area"] for c in cells))
        # Destino: el área donde más tiempo pasa.
        counts = Counter(c["area"] for c in cells)
        target = counts.most_common(1)[0][0]
        target_cell = next(c for c in cells if c["area"] == target)

        with_jungler = 0
        if jungler is not None:
            for s in run:
                other = capture.positions.at(jungler, s["t"])
                if other and other["alive"] is not False and rift.are_near(
                        rift.cell(s["cell"])["id"], rift.cell_id_at(other["x"], other["y"])):
                    with_jungler += 1

        kills, assists, deaths = [], [], []
        for event in capture.get_events("kill", t0_s=t_enter, t1_s=t_exit + 5):
            if event["pid"] == pid:
                kills.append(slim_event(event))
            elif pid in (event["detail"].get("assistants") or []):
                assists.append(slim_event(event))
            elif event["detail"].get("victim") == pid:
                deaths.append(slim_event(event))
        wards = [slim_event(e) for e in capture.get_events("ward_placed", pid=pid,
                                                            t0_s=t_enter, t1_s=t_exit)]
        result = ("kill" if kills else "assist" if assists else "death" if deaths
                  else "ward" if wards else "none")
        roams.append({
            "type": "river" if target in _LANE_RIVER.get(own_lane, ()) else "roam",
            "t_enter": t_enter, "t_exit": t_exit, "duration": round(t_exit - t_enter, 3),
            "target_area": target, "target_kind": target_cell["kind"],
            "target_lane": target_cell["lane"],
            "areas": areas, "with_jungler_s": with_jungler,
            "kills": kills, "assists": assists, "deaths": deaths, "wards": wards,
            "result": result,
        })
    return roams

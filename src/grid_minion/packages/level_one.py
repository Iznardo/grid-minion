"""Paquete de nivel 1: qué hace cada equipo antes de que empiece la partida."""
from typing import Any, Dict, Sequence

from .. import spatial
from ..capture import GameCapture
from ._common import annotated_path, locate, player_info, require_sections, runs, slim_event

SCHEMA = "grid-minion/package/level_one/v1"


def level_one(
    capture: GameCapture,
    until_s: float = 90,
    marks_s: Sequence[float] = (30, 60, 85),
    min_invade_s: float = 3,
) -> Dict[str, Any]:
    """
    Posicionamiento de los 10 jugadores desde 0:00 hasta `until_s`.

    Args:
        capture: Captura con posiciones y eventos.
        until_s: Fin de la ventana. Por defecto 90 s (1:30), cuando ya han
            salido los primeros campamentos.
        marks_s: Instantes en los que se da una "foto" de cada jugador.
        min_invade_s: Tiempo mínimo seguido en terreno enemigo para contarlo.

    Returns:
        ```
        {schema, game, window: {t0, t1},
         players: {pid: {pid, side, role, champion, name,
                         path: [{t, x, y, alive, zone}],
                         marks: {"30": {x, y, zone, cell, area} | None, ...}}},
         wards: [evento], kills: [evento], summoners: [evento],
         invades: [{pid, side, role, champion, zone, t_enter, t_exit, duration,
                    deepest: {x, y}}]}
        ```
        `invades` son candidatos: tramos en jungla o base del equipo
        contrario. Un jugador que sale por el río no cuenta.
    """
    require_sections(capture, "positions", "events")

    players: Dict[str, Any] = {}
    invades = []
    for pid in sorted(capture.players):
        info = player_info(capture, pid)
        path = annotated_path(capture, pid, 0, until_s)
        marks = {}
        for mark in marks_s:
            if mark > until_s:
                continue
            pos = capture.positions.at(pid, mark)
            marks[str(int(mark) if float(mark).is_integer() else mark)] = (
                {"x": pos["x"], "y": pos["y"], **locate(pos["x"], pos["y"])}
                if pos else None)
        players[str(pid)] = {**info, "path": path, "marks": marks}

        side = info["side"]

        def in_enemy_ground(sample, side=side):
            zone = sample["zone"]
            owner = spatial.zone_owner(zone)
            return owner is not None and side is not None and owner != side

        for run in runs(path, in_enemy_ground, min_duration_s=min_invade_s, max_gap_s=2):
            # "Más profundo" = el punto más alejado de la propia base.
            own_corner = ({"x": spatial.MAP_MIN_X, "y": spatial.MAP_MIN_Y} if side == "BLUE"
                          else {"x": spatial.MAP_MAX_X, "y": spatial.MAP_MAX_Y})
            deepest = max(run, key=lambda s: spatial.distance(s, own_corner))
            zones = [s["zone"] for s in run]
            invades.append({
                "pid": pid, "side": side, "role": info["role"], "champion": info["champion"],
                "zone": max(set(zones), key=zones.count),
                "t_enter": run[0]["t"], "t_exit": run[-1]["t"],
                "duration": round(run[-1]["t"] - run[0]["t"], 3),
                "deepest": {"x": deepest["x"], "y": deepest["y"]},
            })

    invades.sort(key=lambda i: (i["t_enter"], i["pid"]))
    return {
        "schema": SCHEMA,
        "game": dict(capture.game),
        "window": {"t0": 0, "t1": until_s},
        "players": players,
        "wards": [slim_event(e) for e in capture.get_events("ward_placed", t1_s=until_s)],
        "kills": [slim_event(e) for e in capture.get_events("kill", t1_s=until_s)],
        "summoners": [slim_event(e) for e in capture.get_events("summoner_used", t1_s=until_s)],
        "invades": invades,
    }

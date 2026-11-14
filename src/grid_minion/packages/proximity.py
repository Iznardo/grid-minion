"""Paquete de proximidad: cuánto juegan el jungla y el support cerca de cada
compañero, por fases de la partida, y dónde conectan jungla y support."""
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..capture import GameCapture
from ..rift import RiftMap
from ._common import player_info, require_sections, slim_event

SCHEMA = "grid-minion/package/proximity/v1"

DEFAULT_PHASES: Tuple[Tuple[float, Optional[float]], ...] = ((0, 420), (420, 900), (900, None))
FOCUS_ROLES = ("JUNGLE", "SUPPORT")
ROLE_ORDER = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
# Un tick sin datos más largo que esto (pausa, hueco del feed) no cuenta entero.
_MAX_TICK_S = 2.0
_GAP_S = 3


def proximity(
    capture: GameCapture,
    phases: Sequence[Tuple[float, Optional[float]]] = DEFAULT_PHASES,
    focus_roles: Sequence[str] = FOCUS_ROLES,
    min_together_s: float = 3,
) -> Dict[str, Any]:
    """
    Cercanía del jungla y del support a cada compañero, por fases.

    **Cerca** = misma celda del mapa o celdas que se tocan
    (`RiftMap.are_near`), con los dos jugadores vivos. Las celdas miden unas
    1 000-1 500 unidades, así que "cerca" es jugar en la misma zona.

    Args:
        capture: Captura con posiciones (y eventos, para las jugadas en común).
        phases: Tramos de la partida en segundos, `(desde, hasta)`; `None`
            como fin = hasta el final. Por defecto 0-7, 7-15 y 15+ minutos.
        focus_roles: Roles de los que se mide la cercanía.
        min_together_s: Segundos seguidos cerca para contar un tramo juntos
            entre jungla y support.

    Returns:
        ```
        {schema, game, params, teams: [{
            side,
            players: [{pid, role, champion, name,
                       phases: [{phase: "0-7", t0, t1, alive_s,
                                 near: {ROL: {pid, seconds, both_alive_s, share}}}],
                       minutes: {ROL: [share por minuto, ...]}}],
            duo: {jungler_pid, support_pid,
                  phases: [{phase, seconds, both_alive_s, share}],
                  together: [{t_enter, t_exit, duration, areas, kind,
                              kills, wards}],
                  areas: {area: segundos juntos}}
        }]}
        ```
        `share` = segundos cerca / segundos con los dos vivos en esa fase
        (`None` si no hubo tiempo con los dos vivos). Así las muertes no
        rebajan la cercanía. `kind` de un tramo juntos: `lvl1`, `objective`,
        `invade`, `gank`, `vision` u `other`.
    """
    require_sections(capture, "positions")
    rift = RiftMap.default()
    track = capture.positions
    times = [t / 1000 for t in track.t_ms]
    weights = _tick_weights(times)

    # Celda de cada jugador en cada tick (o None si no hay dato o está muerto).
    cells: Dict[int, List[Optional[int]]] = {}
    for pid in track.pids:
        column = []
        for i in range(len(times)):
            sample = track._sample(pid, i)
            if sample is None or sample["alive"] is False:
                column.append(None)
            else:
                column.append(rift.cell_id_at(sample["x"], sample["y"]))
        cells[pid] = column

    phase_list = [_phase(p) for p in phases]
    teams = []
    for side in ("BLUE", "RED"):
        mates = {capture.players[pid].get("role"): pid
                 for pid in capture.players_by(side=side)
                 if capture.players[pid].get("role")}
        players = []
        for role in focus_roles:
            pid = mates.get(role)
            if pid is None or pid not in cells:
                continue
            players.append(_player_proximity(capture, rift, pid, mates, cells, times,
                                             weights, phase_list))
        duo = None
        jungler, support = mates.get("JUNGLE"), mates.get("SUPPORT")
        if jungler in cells and support in cells:
            duo = _duo(capture, rift, jungler, support, side, cells, times, weights,
                       phase_list, min_together_s)
        teams.append({"side": side, "players": players, "duo": duo})

    return {
        "schema": SCHEMA,
        "game": dict(capture.game),
        "params": {"phases": [list(p[1:]) for p in phase_list],
                   "focus_roles": list(focus_roles), "min_together_s": min_together_s,
                   "rift_version": rift.version},
        "teams": teams,
    }


# ---------- utilidades ----------

def _phase(spec: Tuple[float, Optional[float]]) -> Tuple[str, float, Optional[float]]:
    t0, t1 = spec
    label = f"{int(t0 // 60)}-{int(t1 // 60)}" if t1 is not None else f"{int(t0 // 60)}+"
    return label, float(t0), (float(t1) if t1 is not None else None)


def _tick_weights(times: List[float]) -> List[float]:
    """Segundos que representa cada muestra (hasta la siguiente)."""
    weights = []
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else t + 1.0
        weights.append(min(max(nxt - t, 0.0), _MAX_TICK_S))
    return weights


def _in_phase(t: float, phase) -> bool:
    _, t0, t1 = phase
    return t >= t0 and (t1 is None or t < t1)


def _share(near: float, both: float) -> Optional[float]:
    return round(near / both, 4) if both > 0 else None


# ---------- jugador vs. compañeros ----------

def _player_proximity(capture, rift, pid, mates, cells, times, weights, phase_list):
    info = player_info(capture, pid)
    focus = cells[pid]
    others = {role: mpid for role, mpid in mates.items() if mpid != pid and mpid in cells}

    phases_out = []
    for phase in phase_list:
        alive_s = 0.0
        near = {role: [0.0, 0.0] for role in others}     # [cerca, ambos vivos]
        for i, t in enumerate(times):
            if not _in_phase(t, phase) or focus[i] is None:
                continue
            alive_s += weights[i]
            for role, mpid in others.items():
                other = cells[mpid][i]
                if other is None:
                    continue
                near[role][1] += weights[i]
                if rift.are_near(focus[i], other):
                    near[role][0] += weights[i]
        phases_out.append({
            "phase": phase[0], "t0": phase[1], "t1": phase[2],
            "alive_s": round(alive_s, 1),
            "near": {role: {"pid": others[role], "seconds": round(n, 1),
                            "both_alive_s": round(b, 1), "share": _share(n, b)}
                     for role, (n, b) in sorted(near.items(), key=lambda kv: ROLE_ORDER.index(kv[0])
                                                if kv[0] in ROLE_ORDER else 99)},
        })

    # Serie por minuto: cuota de cercanía en cada minuto de partida.
    minutes: Dict[str, List[Optional[float]]] = {}
    last_minute = int(times[-1] // 60) if times else -1
    for role, mpid in others.items():
        series = []
        for minute in range(last_minute + 1):
            n = b = 0.0
            for i, t in enumerate(times):
                if not (minute * 60 <= t < (minute + 1) * 60):
                    continue
                if focus[i] is None or cells[mpid][i] is None:
                    continue
                b += weights[i]
                if rift.are_near(focus[i], cells[mpid][i]):
                    n += weights[i]
            series.append(_share(n, b))
        minutes[role] = series
    return {**info, "phases": phases_out, "minutes": minutes}


# ---------- jungla + support ----------

def _duo(capture, rift, jungler, support, side, cells, times, weights, phase_list,
         min_together_s):
    a, b = cells[jungler], cells[support]
    near_flags = [a[i] is not None and b[i] is not None and rift.are_near(a[i], b[i])
                  for i in range(len(times))]

    phases_out = []
    for phase in phase_list:
        n = both = 0.0
        for i, t in enumerate(times):
            if not _in_phase(t, phase) or a[i] is None or b[i] is None:
                continue
            both += weights[i]
            if near_flags[i]:
                n += weights[i]
        phases_out.append({"phase": phase[0], "seconds": round(n, 1),
                           "both_alive_s": round(both, 1), "share": _share(n, both)})

    # Tramos juntos (huecos de hasta _GAP_S no los cortan).
    runs: List[List[int]] = []
    for i, flag in enumerate(near_flags):
        if not flag:
            continue
        if runs and times[i] - times[runs[-1][-1]] <= _GAP_S + 1:
            runs[-1].append(i)
        else:
            runs.append([i])

    enemies = [p for p, m in capture.players.items() if m.get("side") not in (None, side)]
    enemy_laners = [p for p in enemies if capture.players[p].get("role") != "JUNGLE"]
    together, area_seconds = [], {}
    for run in runs:
        t_enter, t_exit = times[run[0]], times[run[-1]]
        if t_exit - t_enter < min_together_s:
            continue
        areas = []
        for i in run:
            for cid in (a[i], b[i]):
                area = rift.cells[cid]["area"]
                if area not in areas:
                    areas.append(area)
            area_seconds_key = rift.cells[a[i]]["area"]
            area_seconds[area_seconds_key] = area_seconds.get(area_seconds_key, 0.0) + weights[i]
        kills, wards = _shared_plays(capture, jungler, support, t_enter, t_exit)
        together.append({
            "t_enter": t_enter, "t_exit": t_exit, "duration": round(t_exit - t_enter, 3),
            "areas": areas,
            "kind": _together_kind(capture, rift, run, a, b, times, side, enemy_laners,
                                   jungler, support, t_enter, t_exit, kills, wards),
            "kills": kills, "wards": wards,
        })
    return {
        "jungler_pid": jungler, "support_pid": support,
        "phases": phases_out, "together": together,
        "areas": {k: round(v, 1) for k, v in sorted(area_seconds.items(), key=lambda kv: -kv[1])},
    }


def _shared_plays(capture, jungler, support, t0, t1):
    if capture.events is None:
        return [], []
    kills = []
    for event in capture.get_events("kill", t0_s=t0, t1_s=t1 + 2):
        involved = {event["pid"], *(event["detail"].get("assistants") or [])}
        if jungler in involved and support in involved:
            kills.append(slim_event(event))
    wards = [slim_event(e) for e in capture.get_events("ward_placed", t0_s=t0, t1_s=t1)
             if e["pid"] in (jungler, support)]
    return kills, wards


def _together_kind(capture, rift, run, a, b, times, side, enemy_laners, jungler, support,
                   t0, t1, kills, wards) -> str:
    """Tipo de tramo juntos, por prioridad: lvl1 > objective > invade > gank >
    vision > other."""
    if t0 < 90:
        return "lvl1"
    kinds = [rift.cells[cid] for i in run for cid in (a[i], b[i])]
    if capture.events is not None:
        objectives = [e for e in capture.get_events("monster_kill", t0_s=t0, t1_s=t1 + 2)
                      if e["pid"] in (jungler, support)
                      and e["detail"].get("monster") in ("dragon", "baron", "riftherald",
                                                         "voidgrub", "scuttlecrab", "atakhan")]
        if objectives:
            return "objective"
    if any(c["kind"] == "pit" for c in kinds):
        return "objective"
    if any(c["kind"] == "jungle" and c["owner"] not in (None, side) for c in kinds):
        return "invade"
    lane_ticks = [i for i in run if rift.cells[a[i]]["kind"] == "lane"]
    for i in lane_ticks:
        area = rift.cells[a[i]]["area"]
        for enemy in enemy_laners:
            pos = capture.positions.at(enemy, times[i])
            if pos and pos["alive"] is not False and rift.area_at(pos["x"], pos["y"]) == area:
                return "gank"
    if kills:
        return "gank"
    if wards:
        return "vision"
    return "other"

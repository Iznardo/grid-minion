"""Paquete de openings tras backear: qué hace cada jugador al volver de base."""
from typing import Any, Collection, Dict, List, Optional

from .. import spatial
from ..capture import GameCapture
from ._common import (
    annotated_path, level_at, player_info, require_sections, zone_at, zone_sequence,
)

SCHEMA = "grid-minion/package/recall_openings/v1"


def recall_openings(
    capture: GameCapture,
    window_s: float = 60,
    pids: Optional[Collection[int]] = None,
    until_s: Optional[float] = None,
    max_shop_s: float = 45,
    arrive_radius: float = 1200,
) -> Dict[str, Any]:
    """
    Por cada recall completado: qué compró el jugador y hacia dónde salió.

    Args:
        capture: Captura con posiciones y eventos.
        window_s: Segundos de trayectoria a guardar desde que sale de base.
        pids: Jugadores a incluir. `None` = todos.
        until_s: Solo recalls completados antes de ese segundo de juego.
        max_shop_s: Si el jugador no sale de base en este tiempo (AFK, pausa),
            las compras se cortan aquí y `t_leave_base` queda en `None`.
        arrive_radius: El jugador "ha llegado" cuando está a menos de esta
            distancia del punto donde acaba la ventana.

    Returns:
        ```
        {schema, game, params, recalls: [{
            pid, side, role, champion, name,
            t_start, t_end, level,
            from: {x, y, zone},               # dónde empezó a canalizar
            items_bought: [item_id, ...],     # compras en base tras el recall
            t_leave_base, shop_s,             # cuándo sale y cuánto tarda
            path: [{t, x, y, alive, zone}],   # `window_s` segundos tras salir
            zones: [{zone, t_enter}],         # zonas por las que pasa, en orden
            destination: {x, y, zone, t} | None,   # dónde está al acabar la ventana
            travel_s,                         # desde que sale hasta llegar a ese punto
            teleport: {t, spell} | None,      # TP usado tras el recall
            died: bool,                       # murió dentro de la ventana
        }]}
        ```
        Los recalls interrumpidos no se incluyen. El oro disponible no está
        en la captura base (posiciones + eventos): lo aportará el observer de
        stats.
    """
    require_sections(capture, "positions", "events")
    wanted = sorted(pids) if pids is not None else sorted(capture.players)

    recalls: List[Dict[str, Any]] = []
    for pid in wanted:
        for end in capture.get_events("channel_end", pid=pid, t1_s=until_s):
            if end["detail"].get("type") != "recall" or end["detail"].get("interrupted"):
                continue
            recalls.append(_one_recall(capture, pid, end["t"], window_s, max_shop_s,
                                       arrive_radius))

    recalls.sort(key=lambda r: (r["t_end"], r["pid"]))
    return {
        "schema": SCHEMA,
        "game": dict(capture.game),
        "params": {"window_s": window_s, "until_s": until_s, "max_shop_s": max_shop_s,
                   "arrive_radius": arrive_radius},
        "recalls": recalls,
    }


def _one_recall(capture: GameCapture, pid: int, t_end: float, window_s: float,
                max_shop_s: float, arrive_radius: float) -> Dict[str, Any]:
    info = player_info(capture, pid)
    side = info["side"]

    starts = [e for e in capture.get_events("channel_start", pid=pid, t1_s=t_end)
              if e["detail"].get("type") == "recall"]
    t_start = starts[-1]["t"] if starts else None

    # Primer instante en el que sale de su base después de haber aparecido en
    # ella. La muestra justo al acabar el recall aún puede traer la posición
    # anterior al teletransporte (hay una por segundo): no cuenta como salida.
    t_leave = None
    seen_in_base = False
    for sample in annotated_path(capture, pid, t_end, t_end + max_shop_s):
        if sample["alive"] is False:
            continue
        if spatial.is_own_base(sample["zone"], side):
            seen_in_base = True
        elif seen_in_base:
            t_leave = sample["t"]
            break

    shop_until = t_leave if t_leave is not None else t_end + max_shop_s
    items = [e["detail"].get("item_id")
             for e in capture.get_events("item_purchased", pid=pid, t0_s=t_end, t1_s=shop_until)]

    path: List[Dict[str, Any]] = []
    zones: List[Dict[str, Any]] = []
    destination = None
    travel_s = None
    if t_leave is not None:
        path = annotated_path(capture, pid, t_leave, t_leave + window_s)
        zones = zone_sequence(path)
        if path:
            last = path[-1]
            destination = {"x": last["x"], "y": last["y"], "zone": last["zone"], "t": last["t"]}
            arrived = next(s for s in path if spatial.distance(s, last) <= arrive_radius)
            travel_s = round(arrived["t"] - t_leave, 3)

    t_window_end = (t_leave if t_leave is not None else t_end) + window_s
    teleport = None
    for event in capture.get_events("summoner_used", pid=pid, t0_s=t_end, t1_s=t_window_end):
        if "teleport" in str(event["detail"].get("spell") or "").lower():
            teleport = {"t": event["t"], "spell": event["detail"].get("spell")}
            break

    died = any(e["detail"].get("victim") == pid
               for e in capture.get_events("kill", t0_s=t_end, t1_s=t_window_end))

    return {
        **info,
        "t_start": t_start,
        "t_end": t_end,
        "level": level_at(capture, pid, t_end),
        "from": zone_at(capture, pid, t_start) if t_start is not None else None,
        "items_bought": items,
        "t_leave_base": t_leave,
        "shop_s": round(t_leave - t_end, 3) if t_leave is not None else None,
        "path": path,
        "zones": zones,
        "destination": destination,
        "travel_s": travel_s,
        "teleport": teleport,
        "died": died,
    }

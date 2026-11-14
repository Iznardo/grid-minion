"""Utilidades compartidas por los paquetes de scouting."""
from typing import Any, Callable, Dict, List, Optional

from .. import spatial
from ..capture import GameCapture
from ..exceptions import GridDataError


def require_sections(capture: GameCapture, *sections: str):
    for section in sections:
        if getattr(capture, section) is None:
            raise GridDataError(
                f"Este paquete necesita la sección '{section}' en la captura.")


def player_info(capture: GameCapture, pid: int) -> Dict[str, Any]:
    meta = capture.players.get(pid, {})
    return {
        "pid": pid,
        "side": meta.get("side"),
        "role": meta.get("role"),
        "champion": meta.get("champion"),
        "name": meta.get("name"),
    }


def annotated_path(capture: GameCapture, pid: int, t0_s: float, t1_s: float
                   ) -> List[Dict[str, Any]]:
    """Tramo de trayectoria con la zona de cada punto: `[{t, x, y, alive, zone}]`."""
    path = capture.positions.between(pid, t0_s, t1_s)
    for sample in path:
        sample["zone"] = spatial.zone_of(sample["x"], sample["y"])
    return path


def zone_at(capture: GameCapture, pid: int, t_s: float) -> Optional[Dict[str, Any]]:
    """Posición y zona del jugador en `t_s`: `{x, y, zone}` o `None`."""
    pos = capture.positions.at(pid, t_s)
    if pos is None:
        return None
    return {"x": pos["x"], "y": pos["y"], "zone": spatial.zone_of(pos["x"], pos["y"])}


def runs(samples: List[Dict[str, Any]], predicate: Callable[[Dict[str, Any]], bool],
         min_duration_s: float = 0.0, max_gap_s: float = 0.0
         ) -> List[List[Dict[str, Any]]]:
    """Tramos consecutivos de `samples` que cumplen `predicate`.

    Dos tramos separados por un hueco de hasta `max_gap_s` se unen (el feed
    da una muestra por segundo: un jugador en el borde de una zona parpadea).
    Se descartan los tramos más cortos que `min_duration_s`.
    """
    found: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for sample in samples:
        if predicate(sample):
            if (not current and found
                    and sample["t"] - found[-1][-1]["t"] <= max_gap_s):
                current = found.pop()
            current.append(sample)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return [run for run in found if run[-1]["t"] - run[0]["t"] >= min_duration_s]


def level_at(capture: GameCapture, pid: int, t_s: float) -> int:
    """Nivel del jugador en `t_s` (todos empiezan en nivel 1)."""
    levels = [e["detail"].get("level") or 1
              for e in capture.get_events("level_up", pid=pid, t1_s=t_s)]
    return max(levels) if levels else 1


def slim_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Copia del evento con su zona, lista para meter en un paquete."""
    out = dict(event)
    out["detail"] = dict(event.get("detail") or {})
    if event.get("x") is not None and event.get("y") is not None:
        out["zone"] = spatial.zone_of(event["x"], event["y"])
    return out


def zone_sequence(path: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Zonas por las que pasa una trayectoria, en orden: `[{zone, t_enter}]`."""
    sequence: List[Dict[str, Any]] = []
    for sample in path:
        if not sequence or sequence[-1]["zone"] != sample["zone"]:
            sequence.append({"zone": sample["zone"], "t_enter": sample["t"]})
    return sequence

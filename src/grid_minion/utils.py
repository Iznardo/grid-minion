import logging
from typing import List, Dict, Any
from collections import Counter

logger = logging.getLogger(__name__)

def split_grid_series(events: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Divide una serie completa de GRID en bloques de partidas individuales.
    
    Utiliza una lógica de detección basada en eventos de Draft (picks/bans) y 
    eventos de inicio de partida para determinar cuándo termina una partida y 
    empieza la siguiente.

    Args:
        events (List[Dict[str, Any]]): Lista de eventos crudos de una serie de GRID.

    Returns:
        List[List[Dict[str, Any]]]: Una lista donde cada elemento es una lista de 
            eventos pertenecientes a una sola partida.
    """
    games = []
    current_game = []
    game_in_progress = False

    for wrapper_event in events:
        sub_events = wrapper_event.get("events", []) if "events" in wrapper_event else [wrapper_event]
        contains_draft = False
        contains_start = False
        
        for sub_ev in sub_events:
            ev_type = sub_ev.get("type", "")
            if "banned-character" in ev_type or "picked-character" in ev_type:
                contains_draft = True
            if ev_type in ["series-started-game", "game-started"]:
                contains_start = True

        if contains_draft and game_in_progress:
            if current_game:
                games.append(current_game)
            current_game = []
            game_in_progress = False
        
        if contains_start:
            game_in_progress = True

        current_game.append(wrapper_event)

    if current_game:
        games.append(current_game)

    return games


def _sub_events(wrapper_event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return wrapper_event.get("events", []) if "events" in wrapper_event else [wrapper_event]


def split_grid_series_detailed(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Variante diagnóstica de `split_grid_series`.

    Mantiene los mismos cortes, pero devuelve metadatos útiles para feeds LPL:
    invalidaciones, abortos, starts y si el draft observado parece completo.
    """
    games = split_grid_series(events)
    detailed = []

    for index, game_events in enumerate(games, start=1):
        flat = []
        for wrapper in game_events:
            flat.extend(_sub_events(wrapper))

        counts = Counter(str(ev.get("type", "")) for ev in flat)
        pick_events = sum(v for k, v in counts.items() if "picked-character" in k and "!" not in k)
        ban_events = sum(v for k, v in counts.items() if "banned-character" in k and "!" not in k)
        undo_pick_events = sum(v for k, v in counts.items() if "team-!picked-character" in k)
        undo_ban_events = sum(v for k, v in counts.items() if "team-!banned-character" in k)
        effective_picks = max(0, pick_events - undo_pick_events)
        effective_bans = max(0, ban_events - undo_ban_events)

        detailed.append({
            "game_number": index,
            "events": game_events,
            "diagnostics": {
                "wrappers": len(game_events),
                "subevents": len(flat),
                "starts": counts.get("series-started-game", 0) + counts.get("game-started", 0),
                "invalidations": counts.get("grid-invalidated-series", 0),
                "aborts": counts.get("game-aborted", 0),
                "pick_events": pick_events,
                "ban_events": ban_events,
                "undo_pick_events": undo_pick_events,
                "undo_ban_events": undo_ban_events,
                "draft_complete_by_count": effective_picks >= 10 and effective_bans >= 10,
            }
        })

    return detailed


def _fragment_time_bounds(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    times = [
        event.get("gameTime")
        for event in events
        if isinstance(event, dict) and isinstance(event.get("gameTime"), (int, float))
    ]
    return {
        "first_ms": min(times) if times else None,
        "last_ms": max(times) if times else None,
    }


def _has_schema(events: List[Dict[str, Any]], schema: str) -> bool:
    return any(
        (event.get("rfc461Schema") or event.get("eventType")) == schema
        for event in events
        if isinstance(event, dict)
    )


def group_riot_livestats_fragments(
    fragments: List[Dict[str, Any]],
    expected_games: int = None,
) -> Dict[str, Any]:
    """
    Agrupa fragments Riot LiveStats descargados desde el manifest de GRID.

    Es conservador: empieza un nuevo grupo cuando ve `game_info` tras tener
    eventos acumulados o cuando el tiempo vuelve cerca de cero. Si no puede
    cuadrar con `expected_games`, baja la confianza y deja diagnóstico.
    """
    groups: List[Dict[str, Any]] = []
    current = None
    last_ms = None

    for fragment in sorted(fragments, key=lambda f: f.get("game_number", 0)):
        events = fragment.get("events") or []
        if not events:
            continue
        bounds = _fragment_time_bounds(events)
        starts_new_game = False
        if current is None:
            starts_new_game = True
        elif _has_schema(events, "game_info"):
            starts_new_game = True
        elif (
            bounds["first_ms"] is not None
            and last_ms is not None
            and last_ms >= 60000
            and bounds["first_ms"] <= 10000
        ):
            starts_new_game = True

        if starts_new_game:
            current = {"fragments": [], "events": []}
            groups.append(current)

        current["fragments"].append(fragment.get("game_number"))
        current["events"].extend(events)
        if bounds["last_ms"] is not None:
            last_ms = bounds["last_ms"]

    diagnostics = []
    for index, group in enumerate(groups, start=1):
        bounds = _fragment_time_bounds(group["events"])
        diagnostics.append({
            "game_number": index,
            "fragments": list(group["fragments"]),
            "events": len(group["events"]),
            "first_ms": bounds["first_ms"],
            "last_ms": bounds["last_ms"],
            "has_game_info": _has_schema(group["events"], "game_info"),
            "has_game_end": _has_schema(group["events"], "game_end"),
        })

    confidence = "high"
    if expected_games is not None and len(groups) != expected_games:
        confidence = "low"
    elif any(not item["has_game_info"] for item in diagnostics):
        confidence = "medium"

    return {
        "games": [group["events"] for group in groups],
        "diagnostics": diagnostics,
        "confidence": confidence,
    }

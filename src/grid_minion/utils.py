import logging
from typing import List, Dict, Any

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

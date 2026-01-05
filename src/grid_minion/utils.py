from typing import List, Dict, Any

def split_grid_series(events: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Divide la serie en las diferentes partidas basándose en el ciclo: Draft -> Juego -> Fin.
    """
    games = []
    current_game = []
    
    # Estado para saber si estamos "jugando" actualmente
    game_in_progress = False

    for wrapper_event in events:
        # 1. Extraer los sub-eventos reales de esta línea
        # GRID suele mandar una lista bajo la clave "events". Si no existe, usamos el wrapper tal cual.
        sub_events = wrapper_event.get("events", []) if "events" in wrapper_event else [wrapper_event]
        
        # 2. Analizar si esta línea contiene eventos clave (Draft o Start)
        contains_draft = False
        contains_start = False
        
        for sub_ev in sub_events:
            ev_type = sub_ev.get("type", "")
            
            if "banned-character" in ev_type or "picked-character" in ev_type:
                contains_draft = True
            
            if ev_type in ["series-started-game", "game-started"]:
                contains_start = True

        # --- LÓGICA DE CORTE ---
        
        # Si aparece un Draft Y ya estábamos en una partida en curso -> NUEVA PARTIDA
        if contains_draft and game_in_progress:
            if current_game:
                games.append(current_game)
            
            current_game = []
            game_in_progress = False # Reseteamos estado
        
        # Si aparece el Start, marcamos que el juego está en marcha
        if contains_start:
            game_in_progress = True

        # Añadimos la línea completa al juego actual
        current_game.append(wrapper_event)

    # Guardar el último bloque al terminar
    if current_game:
        games.append(current_game)

    return games
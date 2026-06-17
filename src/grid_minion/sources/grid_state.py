from typing import Dict, Any, Optional, List


def _side(raw: Optional[str]) -> str:
    value = str(raw or "").upper()
    if value == "BLUE":
        return "BLUE"
    if value == "RED":
        return "RED"
    return "UNKNOWN"


def _riot_team_id(side: str) -> int:
    return 100 if side == "BLUE" else 200


def _participant_base(side: str) -> int:
    return 1 if side == "BLUE" else 6


def extract_grid_game_state(grid_state: Optional[Dict[str, Any]], game_number: int) -> Optional[Dict[str, Any]]:
    """
    Extrae un `GameState` concreto desde el end-state de GRID.

    Acepta tanto el payload completo (`{"seriesState": {...}}`) como el propio
    `seriesState`. Si se le pasa directamente un `GameState`, lo devuelve tal cual.
    """
    if not grid_state:
        return None
    root = grid_state.get("seriesState", grid_state)
    games = root.get("games")
    if not games:
        return root

    for game in games:
        seq = game.get("sequenceNumber")
        if seq == game_number or str(seq) == str(game_number):
            return game

    index = game_number - 1
    if 0 <= index < len(games):
        return games[index]
    return None


def _participant(player: Dict[str, Any], team: Dict[str, Any], index: int) -> Dict[str, Any]:
    side = _side(team.get("side"))
    character = player.get("character") or {}
    name = player.get("name") or player.get("nickname") or "Unknown"
    return {
        "participantId": _participant_base(side) + index,
        "teamId": _riot_team_id(side),
        "riotIdGameName": name,
        "summonerName": name,
        "championName": character.get("name") or "Unknown",
        "championId": None,
        "puuid": "",
        "grid_player_id": str(player.get("id")) if player.get("id") is not None else None,
        "grid_team_id": str(team.get("id")) if team.get("id") is not None else None,
        "source": "GRID_GAME_STATE",
    }


def _draft_from_actions(actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not actions:
        return None

    ordered = sorted(actions, key=lambda a: int(a.get("sequenceNumber", 0) or 0))
    team_order: List[str] = []
    sides = {}
    for action in ordered:
        drafter = action.get("drafter") or {}
        team_id = str(drafter.get("id"))
        if team_id and team_id not in team_order:
            team_order.append(team_id)
        if team_id:
            sides.setdefault(team_id, {
                "team_id": team_id,
                "picks": [],
                "bans": [],
            })

    if len(team_order) < 2:
        return None

    for action in ordered:
        team_id = str((action.get("drafter") or {}).get("id"))
        draftable = action.get("draftable") or {}
        champion = draftable.get("name")
        if not team_id or team_id not in sides:
            continue
        if action.get("type") == "pick":
            sides[team_id]["picks"].append(champion)
        elif action.get("type") == "ban":
            sides[team_id]["bans"].append(champion)

    first = sides[team_order[0]]
    second = sides[team_order[1]]
    return {
        "draft_found": bool(first["picks"] or first["bans"] or second["picks"] or second["bans"]),
        "is_complete": len(first["picks"]) == 5 and len(second["picks"]) == 5,
        "fp": first,
        "sp": second,
        "source": "grid_game_state",
    }


def normalize_grid_game_state(game_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normaliza un `GameState` de GRID a una forma interna para observers."""
    if not game_state:
        return {"source": "GRID_GAME_STATE", "participants": [], "teams": []}

    participants = []
    teams = []
    for team in game_state.get("teams", []) or []:
        side = _side(team.get("side"))
        teams.append({
            "teamId": _riot_team_id(side),
            "side": side,
            "grid_team_id": str(team.get("id")) if team.get("id") is not None else None,
            "name": team.get("name"),
            "won": team.get("won"),
        })
        for idx, player in enumerate(team.get("players", []) or []):
            participants.append(_participant(player, team, idx))

    version = None
    title_version = game_state.get("titleVersion") or {}
    if title_version.get("name"):
        version = str(title_version["name"])

    return {
        "source": "GRID_GAME_STATE",
        "sequence_number": game_state.get("sequenceNumber"),
        "started": game_state.get("started"),
        "finished": game_state.get("finished"),
        "version": version,
        "teams": teams,
        "participants": participants,
        "draft": _draft_from_actions(game_state.get("draftActions", []) or []),
    }

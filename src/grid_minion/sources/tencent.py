from typing import Dict, Any, List, Optional


ROLE_ORDER = {
    "TOP": 0,
    "JUN": 1,
    "JUNGLE": 1,
    "MID": 2,
    "BOT": 3,
    "ADC": 3,
    "SUP": 4,
    "SUPPORT": 4,
}


def _team_side(team: Dict[str, Any]) -> str:
    raw = str(team.get("teamSide", "")).strip().upper()
    if raw == "BLUE":
        return "BLUE"
    if raw == "RED":
        return "RED"
    return "UNKNOWN"


def _riot_team_id(side: str) -> Optional[int]:
    if side == "BLUE":
        return 100
    if side == "RED":
        return 200
    return None


def _participant_base(side: str) -> Optional[int]:
    if side == "BLUE":
        return 1
    if side == "RED":
        return 6
    return None


def _id_key(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _item_id(item: Optional[Dict[str, Any]]) -> Optional[int]:
    if not item:
        return None
    item_id = item.get("itemId")
    return int(item_id) if item_id else None


def _final_items(player: Dict[str, Any]) -> List[int]:
    items = [_item_id(item) for item in player.get("items", [])]
    trinket = _item_id(player.get("trinketItem"))
    if trinket:
        items.append(trinket)
    return [int(item) for item in items if item]


def _collapse_runes(player: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    runes = [r.get("runeId") for r in player.get("perkRunes", []) if r.get("runeId")]
    if not runes and not player.get("perkStyle") and not player.get("perkSubStyle"):
        return None
    return {
        "primary_style": (player.get("perkStyle") or {}).get("styleId"),
        "primary": [int(r) for r in runes[:4]],
        "sub_style": (player.get("perkSubStyle") or {}).get("styleId"),
        "sub": [int(r) for r in runes[4:6]],
        "stat_perks": [int(r) for r in runes[6:9]],
    }


def _ordered_players(team: Dict[str, Any]) -> List[Dict[str, Any]]:
    players = list(team.get("playerInfos", []) or [])
    return sorted(
        players,
        key=lambda p: (
            ROLE_ORDER.get(str(p.get("role") or p.get("playerLocation") or "").upper(), 99),
            p.get("globalPickOrderDefault", p.get("globalPickOrder", 99)),
        ),
    )


def _participant(player: Dict[str, Any], team: Dict[str, Any], index: int) -> Dict[str, Any]:
    side = _team_side(team)
    battle = player.get("battleDetail") or {}
    damage = player.get("damageDetail") or {}
    other = player.get("otherDetail") or {}
    vision = player.get("visionDetail") or {}
    participant_base = _participant_base(side)
    participant_id = participant_base + index if participant_base is not None else None
    return {
        "participantId": participant_id,
        "teamId": _riot_team_id(side),
        "riotIdGameName": player.get("playerName") or "Unknown",
        "summonerName": player.get("playerName") or "Unknown",
        "championName": player.get("heroNameEn") or player.get("heroName") or "Unknown",
        "championId": player.get("heroId"),
        "puuid": "",
        "role": player.get("role") or player.get("playerLocation"),
        "tencent_player_id": player.get("playerId"),
        "tencent_team_id": team.get("teamId"),
        "kills": int(battle.get("kills", 0) or 0),
        "deaths": int(battle.get("death", 0) or 0),
        "assists": int(battle.get("assist", 0) or 0),
        "gold": int(other.get("golds", 0) or 0),
        "cs": int((other.get("creepsKilled", player.get("minionKilled", 0)) or 0)
                  + (other.get("totalNeutralMinKilled", 0) or 0)),
        "damage_dealt": int(damage.get("heroDamage", 0) or 0),
        "vision_score": int(vision.get("visionScore", 0) or 0),
        "runes": _collapse_runes(player),
        "final_items": _final_items(player),
        # spell1Id/spell2Id ya son IDs de Data Dragon (4=Flash, 12=TP...).
        "summoner_spells": [s for s in (player.get("spell1Id"), player.get("spell2Id")) if s],
        "source": "TENCENT_DETAILS",
    }


def _champion_ref(name: Optional[str], numeric_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if name is None and numeric_id is None:
        return None
    return {"name": name, "id": int(numeric_id) if numeric_id else None}


def _draft_side(team: Dict[str, Any]) -> Dict[str, Any]:
    players = sorted(
        list(team.get("playerInfos", []) or []),
        key=lambda p: p.get("globalPickOrder", p.get("globalPickOrderDefault", 99)),
    )
    bans = [
        _champion_ref(None, champ_id)
        for _, champ_id in sorted(
            zip(team.get("globalBanOrderDefault", []) or [], team.get("banHeroList", []) or []),
            key=lambda pair: pair[0],
        )
    ]
    picks = [
        _champion_ref(player.get("heroNameEn") or player.get("heroName"), player.get("heroId"))
        for player in players
    ]
    return {
        "team_id": str(team.get("teamId")),
        "side": _team_side(team),
        "picks": picks,
        "bans": bans,
    }


def normalize_tencent_details(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normaliza Tencent details a un end-state interno consumible por observers.

    Tencent no trae `participantId` Riot ni PUUID. Generamos ids sintéticos
    estables: BLUE 1-5 y RED 6-10, ordenados por rol competitivo.
    """
    if not payload:
        return {"source": "TENCENT_DETAILS", "participants": [], "teams": []}

    blue_team_id = payload.get("blueTeam")
    winner_team_id = payload.get("matchWin")
    blue_team_key = _id_key(blue_team_id)
    winner_team_key = _id_key(winner_team_id)
    winner = None
    if winner_team_key is not None and blue_team_key is not None:
        winner = "BLUE" if winner_team_key == blue_team_key else "RED"

    teams = []
    participants = []
    draft_by_team_id = {}
    for team in payload.get("teamInfos", []) or []:
        side = _team_side(team)
        teams.append({
            "teamId": _riot_team_id(side),
            "side": side,
            "tencent_team_id": team.get("teamId"),
            "win": _id_key(team.get("teamId")) == winner_team_key,
            "kills": int(team.get("kills", 0) or 0),
            "gold": int(team.get("golds", 0) or 0),
            "dragons": int(team.get("dragonAmount", 0) or 0),
            "barons": int(team.get("baronAmount", 0) or 0),
            "bans": list(team.get("banHeroList", []) or []),
        })
        for idx, player in enumerate(_ordered_players(team)):
            participants.append(_participant(player, team, idx))
        team_key = _id_key(team.get("teamId"))
        if team_key is not None:
            draft_by_team_id[team_key] = _draft_side(team)

    first_pick_id = _id_key(payload.get("bpFirstTeam"))
    first = draft_by_team_id.get(first_pick_id)
    second = next((d for tid, d in draft_by_team_id.items() if tid != first_pick_id), None)
    draft = None
    if first and second:
        draft = {
            "draft_found": True,
            "is_complete": len(first["picks"]) == 5 and len(second["picks"]) == 5,
            "fp": first,
            "sp": second,
            "source": "tencent_details",
        }

    return {
        "source": "TENCENT_DETAILS",
        "winner": winner,
        "winner_source": "tencent_details" if winner else None,
        "game_duration_s": payload.get("gameTime"),
        "match_status": payload.get("matchStatus"),
        "blue_tencent_team_id": blue_team_id,
        "winner_tencent_team_id": winner_team_id,
        "teams": teams,
        "participants": participants,
        "draft": draft,
    }

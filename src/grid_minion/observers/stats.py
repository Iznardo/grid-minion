import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)

class PostGameObserver(Observer):
    """
    Observador encargado de recopilar las estadísticas finales y el resultado de la partida.
    
    Procesa tanto el Summary de Riot (fuente definitiva) como los eventos
    de LiveStats para estimar el ganador en caso de que el Summary no esté disponible.
    """
    def __init__(self):
        self.stats: Dict[int, Dict[str, Any]] = {}
        self.winner: Optional[str] = None
        self.game_version: str = "Unknown"
        self._has_summary = False
        self._winner_source: Optional[str] = None

    def notify_event(self, event: Dict[str, Any]):
        """Procesa el fin de partida y los updates de estadísticas."""
        # 1. RIOT SUMMARY (Prioridad Alta)
        if event.get("source") == "RIOT_SUMMARY":
            payload = event.get("payload", {})
            self._process_summary(payload)
            self._has_summary = True
            return

        # 2. RIOT LIVESTATS (Prioridad Media)
        if not self._has_summary:
            rfc_type = event.get("rfc461Schema")
            event_type = event.get("eventType")
            if rfc_type == "game_end" or event_type == "game_end":
                self._process_game_end(event)
            elif rfc_type == "stats_update" or event_type == "stats_update":
                self._process_live_stats_update(event)
            elif rfc_type == "game_info" or event_type == "game_info":
                self._process_live_game_info(event)

    def _process_summary(self, payload: Dict[str, Any]):
        """Extrae estadísticas finales del Riot Summary."""
        raw_version = payload.get("gameVersion", "")
        if raw_version:
            self.game_version = ".".join(raw_version.split(".")[:2])

        teams = payload.get("teams", [])
        for team in teams:
            win_val = team.get("win")
            if win_val is True or str(win_val).lower() == "win":
                self.winner = "BLUE" if team.get("teamId") == 100 else "RED"
                self._winner_source = "summary"
                break
        self._process_participants_stats(payload.get("participants", []), source="SUMMARY")

    def _process_game_end(self, event: Dict[str, Any]):
        """Extrae el ganador del evento game_end de Riot LiveStats."""
        winning_team = event.get("winningTeam")
        if winning_team is not None:
            self.winner = "BLUE" if winning_team == 100 else "RED"
            self._winner_source = "game_end"

    def _process_live_stats_update(self, event: Dict[str, Any]):
        """Actualiza estadísticas basándose en eventos del timeline."""
        self._process_participants_stats(event.get("participants", []), source="LIVESTATS")
        # Solo usamos el oro como fallback si no tenemos un ganador más fiable
        if self._winner_source != "game_end":
            teams_data = event.get("teams", [])
            blue_gold = 0
            red_gold = 0
            for t in teams_data:
                tid = t.get("teamID") or t.get("teamId")
                gold = t.get("totalGold", 0)
                if tid == 100: blue_gold = gold
                elif tid == 200: red_gold = gold
            self.winner = "BLUE" if blue_gold > red_gold else "RED"
            self._winner_source = "gold_heuristic"

    def _process_live_game_info(self, event: Dict[str, Any]):
        raw = event.get("gameVersion")
        if raw:
            self.game_version = ".".join(raw.split(".")[:2])

    def _process_participants_stats(self, participants: List[Dict], source: str):
        """Normaliza las estadísticas de los participantes desde diferentes fuentes."""
        for p in participants:
            pid = p.get("participantId") or p.get("participantID")
            if pid is None: continue

            if source == "SUMMARY":
                kills = p.get("kills", p.get("championsKilled", 0))
                deaths = p.get("deaths", 0)
                assists = p.get("assists", 0)
                gold = p.get("goldEarned", 0)
                cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
                dmg = p.get("totalDamageDealtToChampions", 0)
            else:
                stats_list = p.get("stats", [])
                stats_dict = {}
                if isinstance(stats_list, list):
                    for s in stats_list:
                        stats_dict[s.get("name")] = s.get("value", 0)
                kills = stats_dict.get("CHAMPIONS_KILLED", 0)
                deaths = stats_dict.get("NUM_DEATHS", 0)
                assists = stats_dict.get("ASSISTS", 0)
                gold = p.get("totalGold", p.get("currentGold", 0))
                cs = stats_dict.get("MINIONS_KILLED", 0) + stats_dict.get("NEUTRAL_MINIONS_KILLED", 0)
                dmg = stats_dict.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0)

            self.stats[pid] = {
                "kills": int(kills),
                "deaths": int(deaths),
                "assists": int(assists),
                "gold": int(gold),
                "cs": int(cs),
                "damage_dealt": int(dmg),
                "source": source
            }

    def get_game_stats(self, teams_observer: Optional[TeamsObserver] = None) -> Dict[str, Any]:
        """
        Devuelve el informe final de estadísticas de la partida.

        Args:
            teams_observer (Optional[TeamsObserver]): Si se proporciona, incluye nombres
                y campeones de los jugadores en el informe.

        Returns:
            Dict[str, Any]: Diccionario con metadatos y estadísticas por jugador.
        """
        result = {
            "meta": {
                "winner": self.winner,
                "version": self.game_version,
                "source": "SUMMARY" if self._has_summary else "LIVESTATS",
                "winner_source": self._winner_source
            },
            "players": {}
        }
        for pid, data in self.stats.items():
            entry = data.copy()
            if teams_observer:
                entry["name"] = teams_observer.get_player_name(pid)
                entry["side"] = teams_observer.get_player_team(pid)
                p_obj = teams_observer.get_player_by_id(pid)
                entry["champion"] = p_obj.champion_name if p_obj else "?"
            entry["kda_str"] = f"{entry['kills']}/{entry['deaths']}/{entry['assists']}"
            result["players"][pid] = entry
        return result

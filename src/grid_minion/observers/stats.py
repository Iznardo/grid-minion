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
        self._has_tencent = False
        self._winner_source: Optional[str] = None

    def notify_event(self, event: Dict[str, Any]):
        """Procesa el fin de partida y los updates de estadísticas."""
        # 1. GRID GAME STATE (contexto LPL: versión, sin asumir ganador)
        if event.get("source") == "GRID_GAME_STATE":
            payload = event.get("payload", {})
            if payload.get("version") and self.game_version == "Unknown":
                self.game_version = str(payload["version"])
            return

        # 2. TENCENT DETAILS (end-state LPL si no hay Riot Summary)
        if event.get("source") == "TENCENT_DETAILS":
            if not self._has_summary:
                payload = event.get("payload", {})
                self._process_tencent_details(payload)
                self._has_tencent = True
            return

        # 3. RIOT SUMMARY (Prioridad Alta)
        if event.get("source") == "RIOT_SUMMARY":
            payload = event.get("payload", {})
            self._process_summary(payload)
            self._has_summary = True
            return

        # 4. RIOT LIVESTATS (fallback si no hay end-state autoritativo)
        if not self._has_summary and not self._has_tencent:
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

    def _process_tencent_details(self, payload: Dict[str, Any]):
        """Extrae estadísticas finales desde el end-state normalizado de Tencent."""
        if payload.get("winner"):
            self.winner = payload["winner"]
            self._winner_source = "tencent_details"
        self._process_participants_stats(payload.get("participants", []), source="TENCENT_DETAILS")

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
            blue_gold = None
            red_gold = None
            for t in teams_data:
                tid = t.get("teamID") or t.get("teamId")
                gold = t.get("totalGold", 0)
                if str(tid) == "100":
                    blue_gold = gold
                elif str(tid) == "200":
                    red_gold = gold
            if blue_gold is None or red_gold is None or blue_gold == red_gold:
                return
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
                # runes y final_items solo existen en el summary.
                runes = self._collapse_runes(p.get("perks"))
                final_items = self._final_items(p)
            elif source == "TENCENT_DETAILS":
                kills = p.get("kills", 0)
                deaths = p.get("deaths", 0)
                assists = p.get("assists", 0)
                gold = p.get("gold", 0)
                cs = p.get("cs", 0)
                dmg = p.get("damage_dealt", 0)
                runes = p.get("runes")
                final_items = p.get("final_items")
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
                # Sin summary no hay runas ni build final fiables: no se inventan.
                runes = None
                final_items = None

            self.stats[pid] = {
                "kills": int(kills),
                "deaths": int(deaths),
                "assists": int(assists),
                "gold": int(gold),
                "cs": int(cs),
                "damage_dealt": int(dmg),
                "runes": runes,
                "final_items": final_items,
                "source": source
            }

    @staticmethod
    def _final_items(participant: Dict[str, Any]) -> List[int]:
        """Lista de items finales (`item0..item6`) filtrando los slots vacíos (`0`)."""
        items = [participant.get(f"item{i}", 0) for i in range(7)]
        return [int(it) for it in items if it]

    @staticmethod
    def _collapse_runes(perks: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Colapsa el objeto `perks` del summary a la forma fija del contrato.

        `{primary_style, primary, sub_style, sub, stat_perks}`. Devuelve `None`
        si el summary no trae runas.
        """
        if not perks:
            return None
        styles = perks.get("styles", []) or []
        primary = next((s for s in styles if s.get("description") == "primaryStyle"), {})
        sub = next((s for s in styles if s.get("description") == "subStyle"), {})
        stat = perks.get("statPerks", {}) or {}
        return {
            "primary_style": primary.get("style"),
            "primary": [sel.get("perk") for sel in primary.get("selections", [])],
            "sub_style": sub.get("style"),
            "sub": [sel.get("perk") for sel in sub.get("selections", [])],
            "stat_perks": [stat.get("offense"), stat.get("flex"), stat.get("defense")],
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
                "source": (
                    "SUMMARY" if self._has_summary
                    else "TENCENT_DETAILS" if self._has_tencent
                    else "LIVESTATS"
                ),
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
                # Id numérico de Riot para cruzar por entero (paridad con SoloQ).
                entry["champion_id"] = p_obj.champion_id if p_obj else None
            entry["kda_str"] = f"{entry['kills']}/{entry['deaths']}/{entry['assists']}"
            result["players"][pid] = entry
        return result

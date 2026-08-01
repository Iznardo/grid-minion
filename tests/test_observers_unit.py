import unittest
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver,
    PostGameObserver, ObjectiveKilledObserver, WardsObserver, BuildObserver,
    MidGameStatsObserver, SoloKillObserver,
    PlayerTimelineObserver, CombatObserver, WardEventsObserver,
    BuildingObserver, ObjectiveSpawnObserver, MobilityObserver,
)
from grid_minion.champions import ChampionResolver, set_default_resolver
from grid_minion.sources import normalize_grid_game_state, normalize_tencent_details


# ---------------------------------------------------------------------------
# TeamsObserver
# ---------------------------------------------------------------------------

class TestTeamsObserver(unittest.TestCase):

    def _make_summary_event(self, participants):
        return {"source": "RIOT_SUMMARY", "payload": {"participants": participants}}

    def test_participants_from_summary(self):
        obs = TeamsObserver()
        obs.notify_event(self._make_summary_event([
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100, "championName": "Orianna", "puuid": "aaa"},
            {"participantId": 6, "riotIdGameName": "Zeus",  "teamId": 200, "championName": "Gnar",     "puuid": "bbb"},
        ]))
        p1 = obs.get_player_by_id(1)
        p6 = obs.get_player_by_id(6)
        self.assertEqual(p1.summoner_name, "Faker")
        self.assertEqual(p1.champion_name, "Orianna")
        self.assertEqual(p1.team_side, "BLUE")
        self.assertEqual(p6.team_side, "RED")

    def test_get_player_team_uppercase(self):
        obs = TeamsObserver()
        obs.notify_event(self._make_summary_event([
            {"participantId": 1, "riotIdGameName": "P1", "teamId": 100, "championName": "X", "puuid": "p1"},
            {"participantId": 6, "riotIdGameName": "P6", "teamId": 200, "championName": "Y", "puuid": "p6"},
        ]))
        self.assertEqual(obs.get_player_team(1), "BLUE")
        self.assertEqual(obs.get_player_team(6), "RED")
        self.assertEqual(obs.get_player_team(99), "UNKNOWN")

    def test_get_player_name_unknown_id(self):
        obs = TeamsObserver()
        self.assertEqual(obs.get_player_name(99), "Unknown")

    def test_puuid_crossreference(self):
        """Los IDs de GRID se enlazan al jugador correcto via PUUID."""
        obs = TeamsObserver()
        grid_event = {
            "type": "series-started-game",
            "state": {"teams": [{
                "id": "T1",
                "players": [{"id": "999", "externalLinks": [
                    {"dataProvider": {"name": "RIOT_PUUID"},
                     "externalEntity": {"id": "PUUID_FAKER"}}
                ]}]
            }]}
        }
        obs.notify_event({"events": [grid_event]})
        obs.notify_event(self._make_summary_event([
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "puuid_faker"},
        ]))
        p = obs.get_player_by_id(1)
        self.assertEqual(p.grid_player_id, "999")


# ---------------------------------------------------------------------------
# DraftObserver
# ---------------------------------------------------------------------------

class TestDraftObserver(unittest.TestCase):

    def setUp(self):
        set_default_resolver(ChampionResolver.from_mapping({}))

    def tearDown(self):
        set_default_resolver(None)

    def _ban(self, team_id, champ):
        return {"type": "team-banned-character",
                "actor": {"id": team_id},
                "target": {"state": {"name": champ}}}

    def _pick(self, team_id, champ):
        return {"type": "team-picked-character",
                "actor": {"id": team_id},
                "target": {"state": {"name": champ}}}

    def test_basic_ban_and_pick(self):
        obs = DraftObserver()
        obs.notify_event({"events": [self._ban("A", "Orianna")]})
        obs.notify_event({"events": [self._pick("A", "LeBlanc")]})
        draft = obs.get_draft()
        ban_names = [b["name"] for b in draft["fp"]["bans"] if b]
        pick_names = [p["name"] for p in draft["fp"]["picks"]]
        self.assertIn("Orianna", ban_names)
        self.assertIn("LeBlanc", pick_names)

    def test_first_pick_assignment(self):
        """Sin picks en el feed, el fallback es el orden de llegada de bans."""
        obs = DraftObserver()
        obs.notify_event({"events": [self._ban("TEAM_A", "Orianna")]})
        obs.notify_event({"events": [self._ban("TEAM_B", "Ryze")]})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["team_id"], "TEAM_A")
        self.assertEqual(draft["sp"]["team_id"], "TEAM_B")

    def test_fp_from_first_pick_healthy_draft(self):
        """Draft sano (bans primero): FP sigue siendo quien pickea primero."""
        obs = DraftObserver()
        events = (
            [self._ban("A", c) for c in ["A1", "A2", "A3"]]
            + [self._ban("B", c) for c in ["B1", "B2", "B3"]]
            + [self._pick("A", "P1"), self._pick("B", "P2"), self._pick("B", "P3"),
               self._pick("A", "P4"), self._pick("A", "P5"), self._pick("B", "P6")]
            + [self._ban("B", "B4"), self._ban("A", "A4"),
               self._ban("B", "B5"), self._ban("A", "A5")]
            + [self._pick("B", "P7"), self._pick("A", "P8"),
               self._pick("A", "P9"), self._pick("B", "P10")]
        )
        obs.notify_event({"events": events})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["team_id"], "A")
        self.assertEqual(draft["sp"]["team_id"], "B")
        self.assertTrue(draft["is_complete"])
        self.assertEqual([b["name"] for b in draft["fp"]["bans"]],
                         ["A1", "A2", "A3", "A4", "A5"])
        self.assertEqual([b["name"] for b in draft["sp"]["bans"]],
                         ["B1", "B2", "B3", "B4", "B5"])

    def test_fp_with_dropped_opening_bans(self):
        """Serie GRID 2972536: el feed pierde los 3 bans iniciales del azul.

        El primer evento observado es un ban del rojo, pero FP es el azul
        (pickea primero). Ver docs/bug_draft_observer_fp_dropped_bans.md.
        """
        obs = DraftObserver()
        blue, red = "CITA", "GIANTX"
        events = [
            # Los 3 bans de fase 1 del azul nunca llegan.
            self._ban(red, "Orianna"),
            self._ban(red, "Jarvan IV"),
            self._ban(red, "Syndra"),
            self._pick(blue, "Nocturne"),
            self._pick(red, "Cassiopeia"),
            self._pick(red, "Poppy"),
            self._pick(blue, "Bard"),
            self._pick(blue, "Ezreal"),
            self._pick(red, "Nautilus"),
            self._ban(red, "Anivia"),
            self._ban(blue, "KaiSa"),
            self._ban(red, "Viktor"),
            self._ban(blue, "Sivir"),
            self._pick(red, "Jhin"),
            self._pick(blue, "Annie"),
            self._pick(blue, "Jayce"),
            self._pick(red, "Sion"),
        ]
        obs.notify_event({"events": events})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["team_id"], blue)
        self.assertEqual(draft["sp"]["team_id"], red)
        self.assertTrue(draft["is_complete"])
        fp_bans = [b["name"] if b else None for b in draft["fp"]["bans"]]
        sp_bans = [b["name"] if b else None for b in draft["sp"]["bans"]]
        self.assertEqual(fp_bans, [None, None, None, "KaiSa", "Sivir"])
        self.assertEqual(sp_bans,
                         ["Orianna", "Jarvan IV", "Syndra", "Anivia", "Viktor"])
        self.assertEqual([p["name"] for p in draft["fp"]["picks"]],
                         ["Nocturne", "Bard", "Ezreal", "Annie", "Jayce"])
        self.assertEqual([p["name"] for p in draft["sp"]["picks"]],
                         ["Cassiopeia", "Poppy", "Nautilus", "Jhin", "Sion"])

    def test_undo_ban_before_first_pick(self):
        """Los undos previos al primer pick también se bufferean y clasifican."""
        obs = DraftObserver()
        obs.notify_event({"events": [
            self._ban("A", "Orianna"),
            {"type": "team-!banned-character",
             "actor": {"id": "A"},
             "target": {"state": {"name": "Orianna"}}},
            self._ban("A", "Syndra"),
            self._pick("B", "LeBlanc"),
        ]})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["team_id"], "B")
        self.assertEqual(draft["sp"]["team_id"], "A")
        sp_bans = [b["name"] for b in draft["sp"]["bans"] if b]
        self.assertEqual(sp_bans, ["Syndra"])

    def test_fill_skipped_bans(self):
        """Un equipo que pickea sin banear recibe None en sus bans."""
        obs = DraftObserver()
        obs.notify_event({"events": [self._pick("A", "Orianna")]})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["bans"], [None, None, None])

    def test_invalidation_saves_history(self):
        obs = DraftObserver()
        obs.notify_event({"events": [self._ban("A", "Orianna")]})
        obs.notify_event({"events": [{"type": "grid-invalidated-series"}]})
        self.assertEqual(len(obs.draft_history), 1)
        self.assertFalse(obs.draft_found)

    def test_reset_clears_history(self):
        obs = DraftObserver()
        obs.notify_event({"events": [self._ban("A", "Orianna")]})
        obs.reset()
        self.assertFalse(obs.draft_found)
        self.assertEqual(obs.draft_history, [])


# ---------------------------------------------------------------------------
# PostGameObserver
# ---------------------------------------------------------------------------

class TestPostGameObserver(unittest.TestCase):

    def _summary_event(self, winner_team_id):
        return {
            "source": "RIOT_SUMMARY",
            "payload": {
                "gameVersion": "14.1.500",
                "teams": [{"teamId": winner_team_id, "win": True}],
                "participants": []
            }
        }

    def test_winner_from_summary(self):
        obs = PostGameObserver()
        obs.notify_event(self._summary_event(100))
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["winner_source"], "summary")

    def test_winner_from_game_end(self):
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "game_end", "winningTeam": 200})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "RED")
        self.assertEqual(meta["winner_source"], "game_end")

    def test_winner_gold_heuristic(self):
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "gameTime": 700_000,
                          "teams": [{"teamID": 100, "totalGold": 8000},
                                    {"teamID": 200, "totalGold": 5000}]})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["winner_source"], "gold_heuristic")

    def test_gold_heuristic_below_duration_threshold(self):
        """Por debajo del umbral (default 10 min) la señal de oro es demasiado
        débil (p.ej. una scrim abortada casi al empezar) y no decide ganador."""
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "gameTime": 300_000,
                          "teams": [{"teamID": 100, "totalGold": 8000},
                                    {"teamID": 200, "totalGold": 5000}]})
        meta = obs.get_game_stats()["meta"]
        self.assertIsNone(meta["winner"])
        self.assertIsNone(meta["winner_source"])

    def test_gold_heuristic_needs_both_teams(self):
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "teams": []})
        meta = obs.get_game_stats()["meta"]
        self.assertIsNone(meta["winner"])
        self.assertIsNone(meta["winner_source"])

    def test_gold_heuristic_ignores_ties(self):
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "teams": [{"teamID": 100, "totalGold": 8000},
                                    {"teamID": 200, "totalGold": 8000}]})
        meta = obs.get_game_stats()["meta"]
        self.assertIsNone(meta["winner"])
        self.assertIsNone(meta["winner_source"])

    def test_game_end_beats_gold_heuristic(self):
        """game_end debe ganar al oro aunque llegue después."""
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "teams": [{"teamID": 100, "totalGold": 8000},
                                    {"teamID": 200, "totalGold": 5000}]})
        obs.notify_event({"rfc461Schema": "game_end", "winningTeam": 200})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "RED")
        self.assertEqual(meta["winner_source"], "game_end")

    def test_summary_skips_livestats(self):
        """Con summary disponible, los eventos de livestats no sobreescriben el ganador."""
        obs = PostGameObserver()
        obs.notify_event(self._summary_event(100))
        obs.notify_event({"rfc461Schema": "game_end", "winningTeam": 200})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["winner_source"], "summary")

    def _aborted_summary_event(self):
        """Scrim abortada: Riot publica summary sin ganador (win=False ambos
        equipos) y endOfGameResult='Abort_TooFewPlayers'."""
        return {
            "source": "RIOT_SUMMARY",
            "payload": {
                "gameVersion": "14.1.500",
                "endOfGameResult": "Abort_TooFewPlayers",
                "teams": [{"teamId": 100, "win": False},
                          {"teamId": 200, "win": False}],
                "participants": []
            }
        }

    def test_gold_heuristic_after_summary_without_winner(self):
        """Bug 2026-07-11: un summary sin ganador (scrim abortada) no debe
        bloquear la heurística de oro como último recurso."""
        obs = PostGameObserver()
        obs.notify_event(self._aborted_summary_event())
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "gameTime": 867_010,
                          "teams": [{"teamID": 100, "totalGold": 28214},
                                    {"teamID": 200, "totalGold": 23697}]})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["winner_source"], "gold_heuristic")
        self.assertEqual(meta["end_of_game_result"], "Abort_TooFewPlayers")

    def test_gold_heuristic_after_summary_without_winner_below_threshold(self):
        """Misma scrim abortada pero cortada demasiado pronto (<10 min): la
        señal de oro no es fiable y se prefiere no decidir ganador."""
        obs = PostGameObserver()
        obs.notify_event(self._aborted_summary_event())
        obs.notify_event({"rfc461Schema": "stats_update", "participants": [],
                          "gameTime": 537_031,
                          "teams": [{"teamID": 100, "totalGold": 16428},
                                    {"teamID": 200, "totalGold": 14438}]})
        meta = obs.get_game_stats()["meta"]
        self.assertIsNone(meta["winner"])
        self.assertIsNone(meta["winner_source"])

    def test_game_end_after_summary_without_winner(self):
        """Un summary sin ganador seguido de un game_end fiable sí debe
        decidir el ganador (game_end no depende de _has_summary)."""
        obs = PostGameObserver()
        obs.notify_event(self._aborted_summary_event())
        obs.notify_event({"rfc461Schema": "game_end", "winningTeam": 200})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "RED")
        self.assertEqual(meta["winner_source"], "game_end")

    def test_game_version_from_summary(self):
        obs = PostGameObserver()
        obs.notify_event(self._summary_event(100))
        self.assertEqual(obs.get_game_stats()["meta"]["version"], "14.1")

    def test_runes_and_final_items_from_summary(self):
        obs = PostGameObserver()
        obs.notify_event({
            "source": "RIOT_SUMMARY",
            "payload": {
                "teams": [{"teamId": 100, "win": True}],
                "participants": [{
                    "participantId": 1, "teamId": 100,
                    # GRID usa spell1Id/spell2Id (Match-V4), no summoner1Id/2Id.
                    "spell1Id": 4, "spell2Id": 14,
                    "item0": 3047, "item1": 3157, "item2": 0, "item3": 6653,
                    "item4": 4645, "item5": 0, "item6": 3363,
                    "perks": {
                        "statPerks": {"offense": 5008, "flex": 5008, "defense": 5011},
                        "styles": [
                            {"description": "primaryStyle", "style": 8200,
                             "selections": [{"perk": 8229}, {"perk": 8275}]},
                            {"description": "subStyle", "style": 8400,
                             "selections": [{"perk": 8473}, {"perk": 8242}]},
                        ],
                    },
                }],
            },
        })
        player = obs.get_game_stats()["players"][1]
        # final_items: sin los ceros, en orden
        self.assertEqual(player["final_items"], [3047, 3157, 6653, 4645, 3363])
        # runes colapsadas
        self.assertEqual(player["runes"], {
            "primary_style": 8200,
            "primary": [8229, 8275],
            "sub_style": 8400,
            "sub": [8473, 8242],
            "stat_perks": [5008, 5008, 5011],
        })
        # spell1Id/spell2Id -> IDs de Data Dragon
        self.assertEqual(player["summoner_spells"], [4, 14])

    def test_summoner_spells_fallback_to_match_v5_naming(self):
        """Si el summary trae summoner1Id/2Id (Match-V5), también se leen."""
        obs = PostGameObserver()
        obs.notify_event({
            "source": "RIOT_SUMMARY",
            "payload": {
                "teams": [{"teamId": 100, "win": True}],
                "participants": [{
                    "participantId": 1, "teamId": 100,
                    "summoner1Id": 4, "summoner2Id": 12,
                }],
            },
        })
        self.assertEqual(obs.get_game_stats()["players"][1]["summoner_spells"], [4, 12])

    def test_no_runes_without_summary(self):
        """Sin summary, runes/final_items/summoners son None (no se inventan)."""
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update",
                          "participants": [{"participantId": 1, "stats": []}],
                          "teams": []})
        player = obs.get_game_stats()["players"][1]
        self.assertIsNone(player["runes"])
        self.assertIsNone(player["final_items"])
        self.assertIsNone(player["summoner_spells"])


# ---------------------------------------------------------------------------
# Fuentes LPL (Tencent / GRID GameState)
# ---------------------------------------------------------------------------

def _lpl_player(name, role, champ, champ_id, kills=1, deaths=2, assists=3):
    return {
        "playerId": champ_id * 10,
        "playerName": name,
        "role": role,
        "playerLocation": role,
        "globalPickOrder": 1,
        "globalPickOrderDefault": 1,
        "heroId": champ_id,
        "heroNameEn": champ,
        "items": [{"itemId": 1055}, {"itemId": 3031}],
        "trinketItem": {"itemId": 3363},
        "perkStyle": {"styleId": 8000},
        "perkSubStyle": {"styleId": 8300},
        "perkRunes": [
            {"runeId": 8008}, {"runeId": 9111}, {"runeId": 9103},
            {"runeId": 8017}, {"runeId": 8345}, {"runeId": 8347},
            {"runeId": 5005}, {"runeId": 5008}, {"runeId": 5011},
        ],
        "battleDetail": {"kills": kills, "death": deaths, "assist": assists},
        "damageDetail": {"heroDamage": 12345.6},
        "otherDetail": {"golds": 12000, "creepsKilled": 250},
        "visionDetail": {"visionScore": 33},
        "spell1Id": 4, "spell2Id": 12,
    }


def _tencent_details():
    roles = ["TOP", "JUN", "MID", "BOT", "SUP"]
    blue_players = [
        _lpl_player(f"BLUE{i}", role, f"BlueChamp{i}", 10 + i)
        for i, role in enumerate(roles, start=1)
    ]
    red_players = [
        _lpl_player(f"RED{i}", role, f"RedChamp{i}", 20 + i, kills=2)
        for i, role in enumerate(roles, start=1)
    ]
    return {
        "blueTeam": 1,
        "matchWin": 2,
        "matchStatus": 2,
        "gameTime": 1800,
        "bpFirstTeam": 1,
        "teamInfos": [
            {"teamId": 1, "teamSide": "Blue", "kills": 5, "golds": 50000,
             "banHeroList": [101, 102, 103, 104, 105],
             "globalBanOrderDefault": [1, 3, 5, 8, 10],
             "playerInfos": blue_players},
            {"teamId": 2, "teamSide": "Red", "kills": 10, "golds": 60000,
             "banHeroList": [201, 202, 203, 204, 205],
             "globalBanOrderDefault": [2, 4, 6, 7, 9],
             "playerInfos": red_players},
        ],
    }


class TestLPLSources(unittest.TestCase):

    def setUp(self):
        set_default_resolver(ChampionResolver.from_mapping({}))

    def tearDown(self):
        set_default_resolver(None)

    def test_tencent_details_populates_teams_and_stats(self):
        processor = GameEventProcessor()
        teams = TeamsObserver()
        stats = PostGameObserver()
        processor.attach(teams)
        processor.attach(stats)

        processor.process_bundle(tencent_details=_tencent_details())

        p1 = teams.get_player_by_id(1)
        p6 = teams.get_player_by_id(6)
        self.assertEqual(p1.summoner_name, "BLUE1")
        self.assertEqual(p1.team_side, "BLUE")
        self.assertEqual(p6.summoner_name, "RED1")
        self.assertEqual(p6.team_side, "RED")

        report = stats.get_game_stats(teams)
        self.assertEqual(report["meta"]["winner"], "RED")
        self.assertEqual(report["meta"]["source"], "TENCENT_DETAILS")
        self.assertEqual(report["meta"]["winner_source"], "tencent_details")
        self.assertEqual(report["players"][1]["final_items"], [1055, 3031, 3363])
        self.assertEqual(report["players"][1]["runes"]["primary"], [8008, 9111, 9103, 8017])
        self.assertEqual(report["players"][1]["summoner_spells"], [4, 12])

    def test_tencent_winner_accepts_mixed_id_types(self):
        payload = _tencent_details()
        payload["blueTeam"] = "1"
        payload["matchWin"] = 1

        normalized = normalize_tencent_details(payload)

        self.assertEqual(normalized["winner"], "BLUE")
        self.assertTrue(normalized["teams"][0]["win"])

    def test_tencent_unknown_side_does_not_become_red(self):
        payload = {
            "blueTeam": 1,
            "matchWin": 1,
            "teamInfos": [{
                "teamId": 3,
                "teamSide": "GREEN",
                "playerInfos": [_lpl_player("UNKNOWN1", "TOP", "Champ", 1)],
            }],
        }

        normalized = normalize_tencent_details(payload)

        self.assertIsNone(normalized["teams"][0]["teamId"])
        self.assertEqual(normalized["teams"][0]["side"], "UNKNOWN")
        self.assertIsNone(normalized["participants"][0]["participantId"])
        self.assertIsNone(normalized["participants"][0]["teamId"])

    def test_grid_game_state_unknown_side_does_not_become_red(self):
        normalized = normalize_grid_game_state({
            "teams": [{
                "id": "T1",
                "side": "UNKNOWN",
                "players": [{"id": "P1", "name": "Player", "character": {"name": "Ahri"}}],
            }]
        })

        self.assertIsNone(normalized["teams"][0]["teamId"])
        self.assertEqual(normalized["teams"][0]["side"], "UNKNOWN")
        self.assertIsNone(normalized["participants"][0]["participantId"])
        self.assertIsNone(normalized["participants"][0]["teamId"])

    def test_riot_game_info_enriches_puuid_after_tencent(self):
        processor = GameEventProcessor()
        teams = TeamsObserver()
        processor.attach(teams)
        processor.process_bundle(
            tencent_details=_tencent_details(),
            riot_livestats=[{
                "rfc461Schema": "game_info",
                "participants": [
                    {"participantID": 1, "summonerName": "BLUE1", "teamID": 100,
                     "championName": "BlueChamp1", "puuid": "puuid-blue-1"},
                    {"participantID": 6, "summonerName": "RED1", "teamID": 200,
                     "championName": "RedChamp1", "puuid": "puuid-red-1"},
                ],
            }],
        )

        self.assertEqual(teams.get_player_by_id(1).puuid, "puuid-blue-1")
        self.assertEqual(teams.get_player_by_id(6).puuid, "puuid-red-1")

    def test_riot_summary_overrides_tencent_details(self):
        processor = GameEventProcessor()
        stats = PostGameObserver()
        processor.attach(stats)
        processor.process_bundle(
            tencent_details=_tencent_details(),
            riot_summary={
                "teams": [{"teamId": 100, "win": True}],
                "participants": [],
                "gameVersion": "16.10.1",
            },
        )
        meta = stats.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["source"], "SUMMARY")
        self.assertEqual(meta["winner_source"], "summary")

    def test_grid_game_state_draft_is_fallback_when_grid_events_incomplete(self):
        processor = GameEventProcessor()
        draft = DraftObserver()
        processor.attach(draft)
        actions = []
        sequence = 1
        for action_type, team_id, champ in [
            ("ban", "A", "Aatrox"), ("ban", "B", "Ahri"),
            ("ban", "A", "Akali"), ("ban", "B", "Alistar"),
            ("ban", "A", "Amumu"), ("ban", "B", "Anivia"),
            ("pick", "A", "Annie"), ("pick", "B", "Ashe"),
            ("pick", "B", "Azir"), ("pick", "A", "Bard"),
            ("pick", "A", "Blitzcrank"), ("pick", "B", "Brand"),
            ("ban", "B", "Braum"), ("ban", "A", "Caitlyn"),
            ("ban", "B", "Camille"), ("ban", "A", "Cassiopeia"),
            ("pick", "B", "ChoGath"), ("pick", "A", "Corki"),
            ("pick", "A", "Darius"), ("pick", "B", "Diana"),
        ]:
            actions.append({
                "sequenceNumber": str(sequence),
                "type": action_type,
                "drafter": {"id": team_id},
                "draftable": {"name": champ},
            })
            sequence += 1

        processor.process_bundle(
            grid_game_state={"draftActions": actions},
            grid_livestats=[{"events": [
                {"type": "team-banned-character", "actor": {"id": "A"},
                 "target": {"state": {"name": "Aatrox"}}},
                {"type": "team-picked-character", "actor": {"id": "A"},
                 "target": {"state": {"name": "Annie"}}},
            ]}],
        )

        result = draft.get_draft()
        status = draft.get_draft_status()
        self.assertTrue(result["is_complete"])
        self.assertEqual(len(result["fp"]["picks"]), 5)
        self.assertTrue(status["uses_fallback"])
        self.assertEqual(status["fallback_source"], "grid_game_state")


# ---------------------------------------------------------------------------
# BuildObserver
# ---------------------------------------------------------------------------

class TestBuildObserver(unittest.TestCase):

    def _buy(self, pid, item, ts_ms=0, seq=0):
        return {"rfc461Schema": "item_purchased", "participantID": pid,
                "itemID": item, "gameTime": ts_ms}

    def _sell(self, pid, item, ts_ms=0):
        return {"rfc461Schema": "item_sold", "participantID": pid,
                "itemID": item, "gameTime": ts_ms}

    def _undo(self, pid, item, gold_gain):
        return {"rfc461Schema": "item_undo", "participantID": pid,
                "itemID": item, "goldGain": gold_gain}

    def _skill(self, pid, slot, evolved=False):
        # Los eventos de skill usan 'participant', no 'participantID'.
        return {"rfc461Schema": "skill_level_up", "participant": pid,
                "skillSlot": slot, "evolved": evolved}

    def test_build_path_buy_sell_order(self):
        obs = BuildObserver()
        obs.notify_event(self._buy(1, 1054, ts_ms=2000))
        obs.notify_event(self._buy(1, 3044, ts_ms=817000))
        obs.notify_event(self._sell(1, 1054, ts_ms=1547000))
        bp = obs.get_builds()[1]["build_path"]
        self.assertEqual(bp, [
            {"ts_s": 2, "action": "BUY", "item_id": 1054},
            {"ts_s": 817, "action": "BUY", "item_id": 3044},
            {"ts_s": 1547, "action": "SELL", "item_id": 1054},
        ])

    def test_undo_removes_last_buy(self):
        obs = BuildObserver()
        obs.notify_event(self._buy(1, 1054))
        obs.notify_event(self._buy(1, 3044))
        obs.notify_event(self._undo(1, 3044, gold_gain=850))  # deshace la compra
        items = [(e["action"], e["item_id"]) for e in obs.get_builds()[1]["build_path"]]
        self.assertEqual(items, [("BUY", 1054)])

    def test_undo_removes_last_sell(self):
        obs = BuildObserver()
        obs.notify_event(self._buy(1, 1054))
        obs.notify_event(self._sell(1, 1054))
        obs.notify_event(self._undo(1, 1054, gold_gain=-300))  # deshace la venta
        items = [(e["action"], e["item_id"]) for e in obs.get_builds()[1]["build_path"]]
        self.assertEqual(items, [("BUY", 1054)])

    def test_skill_order_excludes_evolved(self):
        obs = BuildObserver()
        obs.notify_event(self._skill(1, 1))            # Q
        obs.notify_event(self._skill(1, 3))            # E
        obs.notify_event(self._skill(1, 1, evolved=True))  # evolución, se excluye
        obs.notify_event(self._skill(1, 4))            # R
        self.assertEqual(obs.get_builds()[1]["skill_order"], "QER")

    def test_mixed_participant_casing(self):
        """Items usan participantID y skills participant: mismo jugador."""
        obs = BuildObserver()
        obs.notify_event(self._buy(7, 1001))
        obs.notify_event(self._skill(7, 2))
        builds = obs.get_builds()
        self.assertIn(7, builds)
        self.assertEqual(builds[7]["build_path"][0]["item_id"], 1001)
        self.assertEqual(builds[7]["skill_order"], "W")

    def test_reset(self):
        obs = BuildObserver()
        obs.notify_event(self._buy(1, 1054))
        obs.reset()
        self.assertEqual(obs.get_builds(), {})


# ---------------------------------------------------------------------------
# ObjectiveKilledObserver
# ---------------------------------------------------------------------------

class TestObjectiveKilledObserver(unittest.TestCase):

    def _obj_event(self, monster_type, team_id, dragon_type=None, game_time=300000):
        e = {"rfc461Schema": "epic_monster_kill",
             "killerTeamID": team_id,
             "monsterType": monster_type,
             "gameTime": game_time,
             "killer": 1}
        if dragon_type:
            e["dragonType"] = dragon_type
        return e

    def test_dragon(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event(self._obj_event("dragon", 100, dragon_type="fire"))
        objs = obs.get_all_objectives()
        self.assertEqual(len(objs["dragons"]), 1)
        self.assertEqual(objs["dragons"][0]["team"], "BLUE")
        self.assertEqual(objs["dragons"][0]["type"], "fire")

    def test_baron(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event(self._obj_event("baron", 200))
        self.assertEqual(len(obs.get_all_objectives()["barons"]), 1)
        self.assertEqual(obs.get_all_objectives()["barons"][0]["team"], "RED")

    def test_herald(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event(self._obj_event("riftherald", 100))
        self.assertEqual(len(obs.get_all_objectives()["heralds"]), 1)

    def test_neutral_team(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event(self._obj_event("dragon", 0, dragon_type="air"))
        self.assertEqual(obs.get_all_objectives()["dragons"][0]["team"], "NEUTRAL")

    def test_reset(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event(self._obj_event("baron", 100))
        obs.reset()
        self.assertEqual(obs.get_all_objectives()["barons"], [])

    def test_non_objective_event_ignored(self):
        obs = ObjectiveKilledObserver()
        obs.notify_event({"rfc461Schema": "stats_update"})
        objs = obs.get_all_objectives()
        self.assertTrue(all(len(v) == 0 for v in objs.values()))


# ---------------------------------------------------------------------------
# WardsObserver
# ---------------------------------------------------------------------------

class TestWardsObserver(unittest.TestCase):

    def _make_teams_obs(self):
        obs = TeamsObserver()
        obs.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "aaa"},
        ]}})
        return obs

    def test_ward_placed(self):
        wards_obs = WardsObserver(teams_observer=self._make_teams_obs())
        wards_obs.notify_event({"rfc461Schema": "ward_placed",
                                "placer": 1, "wardType": "yellowTrinket",
                                "position": {"x": 100, "z": 200},
                                "gameTime": 30000})
        wards = wards_obs.get_wards()
        self.assertEqual(len(wards), 1)
        self.assertEqual(wards[0]["placer"], "Faker")
        self.assertEqual(wards[0]["type"], "yellowTrinket")
        self.assertEqual(wards[0]["team"], "BLUE")
        self.assertAlmostEqual(wards[0]["time"], 30.0)

    def test_non_ward_event_ignored(self):
        wards_obs = WardsObserver(teams_observer=self._make_teams_obs())
        wards_obs.notify_event({"rfc461Schema": "stats_update"})
        self.assertEqual(len(wards_obs.get_wards()), 0)

    def test_reset(self):
        wards_obs = WardsObserver(teams_observer=self._make_teams_obs())
        wards_obs.notify_event({"rfc461Schema": "ward_placed",
                                "placer": 1, "wardType": "yellowTrinket",
                                "position": {"x": 0, "z": 0}, "gameTime": 1000})
        wards_obs.reset()
        self.assertEqual(len(wards_obs.get_wards()), 0)


# ---------------------------------------------------------------------------
# GameEventProcessor — aislamiento de excepciones
# ---------------------------------------------------------------------------

class TestGameEventProcessorIsolation(unittest.TestCase):

    def test_bad_observer_does_not_kill_good_observer(self):
        """Un observer que explota no debe impedir que el siguiente reciba el evento."""

        class BrokenObserver:
            def notify_event(self, event):
                raise ValueError("observer roto")

        good_obs = ObjectiveKilledObserver()
        processor = GameEventProcessor()
        processor.attach(BrokenObserver())
        processor.attach(good_obs)

        processor.process_events([{
            "rfc461Schema": "epic_monster_kill",
            "killerTeamID": 100,
            "monsterType": "baron",
            "gameTime": 1200000,
            "killer": 1
        }])

        self.assertEqual(len(good_obs.get_all_objectives()["barons"]), 1)


# ---------------------------------------------------------------------------
# MidGameStatsObserver
# ---------------------------------------------------------------------------

class TestMidGameStatsObserver(unittest.TestCase):

    def _stats_update(self, game_time, participants):
        return {"rfc461Schema": "stats_update", "gameTime": game_time,
                "participants": participants}

    def _player(self, pid, minions, neutrals, total_gold, xp):
        # Forma real del feed: id 'participantID', XP/totalGold a nivel participante,
        # CS en la lista 'stats' (neutrales pueden venir como float).
        return {"participantID": pid, "XP": xp, "totalGold": total_gold,
                "stats": [{"name": "MINIONS_KILLED", "value": minions},
                          {"name": "NEUTRAL_MINIONS_KILLED", "value": neutrals}]}

    def test_snapshot_takes_last_before_mark(self):
        obs = MidGameStatsObserver()
        # 6:00 y 7:00 caen en la marca 7; gana el de 7:00.
        obs.notify_event(self._stats_update(360000, [self._player(1, 40, 0, 2000, 2500)]))
        obs.notify_event(self._stats_update(420000, [self._player(1, 56, 0, 2258, 2891)]))
        # 7:10 ya pasa la marca 7: no debe sobrescribir.
        obs.notify_event(self._stats_update(430000, [self._player(1, 60, 0, 2400, 3000)]))
        mark7 = obs.get_mid_game_stats()[1]["marks"][7]
        self.assertEqual(mark7["cs"], 56)
        self.assertEqual(mark7["gold"], 2258)
        self.assertEqual(mark7["xp"], 2891)
        self.assertAlmostEqual(mark7["game_time_s"], 420.0)

    def test_cs_includes_jungle_neutrals(self):
        obs = MidGameStatsObserver()
        # Jungla: MINIONS_KILLED=0, NEUTRAL_MINIONS_KILLED=40 (float, como en el feed).
        obs.notify_event(self._stats_update(420000,
                         [self._player(2, 0, 40.00000762939453, 1945, 1964)]))
        self.assertEqual(obs.get_mid_game_stats()[2]["marks"][7]["cs"], 40)

    def test_mark_not_reached_is_none(self):
        obs = MidGameStatsObserver()
        # Partida que solo llega al minuto 7: la marca 14 queda en None.
        obs.notify_event(self._stats_update(420000, [self._player(1, 56, 0, 2258, 2891)]))
        marks = obs.get_mid_game_stats()[1]["marks"]
        self.assertEqual(marks[7]["cs"], 56)
        self.assertIsNone(marks[14]["cs"])
        self.assertIsNone(marks[14]["gold"])
        self.assertIsNone(marks[14]["xp"])
        self.assertIsNone(marks[14]["game_time_s"])

    def test_custom_marks(self):
        obs = MidGameStatsObserver(marks_minutes=[10])
        obs.notify_event(self._stats_update(600000, [self._player(1, 80, 0, 4000, 5000)]))
        stats = obs.get_mid_game_stats()[1]["marks"]
        self.assertIn(10, stats)
        self.assertNotIn(7, stats)

    def test_enriched_with_teams_observer(self):
        teams = TeamsObserver()
        teams.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "aaa"},
        ]}})
        obs = MidGameStatsObserver()
        obs.notify_event(self._stats_update(420000, [self._player(1, 56, 0, 2258, 2891)]))
        entry = obs.get_mid_game_stats(teams_observer=teams)[1]
        self.assertEqual(entry["name"], "Faker")
        self.assertEqual(entry["side"], "BLUE")
        self.assertEqual(entry["champion"], "Orianna")

    def test_non_stats_event_ignored(self):
        obs = MidGameStatsObserver()
        obs.notify_event({"rfc461Schema": "ward_placed"})
        self.assertEqual(obs.get_mid_game_stats(), {})

    def test_reset(self):
        obs = MidGameStatsObserver()
        obs.notify_event(self._stats_update(420000, [self._player(1, 56, 0, 2258, 2891)]))
        obs.reset()
        self.assertEqual(obs.get_mid_game_stats(), {})


# ---------------------------------------------------------------------------
# SoloKillObserver
# ---------------------------------------------------------------------------

class TestSoloKillObserver(unittest.TestCase):

    def _make_teams_obs(self):
        obs = TeamsObserver()
        obs.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "aaa"},
            {"participantId": 6, "riotIdGameName": "Zeus", "teamId": 200,
             "championName": "Gnar", "puuid": "bbb"},
        ]}})
        return obs

    def _kill(self, killer, victim, assistants=None, game_time=300000,
              position=None):
        e = {"rfc461Schema": "champion_kill", "killer": killer, "victim": victim,
             "assistants": assistants or [], "gameTime": game_time,
             "position": position or {"x": 5000, "z": 7000}}
        return e

    def test_solokill_recorded(self):
        obs = SoloKillObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6, game_time=420000,
                                    position={"x": 5000, "z": 7000}))
        kills = obs.get_solokills()
        self.assertEqual(len(kills), 1)
        k = kills[0]
        self.assertEqual(k["killer"], "Faker")
        self.assertEqual(k["killer_side"], "BLUE")
        self.assertEqual(k["victim"], "Zeus")
        self.assertEqual(k["victim_side"], "RED")
        self.assertEqual(k["position"], {"x": 5000, "y": 7000})
        self.assertAlmostEqual(k["time"], 420.0)

    def test_kill_with_assistants_ignored(self):
        obs = SoloKillObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6, assistants=[2]))
        self.assertEqual(len(obs.get_solokills()), 0)

    def test_execution_by_tower_ignored(self):
        obs = SoloKillObserver(teams_observer=self._make_teams_obs())
        # killer fuera de 1-10 (ejecución por torre/minion).
        obs.notify_event(self._kill(0, 6))
        self.assertEqual(len(obs.get_solokills()), 0)

    def test_non_kill_event_ignored(self):
        obs = SoloKillObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "stats_update"})
        self.assertEqual(len(obs.get_solokills()), 0)

    def test_reset(self):
        obs = SoloKillObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6))
        obs.reset()
        self.assertEqual(len(obs.get_solokills()), 0)


# ---------------------------------------------------------------------------
# PlayerTimelineObserver
# ---------------------------------------------------------------------------

class TestPlayerTimelineObserver(unittest.TestCase):

    def _player(self, pid, x, z, total_gold, xp, level, ult_cd,
                minions=0, neutrals=0, ad=100.0, sums=(0.0, 0.0)):
        return {
            "participantID": pid, "position": {"x": x, "z": z},
            "alive": True, "respawnTimer": 0.0,
            "totalGold": total_gold, "currentGold": total_gold, "XP": xp, "level": level,
            "items": [3006, 3031],
            "attackDamage": ad, "abilityPower": 0, "armor": 50, "magicResist": 40,
            "attackSpeed": 0.8, "health": 1000, "healthMax": 1200,
            "ultimateCooldownRemaining": ult_cd,
            "ability1CooldownRemaining": 0, "ability2CooldownRemaining": 3,
            "ability3CooldownRemaining": 0, "ability4CooldownRemaining": ult_cd,
            "summonerSpell1CooldownRemaining": sums[0],
            "summonerSpell2CooldownRemaining": sums[1],
            "stats": [{"name": "MINIONS_KILLED", "value": minions},
                      {"name": "NEUTRAL_MINIONS_KILLED", "value": neutrals}],
        }

    def _su(self, game_time, participants, teams=None):
        ev = {"rfc461Schema": "stats_update", "gameTime": game_time,
              "participants": participants}
        if teams is not None:
            ev["teams"] = teams
        return ev

    def test_positions_series(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(1000, [self._player(1, 100, 200, 500, 100, 1, 0)]))
        obs.notify_event(self._su(2000, [self._player(1, 150, 250, 600, 150, 2, 5)]))
        pos = obs.get_positions(1)
        self.assertEqual(len(pos), 2)
        self.assertEqual(pos[0], {"t": 1.0, "x": 100, "y": 200})
        self.assertEqual(pos[1]["x"], 150)

    def test_economy_and_cs_with_jungle(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(420000,
                         [self._player(2, 0, 0, 1945, 1964, 6, 0,
                                       minions=10, neutrals=40.00000762939453)]))
        eco = obs.get_economy(2)
        self.assertEqual(eco[0]["gold_total"], 1945)
        self.assertEqual(eco[0]["level"], 6)
        self.assertEqual(eco[0]["cs"], 50)

    def test_champion_stats_present(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(1000, [self._player(1, 0, 0, 500, 100, 1, 0, ad=137.5)]))
        stats = obs.get_champion_stats(1)
        self.assertAlmostEqual(stats[0]["ad"], 137.5)
        self.assertEqual(stats[0]["armor"], 50.0)

    def test_ultimate_availability_direct(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(60000, [self._player(1, 0, 0, 500, 100, 6, 45)]))
        obs.notify_event(self._su(120000, [self._player(1, 0, 0, 600, 200, 6, 0)]))
        # A los 60s la ult está en cooldown; a los 120s disponible.
        self.assertFalse(obs.is_ultimate_up(1, 60))
        self.assertTrue(obs.is_ultimate_up(1, 120))
        # Antes del primer snapshot: sin dato.
        self.assertIsNone(obs.is_ultimate_up(1, 10))

    def test_summoner_availability(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(60000, [self._player(1, 0, 0, 500, 100, 3, 0,
                                                        sums=(0.0, 120.0))]))
        self.assertTrue(obs.is_summoner_up(1, 1, 60))
        self.assertFalse(obs.is_summoner_up(1, 2, 60))

    def test_snapshot_at_takes_last_before(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(60000, [self._player(1, 0, 0, 500, 100, 3, 0)]))
        obs.notify_event(self._su(120000, [self._player(1, 0, 0, 900, 300, 5, 0)]))
        snap = obs.snapshot_at(1, 90)
        self.assertEqual(snap["gold_total"], 500)  # el de 60s, no el de 120s
        self.assertIsNone(obs.snapshot_at(1, 10))

    def test_team_gold_series(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(60000, [self._player(1, 0, 0, 500, 100, 3, 0)],
                                  teams=[{"teamID": 100, "totalGold": 5000},
                                         {"teamID": 200, "totalGold": 4800}]))
        series = obs.get_team_gold_series()
        self.assertEqual(series[100][0]["gold"], 5000)
        self.assertEqual(series[200][0]["gold"], 4800)

    def test_non_stats_update_ignored(self):
        obs = PlayerTimelineObserver()
        obs.notify_event({"rfc461Schema": "champion_kill"})
        self.assertEqual(obs.get_players(), [])

    def test_reset(self):
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(1000, [self._player(1, 0, 0, 500, 100, 1, 0)]))
        obs.reset()
        self.assertEqual(obs.get_players(), [])

    def test_combat_totals_present(self):
        obs = PlayerTimelineObserver()
        p = self._player(1, 0, 0, 500, 100, 1, 0)
        p["stats"] += [
            {"name": "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", "value": 1234},
            {"name": "TOTAL_DAMAGE_TAKEN_FROM_CHAMPIONS", "value": 567},
            {"name": "CHAMPIONS_KILLED", "value": 2},
            {"name": "VISION_SCORE", "value": 12.5},
        ]
        obs.notify_event(self._su(1000, [p]))
        totals = obs.get_combat_totals(1)
        self.assertEqual(totals[0]["damage_to_champions"], 1234)
        self.assertEqual(totals[0]["damage_taken_from_champions"], 567)
        self.assertEqual(totals[0]["champions_killed"], 2)
        self.assertAlmostEqual(totals[0]["vision_score"], 12.5)

    def test_combat_totals_non_champion_damage(self):
        """Daño a torres/objetivos: la evidencia de "este equipo juega para una
        torre / para el objetivo", no todo compromiso deja daño entre campeones."""
        obs = PlayerTimelineObserver()
        p = self._player(1, 0, 0, 500, 100, 1, 0)
        p["stats"] += [
            {"name": "TOTAL_DAMAGE_DEALT_TO_TURRETS", "value": 900},
            {"name": "TOTAL_DAMAGE_DEALT_TO_OBJECTIVES", "value": 4500},
            {"name": "TOTAL_DAMAGE_DEALT_TO_EPIC_MONSTERS", "value": 3100},
        ]
        obs.notify_event(self._su(1000, [p]))
        totals = obs.get_combat_totals(1)
        self.assertEqual(totals[0]["damage_to_turrets"], 900)
        self.assertEqual(totals[0]["damage_to_objectives"], 4500)
        self.assertEqual(totals[0]["damage_to_epic_monsters"], 3100)
        # Ausente de la lista -> None, no 0 (mismo criterio que el resto).
        self.assertIsNone(totals[0]["damage_to_buildings"])

    def test_combat_totals_missing_stat_is_none_not_zero(self):
        # _player() solo mete MINIONS_KILLED/NEUTRAL_MINIONS_KILLED en `stats`:
        # el resto de contadores de combate no aparece en este evento.
        obs = PlayerTimelineObserver()
        obs.notify_event(self._su(1000, [self._player(1, 0, 0, 500, 100, 1, 0)]))
        totals = obs.get_combat_totals(1)
        self.assertIsNone(totals[0]["damage_to_champions"])
        self.assertIsNone(totals[0]["vision_score"])

    def test_combat_totals_no_stats_list(self):
        # Evento sin lista `stats` en absoluto -> todo None, sin excepción.
        obs = PlayerTimelineObserver()
        p = self._player(1, 0, 0, 500, 100, 1, 0)
        del p["stats"]
        obs.notify_event(self._su(1000, [p]))
        totals = obs.get_combat_totals(1)
        self.assertIsNone(totals[0]["damage_to_champions"])
        self.assertIsNone(totals[0]["champions_killed"])


# ---------------------------------------------------------------------------
# CombatObserver
# ---------------------------------------------------------------------------

class TestCombatObserver(unittest.TestCase):

    def _make_teams_obs(self):
        obs = TeamsObserver()
        obs.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "a"},
            {"participantId": 6, "riotIdGameName": "Chovy", "teamId": 200,
             "championName": "Azir", "puuid": "b"},
            {"participantId": 2, "riotIdGameName": "Oner", "teamId": 100,
             "championName": "LeeSin", "puuid": "c"},
        ]}})
        return obs

    def _kill(self, killer, victim, assistants, gt=300000):
        return {"rfc461Schema": "champion_kill", "killer": killer, "victim": victim,
                "assistants": assistants, "killerTeamID": 100, "victimTeamID": 200,
                "position": {"x": 5000, "z": 6000}, "gameTime": gt,
                "fightDuration": 3.2, "killStreakLength": 1, "shutdownBounty": 0,
                "bounty": 300,
                "deathRecap": [{"source": "Q", "casterId": 1, "breakdown": [{"a": 1}]}]}

    def test_kill_recorded_with_context(self):
        obs = CombatObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6, [2]))
        kills = obs.get_kills()
        self.assertEqual(len(kills), 1)
        k = kills[0]
        self.assertEqual(k["killer"], "Faker")
        self.assertEqual(k["killer_side"], "BLUE")
        self.assertEqual(k["victim"], "Chovy")
        self.assertEqual(k["victim_side"], "RED")
        self.assertEqual(k["assistants"][0]["name"], "Oner")
        self.assertEqual(k["position"], {"x": 5000, "y": 6000})
        self.assertEqual(k["damage_breakdown"][0]["source"], "Q")

    def test_special_event(self):
        obs = CombatObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "champion_kill_special",
                          "killType": "firstBlood", "killer": 1,
                          "position": {"x": 1, "z": 2}, "gameTime": 90000})
        specials = obs.get_special_events()
        self.assertEqual(specials[0]["type"], "firstBlood")
        self.assertEqual(specials[0]["killer"], "Faker")

    def test_kda_timeline_accumulates(self):
        obs = CombatObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6, [2], gt=100000))
        obs.notify_event(self._kill(1, 6, [], gt=200000))
        tl = obs.get_kda_timeline()
        self.assertEqual(tl[1][-1]["kills"], 2)     # Faker: 2 kills
        self.assertEqual(tl[6][-1]["deaths"], 2)    # Chovy: 2 deaths
        self.assertEqual(tl[2][-1]["assists"], 1)   # Oner: 1 assist

    def test_reset(self):
        obs = CombatObserver(teams_observer=self._make_teams_obs())
        obs.notify_event(self._kill(1, 6, []))
        obs.reset()
        self.assertEqual(len(obs.get_kills()), 0)


# ---------------------------------------------------------------------------
# WardEventsObserver
# ---------------------------------------------------------------------------

class TestWardEventsObserver(unittest.TestCase):

    def _make_teams_obs(self):
        obs = TeamsObserver()
        obs.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "a"},
            {"participantId": 6, "riotIdGameName": "Chovy", "teamId": 200,
             "championName": "Azir", "puuid": "b"},
        ]}})
        return obs

    def test_placed_and_killed(self):
        obs = WardEventsObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "ward_placed", "placer": 1,
                          "wardType": "control", "position": {"x": 10, "z": 20},
                          "gameTime": 30000})
        obs.notify_event({"rfc461Schema": "ward_killed", "killer": 6,
                          "wardType": "control", "position": {"x": 10, "z": 20},
                          "gameTime": 60000})
        placements = obs.get_placements()
        kills = obs.get_kills()
        self.assertEqual(placements[0]["player"], "Faker")
        self.assertEqual(placements[0]["team"], "BLUE")
        self.assertEqual(placements[0]["type"], "control")
        self.assertEqual(placements[0]["position"], {"x": 10, "y": 20})
        self.assertEqual(kills[0]["killer"], "Chovy")
        self.assertEqual(kills[0]["team"], "RED")

    def test_get_events_merged_sorted(self):
        obs = WardEventsObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "ward_killed", "killer": 6,
                          "wardType": "sight", "position": {"x": 0, "z": 0},
                          "gameTime": 60000})
        obs.notify_event({"rfc461Schema": "ward_placed", "placer": 1,
                          "wardType": "sight", "position": {"x": 0, "z": 0},
                          "gameTime": 30000})
        events = obs.get_events()
        self.assertEqual([e["action"] for e in events], ["placed", "killed"])

    def test_reset(self):
        obs = WardEventsObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "ward_placed", "placer": 1,
                          "wardType": "sight", "position": {"x": 0, "z": 0},
                          "gameTime": 1000})
        obs.reset()
        self.assertEqual(len(obs.get_placements()), 0)


# ---------------------------------------------------------------------------
# BuildingObserver
# ---------------------------------------------------------------------------

class TestBuildingObserver(unittest.TestCase):

    def test_turret_destroyed(self):
        obs = BuildingObserver()
        obs.notify_event({"rfc461Schema": "building_destroyed",
                          "buildingType": "turret", "lane": "top",
                          "turretTier": "outer", "teamID": 200, "lastHitter": 1,
                          "assistants": [2, 3], "bountyGold": 250,
                          "position": {"x": 100, "z": 200}, "gameTime": 600000})
        turrets = obs.get_turrets()
        self.assertEqual(len(turrets), 1)
        t = turrets[0]
        self.assertEqual(t["lane"], "top")
        self.assertEqual(t["turret_tier"], "outer")
        self.assertEqual(t["owner_team"], "RED")
        self.assertEqual(t["killed_by_team"], "BLUE")
        self.assertEqual(t["assistants"], [2, 3])

    def test_inhibitor_and_plate(self):
        obs = BuildingObserver()
        obs.notify_event({"rfc461Schema": "building_destroyed",
                          "buildingType": "inhibitor", "lane": "mid",
                          "teamID": 100, "gameTime": 1500000})
        obs.notify_event({"rfc461Schema": "turret_plate_destroyed", "lane": "bot",
                          "teamID": 200, "lastHitter": 6, "gameTime": 500000})
        self.assertEqual(len(obs.get_inhibitors()), 1)
        self.assertEqual(len(obs.get_turrets()), 0)
        self.assertEqual(obs.get_plates()[0]["lane"], "bot")

    def test_reset(self):
        obs = BuildingObserver()
        obs.notify_event({"rfc461Schema": "building_destroyed",
                          "buildingType": "turret", "teamID": 200, "gameTime": 1000})
        obs.reset()
        self.assertEqual(len(obs.get_buildings()), 0)


# ---------------------------------------------------------------------------
# ObjectiveSpawnObserver
# ---------------------------------------------------------------------------

class TestObjectiveSpawnObserver(unittest.TestCase):

    def _dragon_spawn(self, dtype, gt):
        return {"rfc461Schema": "epic_monster_spawn", "monsterType": "dragon",
                "dragonType": dtype, "gameTime": gt}

    def test_rift_type_from_third_dragon(self):
        obs = ObjectiveSpawnObserver()
        obs.notify_event(self._dragon_spawn("fire", 300000))
        obs.notify_event(self._dragon_spawn("air", 600000))
        # Aún no hay 3 dragones: grieta indefinida.
        self.assertIsNone(obs.get_rift_type())
        obs.notify_event(self._dragon_spawn("earth", 900000))
        rift = obs.get_rift_type()
        self.assertEqual(rift["type"], "earth")
        self.assertAlmostEqual(rift["time"], 900.0)

    def test_rift_type_excludes_elder(self):
        obs = ObjectiveSpawnObserver()
        obs.notify_event(self._dragon_spawn("fire", 300000))
        obs.notify_event(self._dragon_spawn("air", 600000))
        obs.notify_event(self._dragon_spawn("elder", 2000000))
        # Elder no cuenta como 3.er dragón elemental.
        self.assertIsNone(obs.get_rift_type())

    def test_nashor_type_from_baron_spawn(self):
        obs = ObjectiveSpawnObserver()
        # Feed real: el barón solo se expone como "Baron" (sin variante).
        obs.notify_event({"rfc461Schema": "neutral_minion_spawn",
                          "monsterType": "Baron", "teamSide": 0,
                          "position": {"x": 1, "z": 2}, "gameTime": 1200000})
        nashor = obs.get_nashor_type()
        self.assertEqual(nashor["type"], "Baron")
        self.assertAlmostEqual(nashor["time"], 1200.0)  # spawn real, no el placeholder

    def test_nashor_prefers_queued_name_and_real_spawn_time(self):
        obs = ObjectiveSpawnObserver()
        # spawnTime en SEGUNDOS (1200 = 20:00). El placeholder de pre-partida no debe
        # fijar el instante: se usa el primer spawn real del barón.
        obs.notify_event({"rfc461Schema": "queued_epic_monster_info",
                          "monsterName": "SRU_Baron_Territorial", "spawnTime": 1200,
                          "position": {"x": 1, "z": 2}, "gameTime": 0})
        obs.notify_event({"rfc461Schema": "neutral_minion_spawn",
                          "monsterType": "Baron", "gameTime": 1180000})
        nashor = obs.get_nashor_type()
        self.assertEqual(nashor["type"], "SRU_Baron_Territorial")   # nombre específico
        self.assertAlmostEqual(nashor["time"], 1180.0)              # spawn real, no 1.2

    def test_queued_dragon(self):
        obs = ObjectiveSpawnObserver()
        # Feed real: gameTime en ms (60000=1:00) pero nextDragonSpawnTime en SEGUNDOS.
        obs.notify_event({"rfc461Schema": "queued_dragon_info",
                          "nextDragonName": "fire", "nextDragonSpawnTime": 300,
                          "gameTime": 60000})
        q = obs.get_queued_dragon()
        self.assertEqual(q["next_dragon_name"], "fire")
        self.assertAlmostEqual(q["next_spawn_time"], 300.0)  # 5:00, no 300000/1000

    def test_kills_recorded(self):
        obs = ObjectiveSpawnObserver()
        obs.notify_event({"rfc461Schema": "epic_monster_kill", "monsterType": "dragon",
                          "dragonType": "fire", "killType": "steal", "killer": 6,
                          "killerTeamID": 200, "gameTime": 350000})
        k = obs.get_kills()[0]
        self.assertEqual(k["kill_type"], "steal")
        self.assertEqual(k["team"], "RED")

    def test_reset(self):
        obs = ObjectiveSpawnObserver()
        obs.notify_event(self._dragon_spawn("fire", 300000))
        obs.reset()
        self.assertEqual(len(obs.get_dragon_spawns()), 0)


# ---------------------------------------------------------------------------
# MobilityObserver
# ---------------------------------------------------------------------------

class TestMobilityObserver(unittest.TestCase):

    def _make_teams_obs(self):
        obs = TeamsObserver()
        obs.notify_event({"source": "RIOT_SUMMARY", "payload": {"participants": [
            {"participantId": 1, "riotIdGameName": "Faker", "teamId": 100,
             "championName": "Orianna", "puuid": "a"},
            {"participantId": 6, "riotIdGameName": "Chovy", "teamId": 200,
             "championName": "Azir", "puuid": "b"},
        ]}})
        return obs

    def test_summoner_spell_used(self):
        obs = MobilityObserver(teams_observer=self._make_teams_obs())
        obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": 1,
                          "summonerSpellName": "SummonerFlash", "summonerSpellSlot": 2,
                          "maxCooldown": 300000, "chargesRemaining": 0, "maxCharges": 1,
                          "gameTime": 78533})
        use = obs.get_summoner_spell_uses()[0]
        self.assertEqual(use["spell_name"], "SummonerFlash")
        self.assertEqual(use["spell_slot"], 2)
        self.assertEqual(use["player"], "Faker")
        self.assertEqual(use["team"], "BLUE")
        # gameTime en ms -> time en segundos; el cooldown se deja crudo en ms.
        self.assertAlmostEqual(use["time"], 78.533)
        self.assertEqual(use["max_cooldown_ms"], 300000)

    def test_skill_used_uses_participant_key(self):
        """El id de jugador llega como `participant` (no `participantID`) en skills."""
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "skill_used", "participant": 6,
                          "skillSlot": 3, "maxCooldown": 5555, "gameTime": 10537})
        skill = obs.get_skill_uses()[0]
        self.assertEqual(skill["player_id"], 6)
        self.assertEqual(skill["skill_slot"], 3)

    def test_channeling_start_and_end_not_paired(self):
        """Se exponen crudos, sin emparejar: emparejar es derivacion del consumidor."""
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "channeling_started", "participantID": 1,
                          "channelingType": "recall", "gameTime": 106682})
        obs.notify_event({"rfc461Schema": "channeling_ended", "participantID": 1,
                          "channelingType": "recall", "isInterrupted": True,
                          "gameTime": 114696})
        self.assertEqual(len(obs.get_channeling_starts()), 1)
        end = obs.get_channeling_ends()[0]
        self.assertEqual(end["channeling_type"], "recall")
        self.assertTrue(end["interrupted"])
        # El `started` no inventa la clave que solo trae el `ended`.
        self.assertNotIn("interrupted", obs.get_channeling_starts()[0])

    def test_item_active_ability(self):
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "item_active_ability_used", "participantID": 4,
                          "itemID": 3340, "inventorySlot": 7, "maxCooldown": 210000,
                          "gameTime": 28101})
        item = obs.get_item_actives()[0]
        self.assertEqual(item["item_id"], 3340)
        self.assertEqual(item["inventory_slot"], 7)

    def test_works_without_teams_observer(self):
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": 1,
                          "summonerSpellName": "SummonerTeleport", "gameTime": 1000})
        use = obs.get_summoner_spell_uses()[0]
        self.assertEqual(use["player_id"], 1)
        self.assertIsNone(use["player"])
        self.assertIsNone(use["team"])

    def test_filter_by_player(self):
        obs = MobilityObserver()
        for pid, t in ((1, 1000), (6, 2000), (1, 3000)):
            obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": pid,
                              "summonerSpellName": "SummonerFlash", "gameTime": t})
        self.assertEqual(len(obs.get_summoner_spell_uses(pid=1)), 2)
        self.assertEqual(len(obs.get_summoner_spell_uses()), 3)

    def test_get_events_merged_sorted_with_kind(self):
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "skill_used", "participant": 1,
                          "skillSlot": 1, "gameTime": 5000})
        obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": 1,
                          "summonerSpellName": "SummonerFlash", "gameTime": 1000})
        obs.notify_event({"rfc461Schema": "channeling_started", "participantID": 1,
                          "channelingType": "recall", "gameTime": 3000})
        events = obs.get_events()
        self.assertEqual([e["kind"] for e in events],
                         ["summoner_spell", "channeling_started", "skill"])

    def test_ignores_unrelated_schema(self):
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "ward_placed", "placer": 1, "gameTime": 1000})
        self.assertEqual(len(obs.get_events()), 0)

    def test_malformed_participant_id_does_not_raise(self):
        """Un id no convertible no tumba la ingesta: queda a None."""
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": "no-int",
                          "summonerSpellName": "SummonerFlash", "gameTime": 1000})
        self.assertIsNone(obs.get_summoner_spell_uses()[0]["player_id"])

    def test_reset(self):
        obs = MobilityObserver()
        obs.notify_event({"rfc461Schema": "summoner_spell_used", "participantID": 1,
                          "summonerSpellName": "SummonerFlash", "gameTime": 1000})
        obs.reset()
        self.assertEqual(len(obs.get_summoner_spell_uses()), 0)
        self.assertEqual(len(obs.get_events()), 0)


if __name__ == "__main__":
    unittest.main()

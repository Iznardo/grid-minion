import unittest
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver,
    PostGameObserver, ObjectiveKilledObserver, WardsObserver, BuildObserver
)
from grid_minion.champions import ChampionResolver, set_default_resolver


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
        obs = DraftObserver()
        obs.notify_event({"events": [self._ban("TEAM_A", "Orianna")]})
        obs.notify_event({"events": [self._ban("TEAM_B", "Ryze")]})
        draft = obs.get_draft()
        self.assertEqual(draft["fp"]["team_id"], "TEAM_A")
        self.assertEqual(draft["sp"]["team_id"], "TEAM_B")

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
                          "teams": [{"teamID": 100, "totalGold": 8000},
                                    {"teamID": 200, "totalGold": 5000}]})
        meta = obs.get_game_stats()["meta"]
        self.assertEqual(meta["winner"], "BLUE")
        self.assertEqual(meta["winner_source"], "gold_heuristic")

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

    def test_no_runes_without_summary(self):
        """Sin summary, runes y final_items son None (no se inventan)."""
        obs = PostGameObserver()
        obs.notify_event({"rfc461Schema": "stats_update",
                          "participants": [{"participantId": 1, "stats": []}],
                          "teams": []})
        player = obs.get_game_stats()["players"][1]
        self.assertIsNone(player["runes"])
        self.assertIsNone(player["final_items"])


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


if __name__ == "__main__":
    unittest.main()

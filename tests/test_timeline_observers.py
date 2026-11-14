"""Tests de los observers de eventos detallados (combate, wards, estructuras,
spawns de objetivos y movilidad)."""
import unittest

from grid_minion.observers import (
    BuildingObserver, CombatObserver, MobilityObserver, ObjectiveSpawnObserver,
    TeamsObserver, WardEventsObserver,
)


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

import unittest
import json
import os
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver,
    PostGameObserver, ObjectiveKilledObserver, WardsObserver, BuildObserver
)
from grid_minion.champions import ChampionResolver, set_default_resolver

# Mapeo mínimo para normalizar sin tocar Data Dragon (red).
# Forma: {<clave Riot>: {"name": <display GRID>, "key": <id numérico>}}
_TEST_CHAMPIONS = {
    "Ambessa": {"name": "Ambessa", "key": 799},
    "JarvanIV": {"name": "Jarvan IV", "key": 59},
    "Rumble": {"name": "Rumble", "key": 68},
}

class TestObserversIntegration(unittest.TestCase):
    def setUp(self):
        # Resolver seedeado: los tests no deben hacer peticiones de red.
        set_default_resolver(ChampionResolver.from_mapping(_TEST_CHAMPIONS))

    def tearDown(self):
        set_default_resolver(None)

    @classmethod
    def setUpClass(cls):
        # Cargar los mocks
        base_path = os.path.dirname(__file__)
        with open(os.path.join(base_path, 'samples/grid_events.json'), 'r') as f:
            cls.grid_events = json.load(f)
        with open(os.path.join(base_path, 'samples/riot_summary.json'), 'r') as f:
            cls.riot_summary = json.load(f)
        
        cls.riot_livestats = []
        with open(os.path.join(base_path, 'samples/riot_livestats.jsonl'), 'r') as f:
            for line in f:
                if line.strip():
                    cls.riot_livestats.append(json.loads(line))

    def test_full_processing_flow(self):
        """Test de integración que verifica el flujo completo de procesamiento."""
        processor = GameEventProcessor()
        
        teams_obs = TeamsObserver()
        draft_obs = DraftObserver()
        stats_obs = PostGameObserver()
        objectives_obs = ObjectiveKilledObserver()
        wards_obs = WardsObserver(teams_observer=teams_obs)
        build_obs = BuildObserver()

        processor.attach(teams_obs)
        processor.attach(draft_obs)
        processor.attach(stats_obs)
        processor.attach(objectives_obs)
        processor.attach(wards_obs)
        processor.attach(build_obs)
        
        # Procesar bundle
        processor.process_bundle(
            grid_livestats=self.grid_events,
            riot_summary=self.riot_summary,
            riot_livestats=self.riot_livestats
        )
        
        # 1. Verificar Teams & PUUID mapping
        player_1 = teams_obs.get_player_by_id(1)
        self.assertIsNotNone(player_1)
        self.assertEqual(player_1.grid_player_id, "24716")
        self.assertEqual(player_1.summoner_name, "GX ManoloGap")
        
        # 2. Verificar Draft (picks/bans normalizados a {name: clave Riot, id})
        draft = draft_obs.get_draft()
        self.assertTrue(draft['draft_found'])
        pick_names = [p['name'] for p in draft['fp']['picks']]
        ban_names = [b['name'] for b in draft['fp']['bans'] if b]
        self.assertIn("Ambessa", pick_names)
        # "Jarvan IV" (display de GRID) se normaliza a "JarvanIV" (clave de Riot).
        self.assertIn("JarvanIV", ban_names)
        self.assertIn({"name": "Ambessa", "id": 799}, draft['fp']['picks'])
        
        # 3. Verificar Objetivos
        objectives = objectives_obs.get_all_objectives()
        self.assertEqual(len(objectives['dragons']), 1)
        self.assertEqual(objectives['dragons'][0]['team'], "RED")
        self.assertEqual(objectives['dragons'][0]['type'], "chemtech")
        
        # 4. Verificar Visión
        wards = wards_obs.get_wards()
        self.assertEqual(len(wards), 1)
        self.assertEqual(wards[0]['placer'], "GX ManoloGap")
        self.assertEqual(wards[0]['type'], "yellowTrinket")
        
        # 5. Verificar PostGame Stats
        game_stats = stats_obs.get_game_stats(teams_obs)
        self.assertEqual(game_stats['meta']['winner'], "BLUE")
        self.assertEqual(game_stats['players'][1]['kills'], 3)
        # champion_id resuelto vía Data Dragon (player 1 = Rumble = 68).
        self.assertEqual(game_stats['players'][1]['champion'], "Rumble")
        self.assertEqual(game_stats['players'][1]['champion_id'], 68)
        # runes colapsadas y final_items (sin ceros) desde el summary.
        self.assertEqual(game_stats['players'][1]['final_items'],
                         [3047, 3157, 6653, 4645, 3363])
        self.assertEqual(game_stats['players'][1]['runes'], {
            "primary_style": 8200, "primary": [8229, 8275, 8233, 8237],
            "sub_style": 8400, "sub": [8473, 8242],
            "stat_perks": [5008, 5008, 5011],
        })

        # 6. Verificar Builds (timeline)
        builds = build_obs.get_builds()
        # skill_order: slots 1,2,3,1 = QWEQ; la subida evolved (slot 4) se excluye.
        self.assertEqual(builds[1]['skill_order'], "QWEQ")
        # build_path: la compra de 3157 se cancela con su item_undo.
        self.assertEqual(builds[1]['build_path'], [
            {"ts_s": 3, "action": "BUY", "item_id": 1056},
            {"ts_s": 145, "action": "BUY", "item_id": 1052},
            {"ts_s": 200, "action": "SELL", "item_id": 1056},
            {"ts_s": 430, "action": "BUY", "item_id": 3047},
        ])

class TestDraftInvalidation(unittest.TestCase):
    """Eventos sintéticos para el bug de draft vacío por invalidación mid-game."""

    def setUp(self):
        # Resolver vacío: los campeones desconocidos pasan tal cual (sin red).
        set_default_resolver(ChampionResolver.from_mapping({}))

    def tearDown(self):
        set_default_resolver(None)

    @staticmethod
    def _pick(team, champ):
        return {"type": "team-picked-character",
                "actor": {"id": team}, "target": {"state": {"name": champ}}}

    def _feed_full_draft(self, draft_obs):
        # 5 picks por equipo => is_complete. fp = primer actor ("A").
        champs = [f"Champ{i}" for i in range(1, 11)]
        for i, champ in enumerate(champs):
            team = "A" if i % 2 == 0 else "B"
            draft_obs.notify_event(self._pick(team, champ))
        return champs

    def test_invalidacion_mid_game_no_borra_el_draft(self):
        draft_obs = DraftObserver()
        self._feed_full_draft(draft_obs)
        self.assertTrue(draft_obs.is_complete)

        # Empieza la partida y luego llegan 3 invalidaciones (reconexión de feed).
        draft_obs.notify_event({"type": "series-started-game"})
        for _ in range(3):
            draft_obs.notify_event({"type": "grid-invalidated-series"})

        draft = draft_obs.get_draft()
        self.assertTrue(draft["is_complete"])
        self.assertTrue(draft["draft_found"])
        pick_names = [p["name"] for p in draft["fp"]["picks"]]
        self.assertIn("Champ1", pick_names)

    def test_invalidacion_pre_game_si_resetea(self):
        # El comportamiento de remake pre-partida se conserva.
        draft_obs = DraftObserver()
        draft_obs.notify_event(self._pick("A", "Champ1"))
        self.assertTrue(draft_obs.draft_found)

        draft_obs.notify_event({"type": "grid-invalidated-series"})

        self.assertFalse(draft_obs.draft_found)
        self.assertEqual(len(draft_obs.draft_history), 1)


if __name__ == '__main__':
    unittest.main()

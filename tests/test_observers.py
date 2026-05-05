import unittest
import json
import os
from src.grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver, 
    PostGameObserver, ObjectiveKilledObserver, WardsObserver
)

class TestObserversIntegration(unittest.TestCase):
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
        
        processor.attach(teams_obs)
        processor.attach(draft_obs)
        processor.attach(stats_obs)
        processor.attach(objectives_obs)
        processor.attach(wards_obs)
        
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
        
        # 2. Verificar Draft
        draft = draft_obs.get_draft()
        self.assertTrue(draft['draft_found'])
        self.assertIn("Ambessa", draft['fp']['picks'])
        self.assertIn("Jarvan IV", draft['fp']['bans'])
        
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

if __name__ == '__main__':
    unittest.main()

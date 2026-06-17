import unittest
from grid_minion.utils import (
    split_grid_series,
    split_grid_series_detailed,
    group_riot_livestats_fragments,
)

class TestUtils(unittest.TestCase):
    def test_split_grid_series_basic(self):
        """Verifica que divide correctamente una serie en partidas."""
        events = [
            {"events": [{"type": "team-picked-character"}]}, # Game 1 Draft
            {"events": [{"type": "game-started"}]},         # Game 1 Start
            {"events": [{"type": "other-event"}]},          
            {"events": [{"type": "team-picked-character"}]}, # Game 2 Draft (Trigger split)
            {"events": [{"type": "game-started"}]}          # Game 2 Start
        ]
        games = split_grid_series(events)
        self.assertEqual(len(games), 2)
        self.assertEqual(len(games[0]), 3)
        self.assertEqual(len(games[1]), 2)

    def test_split_grid_series_single_game(self):
        """Verifica que funciona con una sola partida."""
        events = [{"type": "game-started"}, {"type": "other"}]
        games = split_grid_series(events)
        self.assertEqual(len(games), 1)

    def test_split_grid_series_detailed_preserves_events_and_diagnostics(self):
        events = [
            {"events": [{"type": "team-picked-character"}]},
            {"events": [{"type": "grid-invalidated-series"}]},
            {"events": [{"type": "game-started"}]},
        ]
        detailed = split_grid_series_detailed(events)
        self.assertEqual(len(detailed), 1)
        self.assertEqual(detailed[0]["events"], events)
        self.assertEqual(detailed[0]["diagnostics"]["invalidations"], 1)
        self.assertEqual(detailed[0]["diagnostics"]["starts"], 1)

    def test_group_riot_livestats_fragments_uses_game_info_boundaries(self):
        fragments = [
            {"game_number": 1, "events": [
                {"rfc461Schema": "game_info", "gameTime": 0, "participants": [{}]},
                {"rfc461Schema": "stats_update", "gameTime": 1000},
            ]},
            {"game_number": 2, "events": [
                {"rfc461Schema": "stats_update", "gameTime": 2000},
            ]},
            {"game_number": 29, "events": [
                {"rfc461Schema": "game_info", "gameTime": 0, "participants": [{}]},
                {"rfc461Schema": "game_end", "gameTime": 1800000},
            ]},
        ]
        grouped = group_riot_livestats_fragments(fragments, expected_games=2)
        self.assertEqual(len(grouped["games"]), 2)
        self.assertEqual(grouped["diagnostics"][0]["fragments"], [1, 2])
        self.assertEqual(grouped["diagnostics"][1]["fragments"], [29])
        self.assertEqual(grouped["confidence"], "high")

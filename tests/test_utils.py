import unittest
from src.grid_minion.utils import split_grid_series

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

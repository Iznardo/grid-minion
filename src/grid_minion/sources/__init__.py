"""Normalizadores internos para fuentes no-Riot usadas por GRID."""

from .tencent import normalize_tencent_details
from .grid_state import extract_grid_game_state, normalize_grid_game_state

__all__ = [
    "normalize_tencent_details",
    "extract_grid_game_state",
    "normalize_grid_game_state",
]

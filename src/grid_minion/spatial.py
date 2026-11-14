"""Geometría de la Grieta del Invocador: funciones puras sobre coordenadas.

Todo es derivación sobre las posiciones que da `PositionObserver` (unidades
de mapa de Riot, `y` = el `z` del feed). Nada aquí lee el feed.

Orientación del mapa:
  - Base azul abajo a la izquierda (cerca de `(0, 0)`), base roja arriba a la
    derecha.
  - La línea central recorre la diagonal `y = x`.
  - El río recorre la otra diagonal (`x + y` constante): Barón arriba a la
    izquierda, dragón abajo a la derecha.
  - "top" es el lado del mapa por encima de la línea central (`y > x`);
    "bot", el de debajo.

Las zonas son una **aproximación por rectas**, pensada para clasificar dónde
está un jugador (línea, jungla, río, base), no para reproducir el terreno.
Los umbrales están calibrados con partidas reales (ver tests).
"""
import math
from typing import Any, Dict, Optional, Tuple

# Límites del mapa que usa Riot para escalar el minimapa.
MAP_MIN_X, MAP_MIN_Y = -120, -120
MAP_MAX_X, MAP_MAX_Y = 14870, 14980
# Centro de simetría del mapa, medido con posiciones reales (el terreno girado
# 180° sobre este punto coincide consigo mismo). No es el centro geométrico de
# los límites del minimapa, que está ~50 unidades desplazado.
MAP_CENTER = (7425.0, 7450.0)

# `x + y` sobre la línea del río (pasa por el centro del mapa).
RIVER_SUM = MAP_CENTER[0] + MAP_CENTER[1]

# Umbrales de zona (unidades de mapa).
BASE_SIZE = 4300          # lado del cuadrado de cada base, desde su esquina
LANE_EDGE = 2000          # ancho de las líneas laterales, desde el borde
LANE_CORNER = 7300        # suma de distancias a los dos bordes en la esquina
MID_HALF_WIDTH = 950      # |x - y| máximo para estar en la línea central
RIVER_HALF_WIDTH = 1250   # |x + y - RIVER_SUM| máximo para estar en el río

ZONES = (
    "base_blue", "base_red",
    "lane_top", "lane_mid", "lane_bot",
    "river_top", "river_bot",
    "jungle_blue_top", "jungle_blue_bot", "jungle_red_top", "jungle_red_bot",
)


def distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Distancia euclídea entre dos puntos `{x, y}`."""
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def normalize(x: float, y: float) -> Tuple[float, float]:
    """Coordenadas de mapa -> `[0, 1]` (origen abajo a la izquierda)."""
    return ((x - MAP_MIN_X) / (MAP_MAX_X - MAP_MIN_X),
            (y - MAP_MIN_Y) / (MAP_MAX_Y - MAP_MIN_Y))


def rotate(x: float, y: float) -> Tuple[float, float]:
    """Giro de 180° alrededor del centro del mapa.

    Lleva la base roja sobre la azul y cada campamento sobre su equivalente
    del otro equipo (el rojo del equipo rojo cae sobre el rojo del azul). Las
    líneas laterales se intercambian: la línea superior pasa a la inferior.
    """
    return (2 * MAP_CENTER[0] - x, 2 * MAP_CENTER[1] - y)


def to_blue_perspective(x: float, y: float, side: Optional[str]) -> Tuple[float, float]:
    """Posición como si el jugador jugara en azul.

    Para comparar a un mismo equipo o jugador entre partidas en lados
    distintos: las posiciones de un jugador del lado rojo se giran 180°; las
    del azul no cambian. Tras el giro, "su" jungla y "su" base caen siempre
    en el mismo sitio (ver `rotate` sobre las líneas laterales).
    """
    return rotate(x, y) if side == "RED" else (x, y)


def map_half(x: float, y: float) -> str:
    """Mitad del mapa en la que cae el punto: `BLUE` o `RED` (según el río)."""
    return "BLUE" if x + y < RIVER_SUM else "RED"


def map_side(x: float, y: float) -> str:
    """Lado respecto a la línea central: `top` (`y > x`) o `bot`."""
    return "top" if y > x else "bot"


def zone_of(x: float, y: float) -> str:
    """Zona del mapa en la que cae el punto (una de `ZONES`).

    Prioridad: base > línea > río > jungla. Así, el tramo de río que cruza la
    línea central cuenta como línea, y lo que no es base, línea ni río es
    jungla del cuadrante correspondiente.
    """
    if max(x - MAP_MIN_X, y - MAP_MIN_Y) < BASE_SIZE:
        return "base_blue"
    if max(MAP_MAX_X - x, MAP_MAX_Y - y) < BASE_SIZE:
        return "base_red"
    if abs(x - y) < MID_HALF_WIDTH:
        return "lane_mid"
    # Líneas laterales: una franja pegada a cada borde y, en la esquina donde
    # la línea gira (y donde se juega la mayor parte de la fase de líneas), un
    # triángulo que une las dos franjas.
    left, top = x - MAP_MIN_X, MAP_MAX_Y - y
    if left < LANE_EDGE or top < LANE_EDGE or left + top < LANE_CORNER:
        return "lane_top"
    bottom, right = y - MAP_MIN_Y, MAP_MAX_X - x
    if bottom < LANE_EDGE or right < LANE_EDGE or bottom + right < LANE_CORNER:
        return "lane_bot"
    side = map_side(x, y)
    if abs(x + y - RIVER_SUM) < RIVER_HALF_WIDTH:
        return f"river_{side}"
    return f"jungle_{map_half(x, y).lower()}_{side}"


def zone_kind(zone: str) -> str:
    """Tipo de zona: `base`, `lane`, `river` o `jungle`."""
    return zone.split("_", 1)[0]


def lane_of(zone: str) -> Optional[str]:
    """`top`/`mid`/`bot` si la zona es una línea; si no, `None`."""
    return zone.split("_", 1)[1] if zone.startswith("lane_") else None


def zone_owner(zone: str) -> Optional[str]:
    """Equipo dueño de la zona (`BLUE`/`RED`) para bases y jungla; `None` en
    líneas y río, que son terreno compartido."""
    if zone.startswith(("base_", "jungle_")):
        return zone.split("_")[1].upper()
    return None


def is_enemy_jungle(zone: str, side: Optional[str]) -> bool:
    """¿Es la zona jungla del equipo contrario a `side`?"""
    return zone.startswith("jungle_") and side in ("BLUE", "RED") and zone_owner(zone) != side


def is_own_base(zone: str, side: Optional[str]) -> bool:
    return zone.startswith("base_") and zone_owner(zone) == side

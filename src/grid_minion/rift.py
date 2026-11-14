"""Mapa de celdas de la Grieta del Invocador.

El mapa se divide en ~100 **celdas** pequeñas (tramos de línea, entradas del
río, campamentos, pasillos de jungla...). Cada posición cae en una celda, y
cada celda tiene etiquetas que la agrupan en **áreas** más grandes:

    rift = RiftMap.default()
    cell = rift.cell_at(7765, 4020)
    cell["name"]      # 'jungle_blue_red_buff'
    cell["area"]      # 'jungle_blue_red_buff'
    cell["kind"]      # 'jungle'
    cell["owner"]     # 'BLUE'
    rift.area_at(1100, 11500)   # 'lane_top_laning'

Etiquetas de cada celda:

| Campo | Valores |
|---|---|
| `kind` | `lane`, `river`, `pit`, `jungle`, `base` |
| `owner` | `BLUE`, `RED` o `None` (líneas en zona de línea, río, fosos) |
| `lane` | `top`, `mid`, `bot` o `None` |
| `segment` | en líneas: `blue_back`, `laning`, `red_back` (respecto a las torres exteriores) |
| `area` | agrupación: `lane_top_laning`, `river_top`, `jungle_red_raptors`, `base_blue`... |
| `landmark` | punto de interés si lo tiene: `raptors`, `dragon_pit`, `scuttle_top`... |

El mapa es una rejilla de etiquetas (una celda por píxel de 100 x 100
unidades) guardada en `data/rift_cells.json`. Al ser una rejilla, no puede
haber huecos ni solapes entre celdas. Los píxeles por los que nunca pasa nadie
(muros) no tienen celda; una consulta sobre ellos devuelve la celda caminable
más cercana.

El fichero se genera con `tools/build_rift_cells.py` a partir de posiciones
reales (lo caminable) y de puntos fijos del mapa (torres, campamentos, fosos),
y se puede retocar a mano con el editor de celdas.
"""
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import spatial

DATA_FILE = Path(__file__).resolve().parent / "data" / "rift_cells.json"
RIFT_CELLS_SCHEMA = "grid-minion/rift-cells/v1"

# ---------- simetría del mapa ----------

_SWAP_OWNER = {"BLUE": "RED", "RED": "BLUE"}
_SWAP_LANE = {"top": "bot", "bot": "top", "mid": "mid"}
_SWAP_SEGMENT = {"blue_back": "red_back", "red_back": "blue_back", "laning": "laning"}


def mirror_point(x: float, y: float) -> Tuple[float, float]:
    """Giro de 180° alrededor del centro del mapa (ver `spatial.rotate`)."""
    return spatial.rotate(x, y)


def _swap_words(text: Optional[str]) -> Optional[str]:
    """Cambia azul<->rojo y top<->bot en un nombre (`river_top` -> `river_bot`)."""
    if not text:
        return text
    swap = {"blue": "red", "red": "blue", "top": "bot", "bot": "top",
            "baron": "dragon", "dragon": "baron"}
    parts = text.split("_")
    out = []
    for i, part in enumerate(parts):
        # `red_buff` / `blue_buff` es el nombre de un campamento, no un dueño.
        if part in ("red", "blue") and i + 1 < len(parts) and parts[i + 1] == "buff":
            out.append(part)
        else:
            out.append(swap.get(part, part))
    return "_".join(out)


def mirror_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Etiquetas de la celda simétrica (azul<->rojo, top<->bot, Barón<->dragón)."""
    out = dict(meta)
    out["owner"] = _SWAP_OWNER.get(meta.get("owner"), meta.get("owner"))
    out["lane"] = _SWAP_LANE.get(meta.get("lane"), meta.get("lane"))
    out["segment"] = _SWAP_SEGMENT.get(meta.get("segment"), meta.get("segment"))
    for key in ("name", "area", "landmark"):
        out[key] = _swap_words(meta.get(key))
    return out


# ---------- codificación de la rejilla ----------

def rle_encode(values: Iterable[int]) -> List[int]:
    """`[v, n, v, n, ...]`: valor y número de repeticiones."""
    out: List[int] = []
    for value in values:
        if out and out[-2] == value:
            out[-1] += 1
        else:
            out.extend((value, 1))
    return out


def rle_decode(runs: List[int]) -> List[int]:
    out: List[int] = []
    for i in range(0, len(runs), 2):
        out.extend([runs[i]] * runs[i + 1])
    return out


class RiftMap:
    """Rejilla de celdas con sus etiquetas. Ver el docstring del módulo."""

    def __init__(self, data: Dict[str, Any]):
        if data.get("schema") != RIFT_CELLS_SCHEMA:
            raise ValueError(f"Esquema de celdas no soportado: {data.get('schema')!r}")
        grid = data["grid"]
        self.origin_x, self.origin_y = grid["origin"]
        self.pixel = grid["pixel"]
        self.width, self.height = grid["width"], grid["height"]
        self.labels: List[int] = rle_decode(grid["labels"])
        if len(self.labels) != self.width * self.height:
            raise ValueError("La rejilla de celdas no cuadra con su tamaño.")
        self.cells: Dict[int, Dict[str, Any]] = {
            int(cid): {"id": int(cid), **meta} for cid, meta in data["cells"].items()}
        self.version = data.get("version")
        self.source = data.get("source")
        self._by_name = {meta["name"]: cid for cid, meta in self.cells.items()}
        self._filled = self._fill_walls()

    # ---------- carga ----------

    _default: Optional["RiftMap"] = None

    @classmethod
    def default(cls) -> "RiftMap":
        """El mapa que viene con la librería (cargado una sola vez)."""
        if cls._default is None:
            cls._default = cls.load(DATA_FILE)
        return cls._default

    @classmethod
    def load(cls, path) -> "RiftMap":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": RIFT_CELLS_SCHEMA,
            "version": self.version,
            "source": self.source,
            "grid": {"origin": [self.origin_x, self.origin_y], "pixel": self.pixel,
                     "width": self.width, "height": self.height,
                     "labels": rle_encode(self.labels)},
            "cells": {str(cid): {k: v for k, v in meta.items() if k != "id"}
                      for cid, meta in sorted(self.cells.items())},
        }

    # ---------- rejilla ----------

    def _pixel_of(self, x: float, y: float) -> Tuple[int, int]:
        col = int((x - self.origin_x) // self.pixel)
        row = int((y - self.origin_y) // self.pixel)
        return (min(max(col, 0), self.width - 1), min(max(row, 0), self.height - 1))

    def _fill_walls(self) -> List[int]:
        """Etiquetas con los muros rellenos con la celda caminable más cercana."""
        filled = list(self.labels)
        queue = deque(i for i, label in enumerate(filled) if label)
        w, h = self.width, self.height
        while queue:
            i = queue.popleft()
            row, col = divmod(i, w)
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                r, c = row + dr, col + dc
                if 0 <= r < h and 0 <= c < w and not filled[r * w + c]:
                    filled[r * w + c] = filled[i]
                    queue.append(r * w + c)
        return filled

    def is_walkable(self, x: float, y: float) -> bool:
        """¿Ha pasado alguien alguna vez por ese punto (según los datos del mapa)?"""
        col, row = self._pixel_of(x, y)
        return bool(self.labels[row * self.width + col])

    # ---------- consultas ----------

    def cell_id_at(self, x: float, y: float) -> int:
        col, row = self._pixel_of(x, y)
        return self._filled[row * self.width + col]

    def cell_at(self, x: float, y: float) -> Dict[str, Any]:
        """Celda en la que cae el punto (la caminable más cercana si es un muro)."""
        return self.cells[self.cell_id_at(x, y)]

    def area_at(self, x: float, y: float) -> str:
        return self.cell_at(x, y)["area"]

    def cell(self, cell: Any) -> Dict[str, Any]:
        """Celda por id o por nombre."""
        if isinstance(cell, str):
            return self.cells[self._by_name[cell]]
        return self.cells[int(cell)]

    def areas(self) -> Dict[str, List[int]]:
        """`{area: [ids de celda]}`."""
        out: Dict[str, List[int]] = {}
        for cid, meta in sorted(self.cells.items()):
            out.setdefault(meta["area"], []).append(cid)
        return out

    def centroid(self, cell: Any) -> Tuple[float, float]:
        """Centro de una celda (media de sus píxeles caminables)."""
        cid = self.cell(cell)["id"]
        xs = ys = n = 0
        for i, label in enumerate(self.labels):
            if label == cid:
                row, col = divmod(i, self.width)
                xs += col
                ys += row
                n += 1
        if not n:
            raise ValueError(f"La celda {cid} no tiene píxeles.")
        return (self.origin_x + (xs / n + 0.5) * self.pixel,
                self.origin_y + (ys / n + 0.5) * self.pixel)

    # ---------- contornos ----------

    def outlines(self, filled: bool = False) -> Dict[int, List[List[Tuple[float, float]]]]:
        """Contorno de cada celda como lista de anillos de puntos (coordenadas
        de mapa). Para dibujar el mapa o exportarlo. Con `filled=True` las
        celdas incluyen los muros asignados (el mapa queda cubierto entero)."""
        labels = self._filled if filled else self.labels
        w, h = self.width, self.height

        def label_at(col, row):
            if 0 <= col < w and 0 <= row < h:
                return labels[row * w + col]
            return 0

        # Aristas orientadas del borde de cada celda (en coordenadas de rejilla),
        # recorridas dejando la celda a la izquierda.
        edges: Dict[int, Dict[Tuple[int, int], List[Tuple[int, int]]]] = {}
        for row in range(h):
            for col in range(w):
                label = labels[row * w + col]
                if not label:
                    continue
                cell_edges = edges.setdefault(label, {})
                if label_at(col, row - 1) != label:
                    cell_edges.setdefault((col + 1, row), []).append((col, row))
                if label_at(col, row + 1) != label:
                    cell_edges.setdefault((col, row + 1), []).append((col + 1, row + 1))
                if label_at(col - 1, row) != label:
                    cell_edges.setdefault((col, row), []).append((col, row + 1))
                if label_at(col + 1, row) != label:
                    cell_edges.setdefault((col + 1, row + 1), []).append((col + 1, row))

        result: Dict[int, List[List[Tuple[float, float]]]] = {}
        for label, cell_edges in edges.items():
            rings = []
            while cell_edges:
                start = next(iter(cell_edges))
                ring = [start]
                point = start
                while True:
                    nexts = cell_edges.get(point)
                    if not nexts:
                        break
                    nxt = nexts.pop()
                    if not nexts:
                        del cell_edges[point]
                    if nxt == start:
                        break
                    ring.append(nxt)
                    point = nxt
                rings.append(_simplify(ring))
            result[label] = [
                [(self.origin_x + c * self.pixel, self.origin_y + r * self.pixel) for c, r in ring]
                for ring in rings if len(ring) >= 3]
        return result


def _simplify(ring: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Quita los vértices intermedios de los tramos rectos."""
    out = []
    n = len(ring)
    for i, point in enumerate(ring):
        prev, nxt = ring[i - 1], ring[(i + 1) % n]
        if (point[0] - prev[0]) * (nxt[1] - point[1]) != (point[1] - prev[1]) * (nxt[0] - point[0]):
            out.append(point)
    return out or ring

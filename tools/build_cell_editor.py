"""Genera la página del editor de celdas con el mapa y el tráfico incrustados.

    python tools/build_cell_editor.py <carpeta con capturas .json.gz> <salida.html>

El editor (`tools/cell_editor.html`) muestra el mapa de celdas de la librería
sobre el tráfico real de jugadores, y permite repintar celdas, editar sus
etiquetas y guardar o exportar el resultado en el formato de
`src/grid_minion/data/rift_cells.json`.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_minion import GameCapture  # noqa: E402
from grid_minion.rift import DATA_FILE  # noqa: E402


def density_string(capture_paths, grid):
    """Tráfico por píxel cuantizado a un carácter 0-9 (escala logarítmica)."""
    ox, oy = grid["origin"]
    pixel, w, h = grid["pixel"], grid["width"], grid["height"]
    counts = [0] * (w * h)
    for path in capture_paths:
        capture = GameCapture.load(path)
        if capture.positions is None:
            continue
        for row in capture.positions.rows():
            if row["alive"]:
                col = int((row["x"] - ox) // pixel)
                r = int((row["y"] - oy) // pixel)
                if 0 <= col < w and 0 <= r < h:
                    counts[r * w + col] += 1
    top = max(counts) or 1
    return "".join(str(min(9, round(9 * math.log1p(c) / math.log1p(top)))) for c in counts)


def main():
    folder, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    density = density_string(sorted(folder.glob("*.json.gz")), data["grid"])
    template = (ROOT / "tools" / "cell_editor.html").read_text(encoding="utf-8")
    page = (template
            .replace("/*__MAP__*/", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
            .replace("/*__DENSITY__*/", density))
    out.write_text(page, encoding="utf-8")
    print(f"{out}: {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

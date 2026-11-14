"""Genera `src/grid_minion/data/rift_cells.json`: el mapa de celdas de la Grieta.

No forma parte de la librería: se ejecuta a mano cuando haga falta rehacer el
mapa (p. ej. si Riot cambia el terreno).

    python tools/build_rift_cells.py <carpeta con capturas .json.gz> [salida.json]

Cómo se construye:
  1. **Lo caminable** sale de los datos: cualquier píxel de 100 x 100 unidades
     por el que haya pasado algún jugador en las capturas. El mapa es
     simétrico, así que se une con su giro de 180° y se tapan agujeros sueltos.
  2. **Semillas**: puntos fijos del mapa (torres, campamentos, fosos) y puntos
     a lo largo de las líneas, el río y los pasillos de jungla, cada uno con
     sus etiquetas. Solo se definen para la mitad azul; la roja es su giro.
  3. **Celdas**: cada píxel caminable va a la semilla más cercana **andando**
     (distancia por el terreno, sin atravesar muros), así las celdas siguen
     los pasillos. La mitad roja se copia de la azul girada, así el mapa es
     exactamente simétrico.

El resultado es un borrador para revisar y retocar con el editor de celdas.
"""
import gzip
import heapq
import json
import math
import sys
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grid_minion import GameCapture, spatial  # noqa: E402
from grid_minion.rift import RIFT_CELLS_SCHEMA, RiftMap, mirror_meta, rle_encode  # noqa: E402

ORIGIN = (-200, -200)
PIXEL = 50
# Con este origen y el centro de simetría medido (spatial.MAP_CENTER =
# 7425, 7450), el giro de 180° lleva cada píxel exactamente a otro píxel.
WIDTH, HEIGHT = 305, 306        # cubre de -200 a 15 050 / 15 100
MIN_SAMPLES = 2                 # muestras para considerar caminable un píxel
VERSION = "2026.11-draft2"


def lane(lane_name, segment, x, y, landmark=None):
    return {"kind": "lane", "owner": None if segment == "laning" else
            ("BLUE" if segment == "blue_back" else "RED"),
            "lane": lane_name, "segment": segment,
            "area": f"lane_{lane_name}_{segment}", "landmark": landmark, "at": (x, y)}


def river(side, x, y, landmark=None):
    return {"kind": "river", "owner": None, "lane": None, "segment": None,
            "area": f"river_{side}", "landmark": landmark, "at": (x, y)}


def jungle(camp, x, y, landmark=None):
    return {"kind": "jungle", "owner": "BLUE", "lane": None, "segment": None,
            "area": f"jungle_blue_{camp}", "landmark": landmark, "at": (x, y)}


def base(x, y, landmark=None):
    return {"kind": "base", "owner": "BLUE", "lane": None, "segment": None,
            "area": "base_blue", "landmark": landmark, "at": (x, y)}


# Torres exteriores azules (del feed): separan "blue_back" de "laning".
BLUE_TOP_OUTER_Y = 10441
BLUE_BOT_OUTER_X = 10504
BLUE_MID_OUTER_T = 6100          # a lo largo de la diagonal x ~ y

# Semillas de la mitad azul (x + y < ~14 800). La mitad roja es su giro.
SEEDS = []

# Línea superior, franja izquierda (x ~ 1050), de la base a la esquina.
# La esquina queda entre la última semilla de esta franja y la primera de la
# franja superior (el giro de la línea inferior): no lleva semilla propia, que
# caería justo sobre la línea que separa las dos mitades del mapa.
for y in range(4700, 12000, 1000):
    SEEDS.append(lane("top", "blue_back" if y < BLUE_TOP_OUTER_Y else "laning", 1050, y))

# Línea inferior, franja de abajo (y ~ 1050).
for x in range(4700, 12000, 1000):
    SEEDS.append(lane("bot", "blue_back" if x < BLUE_BOT_OUTER_X else "laning", x, 1050))

# Línea central, de la base hacia el centro.
for t in (4000, 4900, 5800, 6700):
    SEEDS.append(lane("mid", "blue_back" if t < BLUE_MID_OUTER_T else "laning", t, t + 55))

# Río: puntos sobre la línea del río en la mitad superior (su giro da la
# inferior), el cangrejo superior y el foso del dragón (su giro, el del Barón).
SEEDS += [
    river("top", 3100, 11800, "top_river_lane_entrance"),
    river("top", 3750, 11050),
    river("top", 4400, 9600, "scuttle_top"),
    river("top", 6100, 8700, "top_river_mid_entrance"),
    # Los fosos ocupan un círculo fijo alrededor del objetivo: el resto de
    # semillas del río no pueden recortarlos.
    {"kind": "pit", "owner": None, "lane": None, "segment": None,
     "area": "dragon_pit", "landmark": "dragon_pit", "at": (9870, 4430), "radius": 1150},
]

# Jungla azul: campamentos (coordenadas del feed) y pasillos entre ellos.
SEEDS += [
    jungle("blue_buff", 3821, 7901, "blue_buff"),
    jungle("gromp", 2110, 8450, "gromp"),
    jungle("wolves", 3780, 6443, "wolves"),
    jungle("raptors", 6823, 5507, "raptors"),
    jungle("red_buff", 7765, 4020, "red_buff"),
    jungle("krugs", 8482, 2705, "krugs"),
    jungle("blue_buff", 2800, 9800, "blue_side_top_entrance"),
    jungle("wolves", 2600, 5500, "wolves_base_entrance"),
    jungle("raptors", 5000, 7300, "blue_side_mid_entrance"),
    jungle("red_buff", 8900, 5300, "red_side_river_entrance"),
    jungle("red_buff", 6000, 2800, "red_buff_base_entrance"),
    jungle("krugs", 9800, 2300, "bot_side_entrance"),
]

# Base azul.
SEEDS += [
    base(700, 700, "fountain"),
    base(2400, 2400),
    base(1100, 3600, "top_inhibitor"),
    base(3600, 1100, "bot_inhibitor"),
]


# ---------- rejilla ----------

def pixel_of(x, y):
    col = int((x - ORIGIN[0]) // PIXEL)
    row = int((y - ORIGIN[1]) // PIXEL)
    return min(max(col, 0), WIDTH - 1), min(max(row, 0), HEIGHT - 1)


def mirror_pixel(col, row):
    # Con este origen y tamaño, el giro de 180° es una biyección exacta de píxeles.
    return WIDTH - 1 - col, HEIGHT - 1 - row


def pixel_center(col, row):
    return ORIGIN[0] + (col + 0.5) * PIXEL, ORIGIN[1] + (row + 0.5) * PIXEL


def is_blue_half(col, row):
    x, y = pixel_center(col, row)
    mx, my = pixel_center(*mirror_pixel(col, row))
    # Mitad azul: más cerca de la esquina azul que su píxel simétrico.
    return (x + y) < (mx + my) or ((x + y) == (mx + my) and x <= mx)


def walkable_mask(capture_paths):
    counts = [0] * (WIDTH * HEIGHT)
    games = 0
    for path in capture_paths:
        capture = GameCapture.load(path)
        if capture.positions is None:
            continue
        games += 1
        for row in capture.positions.rows():
            if row["alive"]:
                col, r = pixel_of(row["x"], row["y"])
                counts[r * WIDTH + col] += 1
    mask = [c >= MIN_SAMPLES for c in counts]
    # Simetría: el terreno es el mismo girado 180°.
    for row in range(HEIGHT):
        for col in range(WIDTH):
            mc, mr = mirror_pixel(col, row)
            if mask[mr * WIDTH + mc]:
                mask[row * WIDTH + col] = True
    mask = _drop_specks(mask)
    # Tapar agujeros sueltos (píxeles caminables por los que no pasó nadie).
    for _ in range(1):
        fixed = list(mask)
        for row in range(1, HEIGHT - 1):
            for col in range(1, WIDTH - 1):
                if mask[row * WIDTH + col]:
                    continue
                around = sum(mask[(row + dr) * WIDTH + col + dc]
                             for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc)
                if around >= 6:
                    fixed[row * WIDTH + col] = True
        mask = fixed
    return mask, counts, games


def _drop_specks(mask, min_pixels=40):
    """Quita manchas sueltas de "caminable" dentro de muros (muestras raras:
    campeones que atraviesan muros, saltos). Se queda con las zonas conectadas
    de al menos `min_pixels` píxeles."""
    seen = [False] * len(mask)
    keep = [False] * len(mask)
    for start in range(len(mask)):
        if not mask[start] or seen[start]:
            continue
        component, queue = [], deque([start])
        seen[start] = True
        while queue:
            i = queue.popleft()
            component.append(i)
            row, col = divmod(i, WIDTH)
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c, r = col + dc, row + dr
                j = r * WIDTH + c
                if 0 <= c < WIDTH and 0 <= r < HEIGHT and mask[j] and not seen[j]:
                    seen[j] = True
                    queue.append(j)
        if len(component) >= min_pixels:
            for i in component:
                keep[i] = True
    return keep


def snap(mask, x, y):
    """Píxel caminable más cercano a un punto."""
    col, row = pixel_of(x, y)
    seen = {(col, row)}
    queue = deque([(col, row)])
    while queue:
        c, r = queue.popleft()
        if mask[r * WIDTH + c]:
            return c, r
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < WIDTH and 0 <= nr < HEIGHT and (nc, nr) not in seen:
                seen.add((nc, nr))
                queue.append((nc, nr))
    raise ValueError(f"No hay terreno caminable cerca de {(x, y)}")


def geodesic_labels(mask, seeds):
    """Cada píxel caminable -> semilla más cercana andando (Dijkstra, 8 vecinos)."""
    dist = [math.inf] * (WIDTH * HEIGHT)
    labels = [0] * (WIDTH * HEIGHT)
    heap = []
    for label, (col, row) in seeds.items():
        i = row * WIDTH + col
        dist[i] = 0
        labels[i] = label
        heapq.heappush(heap, (0.0, i))
    steps = [(dc, dr, math.hypot(dc, dr)) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if dc or dr]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        row, col = divmod(i, WIDTH)
        for dc, dr, cost in steps:
            nc, nr = col + dc, row + dr
            if not (0 <= nc < WIDTH and 0 <= nr < HEIGHT):
                continue
            j = nr * WIDTH + nc
            if not mask[j]:
                continue
            # Sin cortar esquinas de muro en diagonal.
            if dc and dr and not (mask[row * WIDTH + nc] or mask[nr * WIDTH + col]):
                continue
            nd = d + cost
            if nd < dist[j]:
                dist[j] = nd
                labels[j] = labels[i]
                heapq.heappush(heap, (nd, j))
    return labels


_KIND_GROUP = {"lane": "lane", "river": "river", "pit": "river", "jungle": "jungle", "base": "base"}

# Campamento que define cada área de jungla (`landmark` de su semilla).
_CAMP_OF_AREA = {"blue_buff": "blue_buff", "gromp": "gromp", "wolves": "wolves",
                 "raptors": "raptors", "red_buff": "red_buff", "krugs": "krugs"}


def _nearest_in(region, col, row):
    """Píxel de `region` (conjunto de índices) más cercano a (col, row)."""
    best, best_d = None, math.inf
    for i in region:
        r, c = divmod(i, WIDTH)
        d = (c - col) ** 2 + (r - row) ** 2
        if d < best_d:
            best, best_d = i, d
    return divmod(best, WIDTH)[::-1]


def hierarchical_labels(mask, cells, seed_pixels):
    """Asigna píxeles a celdas por niveles, para que ninguna celda cruce a otro
    tipo de terreno ni a otra área:

      1. Tipo de terreno de cada píxel (línea, río, jungla, base), con las
         zonas calibradas de `spatial.zone_of`.
      2. En la jungla, cada píxel va al **campamento** más cercano andando: eso
         fija las áreas de campamento, sin que los pasillos las muevan.
      3. Dentro de cada región resultante, cada píxel va a la semilla más
         cercana andando, solo entre las semillas de esa región.

    Lo que quede sin asignar (bolsas desconectadas de su región) va a la
    semilla más cercana andando sin restricciones.
    """
    group_of = [None] * (WIDTH * HEIGHT)
    for i, walkable in enumerate(mask):
        if walkable:
            row, col = divmod(i, WIDTH)
            group_of[i] = spatial.zone_kind(spatial.zone_of(*pixel_center(col, row)))

    labels = [0] * (WIDTH * HEIGHT)
    for group in ("lane", "river", "base", "jungle"):
        region_mask = [g == group for g in group_of]
        region = {i for i, inside in enumerate(region_mask) if inside}
        ids = [cid for cid, meta in cells.items() if _KIND_GROUP[meta["kind"]] == group]

        if group != "jungle":
            subregions = [(region_mask, region, ids)]
        else:
            # Nivel 2: áreas de campamento.
            camp_ids = [cid for cid in ids if cells[cid]["landmark"] in _CAMP_OF_AREA]
            camp_seeds = {}
            for cid in camp_ids:
                col, row = seed_pixels[cid]
                if not region_mask[row * WIDTH + col]:
                    col, row = _nearest_in(region, col, row)
                camp_seeds[cid] = (col, row)
            area_labels = geodesic_labels(region_mask, camp_seeds)
            subregions = []
            for camp in camp_ids:
                area = cells[camp]["area"]
                sub_mask = [area_labels[i] == camp for i in range(len(mask))]
                sub = {i for i, inside in enumerate(sub_mask) if inside}
                sub_ids = [cid for cid in ids if cells[cid]["area"] == area]
                subregions.append((sub_mask, sub, sub_ids))

        # Fosos: un círculo fijo, antes que el resto del río.
        if group == "river":
            for cid in [c for c in ids if cells[c].get("radius")]:
                cx, cy = cells[cid]["center"]
                radius = cells[cid]["radius"]
                for i in list(region):
                    row, col = divmod(i, WIDTH)
                    x, y = pixel_center(col, row)
                    if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                        labels[i] = cid
                        region.discard(i)
                        region_mask[i] = False
            ids = [c for c in ids if not cells[c].get("radius")]
            subregions = [(region_mask, region, ids)]

        # Nivel 3: celdas dentro de cada región.
        for sub_mask, sub, sub_ids in subregions:
            if not sub or not sub_ids:
                continue
            seeds = {}
            for cid in sub_ids:
                col, row = seed_pixels[cid]
                if not sub_mask[row * WIDTH + col]:
                    col, row = _nearest_in(sub, col, row)
                seeds[cid] = (col, row)
            for i, label in enumerate(geodesic_labels(sub_mask, seeds)):
                if label:
                    labels[i] = label

    # Bolsas sin asignar.
    leftover = [i for i, walkable in enumerate(mask) if walkable and not labels[i]]
    if leftover:
        propagated = _propagate(mask, labels)
        for i in leftover:
            labels[i] = propagated[i]
    return labels


def _propagate(mask, labels):
    """Extiende las etiquetas existentes a los píxeles caminables sin etiqueta."""
    out = list(labels)
    queue = deque(i for i, label in enumerate(out) if label)
    while queue:
        i = queue.popleft()
        row, col = divmod(i, WIDTH)
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c, r = col + dc, row + dr
            j = r * WIDTH + c
            if 0 <= c < WIDTH and 0 <= r < HEIGHT and mask[j] and not out[j]:
                out[j] = out[i]
                queue.append(j)
    return out


def build(capture_paths):
    mask, counts, games = walkable_mask(capture_paths)

    # Celdas: id impar = semilla azul, el par siguiente = su giro.
    cells, mirror_of, seed_pixels = {}, {}, {}
    for k, seed in enumerate(SEEDS):
        blue_id, red_id = 2 * k + 1, 2 * k + 2
        meta = {key: seed[key] for key in ("kind", "owner", "lane", "segment", "area", "landmark")}
        cells[blue_id] = meta
        cells[red_id] = mirror_meta(meta)
        if seed.get("radius"):
            for cid, center in ((blue_id, seed["at"]), (red_id, spatial.rotate(*seed["at"]))):
                cells[cid]["radius"] = seed["radius"]
                cells[cid]["center"] = center
        mirror_of[blue_id], mirror_of[red_id] = red_id, blue_id
        seed_pixels[blue_id] = snap(mask, *seed["at"])
        seed_pixels[red_id] = snap(mask, *spatial.rotate(*seed["at"]))

    labels = hierarchical_labels(mask, cells, seed_pixels)

    # Simetría exacta: la mitad roja es la azul girada.
    for row in range(HEIGHT):
        for col in range(WIDTH):
            if is_blue_half(col, row):
                continue
            mc, mr = mirror_pixel(col, row)
            source = labels[mr * WIDTH + mc]
            labels[row * WIDTH + col] = mirror_of[source] if source else 0

    # Nombres: área + índice, en el orden de las semillas.
    per_area = Counter()
    for cid in sorted(cells):
        area = cells[cid]["area"]
        per_area[area] += 1
        cells[cid]["name"] = f"{area}_{per_area[area]}"
        cells[cid]["mirror"] = mirror_of[cid]
    for cid, meta in cells.items():
        if meta["landmark"]:
            meta["name"] = meta["landmark"] if meta["kind"] in ("pit",) else \
                f"{meta['area']}_{meta['landmark']}" if meta["kind"] == "lane" else \
                (f"jungle_{meta['owner'].lower()}_{meta['landmark']}" if meta["kind"] == "jungle"
                 else f"{meta['area']}_{meta['landmark']}")
    assert len({m["name"] for m in cells.values()}) == len(cells), "nombres repetidos"

    return {
        "schema": RIFT_CELLS_SCHEMA,
        "version": VERSION,
        "source": f"generado con tools/build_rift_cells.py a partir de {games} partidas",
        "grid": {"origin": list(ORIGIN), "pixel": PIXEL, "width": WIDTH, "height": HEIGHT,
                 "labels": rle_encode(labels)},
        "cells": {str(cid): {key: meta[key] for key in
                             ("name", "kind", "owner", "lane", "segment", "area",
                              "landmark", "mirror")}
                  for cid, meta in sorted(cells.items())},
    }, counts


def main():
    folder = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "src" / "grid_minion" / "data" / "rift_cells.json"
    paths = sorted(folder.glob("*.json.gz"))
    data, _ = build(paths)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n",
                   encoding="utf-8")
    rift = RiftMap(data)
    sizes = Counter(rift.labels)
    sizes.pop(0, None)
    print(f"{out}: {len(rift.cells)} celdas, {len(rift.areas())} áreas, "
          f"{sum(sizes.values())} píxeles caminables, celda menor {min(sizes.values())} px, "
          f"mayor {max(sizes.values())} px, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

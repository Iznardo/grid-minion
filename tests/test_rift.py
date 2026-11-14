import json
import unittest

from grid_minion import RiftMap, build_capture, iter_jsonl, spatial
from grid_minion.rift import mirror_meta, rle_decode, rle_encode

from test_contract_gridextractor import REAL

# Coordenadas reales de los campamentos del equipo azul (del feed).
BLUE_CAMPS = {
    "blue_buff": (3821, 7901), "gromp": (2110, 8450), "wolves": (3780, 6443),
    "raptors": (6823, 5507), "red_buff": (7765, 4020), "krugs": (8482, 2705),
}


class TestRiftMapData(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rift = RiftMap.default()

    def test_carga_y_metadatos(self):
        rift = self.rift
        self.assertGreaterEqual(len(rift.cells), 60)
        self.assertEqual(len(rift.labels), rift.width * rift.height)
        kinds = {meta["kind"] for meta in rift.cells.values()}
        self.assertEqual(kinds, {"lane", "river", "pit", "jungle", "base"})
        names = [meta["name"] for meta in rift.cells.values()]
        self.assertEqual(len(names), len(set(names)))

    def test_todas_las_celdas_tienen_pixeles(self):
        used = set(self.rift.labels) - {0}
        self.assertEqual(used, set(self.rift.cells))

    def test_areas_esperadas(self):
        areas = set(self.rift.areas())
        expected = {"base_blue", "base_red", "river_top", "river_bot", "baron_pit", "dragon_pit"}
        for lane in ("top", "mid", "bot"):
            for segment in ("blue_back", "laning", "red_back"):
                expected.add(f"lane_{lane}_{segment}")
        for owner in ("blue", "red"):
            for camp in BLUE_CAMPS:
                expected.add(f"jungle_{owner}_{camp}")
        self.assertEqual(areas, expected)

    def test_simetria_exacta(self):
        rift = self.rift
        for cid, meta in rift.cells.items():
            mirror = rift.cells[meta["mirror"]]
            with self.subTest(cell=meta["name"]):
                self.assertEqual(mirror["mirror"], cid)
                expected = mirror_meta(meta)
                for key in ("kind", "owner", "lane", "segment", "area", "landmark"):
                    self.assertEqual(mirror[key], expected[key])
        # Píxel a píxel: cada píxel y su giro tienen celdas simétricas.
        for i, label in enumerate(rift.labels):
            if not label:
                continue
            row, col = divmod(i, rift.width)
            mirrored = rift.labels[(rift.height - 1 - row) * rift.width + (rift.width - 1 - col)]
            self.assertEqual(mirrored, rift.cells[label]["mirror"])

    def test_campamentos_y_fosos_caen_en_su_area(self):
        rift = self.rift
        for camp, (x, y) in BLUE_CAMPS.items():
            with self.subTest(camp=camp):
                self.assertEqual(rift.area_at(x, y), f"jungle_blue_{camp}")
                rx, ry = spatial.rotate(x, y)
                self.assertEqual(rift.area_at(rx, ry), f"jungle_red_{camp}")
        self.assertEqual(rift.area_at(9870, 4430), "dragon_pit")
        self.assertEqual(rift.area_at(5007, 10471), "baron_pit")
        self.assertEqual(rift.area_at(700, 700), "base_blue")
        self.assertEqual(rift.area_at(14100, 14100), "base_red")

    def test_muros_devuelven_la_celda_caminable_mas_cercana(self):
        rift = self.rift
        walls = [i for i, label in enumerate(rift.labels) if not label]
        self.assertTrue(walls)
        row, col = divmod(walls[len(walls) // 2], rift.width)
        x = rift.origin_x + (col + 0.5) * rift.pixel
        y = rift.origin_y + (row + 0.5) * rift.pixel
        self.assertFalse(rift.is_walkable(x, y))
        self.assertIn(rift.cell_at(x, y)["id"], rift.cells)
        # Fuera del mapa también responde.
        self.assertIn(rift.cell_at(-5000, 99999)["id"], rift.cells)

    def test_contornos_y_centroides(self):
        rift = self.rift
        outlines = rift.outlines()
        self.assertEqual(set(outlines), set(rift.cells))
        for rings in outlines.values():
            self.assertTrue(rings)
            self.assertTrue(all(len(ring) >= 4 for ring in rings))
        cx, cy = rift.centroid("dragon_pit")
        self.assertLess(spatial.distance({"x": cx, "y": cy}, {"x": 9870, "y": 4430}), 400)

    def test_serializacion(self):
        self.assertEqual(rle_decode(rle_encode([0, 0, 3, 3, 3, 1])), [0, 0, 3, 3, 3, 1])
        data = json.loads(json.dumps(self.rift.to_dict()))
        again = RiftMap(data)
        self.assertEqual(again.labels, self.rift.labels)
        self.assertEqual(again.cells, self.rift.cells)

    def test_esquema_no_soportado(self):
        with self.assertRaises(ValueError):
            RiftMap({"schema": "otro/v9"})


class TestRiftMapOnRealGame(unittest.TestCase):
    """Calibración con la partida real del fixture."""

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(iter_jsonl(REAL / "riot_livestats.jsonl.gz"))
        cls.rift = RiftMap.default()

    def test_casi_todas_las_posiciones_son_caminables(self):
        samples = [s for pid in self.capture.players
                   for s in self.capture.positions_between(pid, 0, 3000) if s["alive"]]
        off = sum(not self.rift.is_walkable(s["x"], s["y"]) for s in samples)
        # Bordes de muro a 50 unidades por píxel: ~1-2 %. Se resuelven igual
        # (la celda caminable más cercana).
        self.assertLess(off / len(samples), 0.03)

    def test_jugadores_de_linea_en_su_linea(self):
        expected = {"TOP": "top", "MID": "mid", "ADC": "bot"}
        for pid, meta in self.capture.players.items():
            lane = expected.get(meta["role"])
            if lane is None:
                continue
            samples = [s for s in self.capture.positions_between(pid, 120, 300) if s["alive"]]
            in_lane = sum(self.rift.cell_at(s["x"], s["y"])["lane"] == lane for s in samples)
            with self.subTest(pid=pid, role=meta["role"]):
                self.assertGreater(in_lane / len(samples), 0.75)

    def test_objetivos_en_sus_fosos(self):
        for event in self.capture.get_events("monster_kill"):
            monster = event["detail"]["monster"]
            if monster in ("dragon", "baron"):
                with self.subTest(t=event["t"]):
                    self.assertEqual(self.rift.area_at(event["x"], event["y"]), f"{monster}_pit")


if __name__ == "__main__":
    unittest.main()

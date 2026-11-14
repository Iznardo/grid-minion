import unittest

from grid_minion import build_capture, iter_jsonl, spatial
from grid_minion.observers import JUNGLE_CAMPS

from test_contract_gridextractor import REAL


class TestZones(unittest.TestCase):

    def test_puntos_conocidos(self):
        cases = {
            (500, 500): "base_blue",
            (14300, 14400): "base_red",
            (7400, 7400): "lane_mid",
            (1000, 10400): "lane_top",        # torre exterior superior azul
            (4300, 13900): "lane_top",        # torre exterior superior roja
            (2200, 12400): "lane_top",        # esquina donde gira la línea
            (10500, 1000): "lane_bot",
            (13900, 4500): "lane_bot",
            (12500, 2400): "lane_bot",        # esquina inferior
            (5000, 10500): "river_top",       # foso del Barón
            (9900, 4400): "river_bot",        # foso del dragón
            (3800, 7900): "jungle_blue_top",  # azul del equipo azul
            (7800, 4000): "jungle_blue_bot",  # rojo del equipo azul
            (7000, 10900): "jungle_red_top",  # rojo del equipo rojo
            (11000, 7000): "jungle_red_bot",  # azul del equipo rojo
        }
        for (x, y), expected in cases.items():
            with self.subTest(point=(x, y)):
                self.assertEqual(spatial.zone_of(x, y), expected)

    def test_toda_zona_devuelta_es_conocida(self):
        for x in range(0, 15000, 500):
            for y in range(0, 15000, 500):
                self.assertIn(spatial.zone_of(x, y), spatial.ZONES)

    def test_helpers_de_zona(self):
        self.assertEqual(spatial.zone_kind("jungle_red_top"), "jungle")
        self.assertEqual(spatial.lane_of("lane_mid"), "mid")
        self.assertIsNone(spatial.lane_of("river_top"))
        self.assertEqual(spatial.zone_owner("base_red"), "RED")
        self.assertIsNone(spatial.zone_owner("lane_top"))
        self.assertTrue(spatial.is_enemy_jungle("jungle_red_top", "BLUE"))
        self.assertFalse(spatial.is_enemy_jungle("jungle_red_top", "RED"))
        self.assertFalse(spatial.is_enemy_jungle("river_top", "BLUE"))
        self.assertTrue(spatial.is_own_base("base_blue", "BLUE"))
        self.assertFalse(spatial.is_own_base("base_blue", "RED"))


class TestTransforms(unittest.TestCase):

    def test_normalize(self):
        self.assertEqual(spatial.normalize(spatial.MAP_MIN_X, spatial.MAP_MIN_Y), (0.0, 0.0))
        self.assertEqual(spatial.normalize(spatial.MAP_MAX_X, spatial.MAP_MAX_Y), (1.0, 1.0))

    def test_rotate_lleva_cada_cosa_a_su_equivalente(self):
        self.assertEqual(spatial.rotate(*spatial.rotate(1234, 5678)), (1234, 5678))
        self.assertEqual(spatial.zone_of(*spatial.rotate(14300, 14400)), "base_blue")
        # El rojo del equipo rojo cae sobre el rojo del azul.
        self.assertEqual(spatial.zone_of(*spatial.rotate(7000, 10900)), "jungle_blue_bot")
        # Las líneas laterales se intercambian.
        self.assertEqual(spatial.zone_of(*spatial.rotate(1000, 10400)), "lane_bot")

    def test_to_blue_perspective_solo_gira_al_rojo(self):
        self.assertEqual(spatial.to_blue_perspective(100, 200, "BLUE"), (100, 200))
        self.assertEqual(spatial.to_blue_perspective(100, 200, "RED"), spatial.rotate(100, 200))

    def test_distance(self):
        self.assertEqual(spatial.distance({"x": 0, "y": 0}, {"x": 3, "y": 4}), 5.0)


class TestZonesOnRealGame(unittest.TestCase):
    """Calibración: las zonas deben cuadrar con lo que el propio feed dice."""

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(iter_jsonl(REAL / "riot_livestats.jsonl.gz"))

    def test_campamentos_en_jungla_y_coinciden_con_el_flag_del_feed(self):
        camps = [e for e in self.capture.get_events("monster_kill")
                 if e["detail"]["monster"] in JUNGLE_CAMPS - {"scuttlecrab"}
                 and e["pid"] is not None and e["x"] is not None]
        self.assertGreater(len(camps), 80)
        wrong = 0
        for e in camps:
            zone = spatial.zone_of(e["x"], e["y"])
            enemy = spatial.is_enemy_jungle(zone, e["side"])
            if not zone.startswith("jungle_") or enemy != bool(e["detail"]["in_enemy_jungle"]):
                wrong += 1
        # Tolerancia: campamentos rematados desde el borde de una línea.
        self.assertLessEqual(wrong / len(camps), 0.05)

    def test_objetivos_en_el_rio(self):
        zones = {e["detail"]["monster"]: spatial.zone_of(e["x"], e["y"])
                 for e in self.capture.get_events("monster_kill")
                 if e["detail"]["monster"] in ("dragon", "baron", "riftherald")}
        self.assertEqual(zones, {"dragon": "river_bot", "baron": "river_top",
                                 "riftherald": "river_top"})

    def test_jugadores_de_linea_en_su_linea_durante_la_fase_de_lineas(self):
        expected = {"TOP": "lane_top", "MID": "lane_mid", "ADC": "lane_bot"}
        for pid, meta in self.capture.players.items():
            lane = expected.get(meta["role"])
            if lane is None:
                continue
            samples = [s for s in self.capture.positions_between(pid, 120, 300) if s["alive"]]
            in_lane = sum(spatial.zone_of(s["x"], s["y"]) == lane for s in samples)
            with self.subTest(pid=pid, role=meta["role"]):
                self.assertGreater(in_lane / len(samples), 0.7)

    def test_todos_empiezan_en_su_base(self):
        for pid, meta in self.capture.players.items():
            pos = self.capture.position_at(pid, 5)
            self.assertTrue(spatial.is_own_base(spatial.zone_of(pos["x"], pos["y"]), meta["side"]))


if __name__ == "__main__":
    unittest.main()

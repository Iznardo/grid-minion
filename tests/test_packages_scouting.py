"""Tests de los paquetes `support_openings` y `proximity`."""
import json
import unittest

from grid_minion import RiftMap, build_capture, iter_jsonl
from grid_minion.packages import proximity, support_openings

from test_contract_gridextractor import REAL
from test_packages import BLUE_BASE, MID_LANE, RED_BASE, _still, make_capture

TOP_LANE = (1050, 11400)     # lane_top_laning
BOT_LANE = (12100, 1100)     # lane_bot_laning


class TestNeighbours(unittest.TestCase):

    def test_vecinas_simetricas_y_cercania(self):
        rift = RiftMap.default()
        raptors = rift.cell("jungle_blue_raptors")["id"]
        neighbours = rift.neighbours(raptors)
        self.assertIn(rift.cell("jungle_blue_red_buff")["id"], neighbours)
        self.assertNotIn(raptors, neighbours)
        for cid in neighbours:
            self.assertIn(raptors, rift.neighbours(cid))
        self.assertTrue(rift.are_near(raptors, raptors))
        self.assertTrue(rift.are_near(raptors, neighbours[0]))
        fountain = rift.cell("base_red_fountain")["id"]
        self.assertFalse(rift.are_near(raptors, fountain))


class TestProximitySynthetic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Jungla azul: 7 min en mid, 8 min arriba, el resto abajo con el ADC.
        jungler = _still(MID_LANE, 420) + _still(TOP_LANE, 480) + _still(BOT_LANE, 100)
        cls.capture = make_capture({
            1: _still(TOP_LANE, 1000), 2: jungler, 3: _still(MID_LANE, 1000),
            4: _still(BOT_LANE, 1000), 5: _still(BOT_LANE, 1000),
        })
        cls.package = proximity(cls.capture)

    def _player(self, side, role):
        team = next(t for t in self.package["teams"] if t["side"] == side)
        return next(p for p in team["players"] if p["role"] == role)

    def test_jungla_por_fases(self):
        jungler = self._player("BLUE", "JUNGLE")
        self.assertEqual([p["phase"] for p in jungler["phases"]], ["0-7", "7-15", "15+"])
        shares = [{role: v["share"] for role, v in p["near"].items()} for p in jungler["phases"]]
        self.assertEqual(shares[0], {"TOP": 0.0, "MID": 1.0, "ADC": 0.0, "SUPPORT": 0.0})
        self.assertEqual(shares[1], {"TOP": 1.0, "MID": 0.0, "ADC": 0.0, "SUPPORT": 0.0})
        self.assertEqual(shares[2], {"TOP": 0.0, "MID": 0.0, "ADC": 1.0, "SUPPORT": 1.0})
        self.assertEqual(jungler["phases"][0]["near"]["MID"]["seconds"], 420.0)
        self.assertEqual(list(jungler["phases"][0]["near"]), ["TOP", "MID", "ADC", "SUPPORT"])

    def test_support_pegado_a_su_adc(self):
        support = self._player("BLUE", "SUPPORT")
        for phase in support["phases"]:
            self.assertEqual(phase["near"]["ADC"]["share"], 1.0)
        self.assertEqual(support["phases"][2]["near"]["JUNGLE"]["share"], 1.0)

    def test_serie_por_minuto(self):
        jungler = self._player("BLUE", "JUNGLE")
        self.assertEqual(jungler["minutes"]["MID"][:7], [1.0] * 7)
        self.assertEqual(jungler["minutes"]["TOP"][7:15], [1.0] * 8)

    def test_duo_jungla_support(self):
        team = next(t for t in self.package["teams"] if t["side"] == "BLUE")
        duo = team["duo"]
        self.assertEqual((duo["jungler_pid"], duo["support_pid"]), (2, 5))
        self.assertEqual([p["share"] for p in duo["phases"]], [0.0, 0.0, 1.0])
        self.assertEqual(len(duo["together"]), 1)
        together = duo["together"][0]
        self.assertEqual((together["t_enter"], together["kind"]), (900.0, "other"))
        self.assertIn("lane_bot_laning", together["areas"])
        self.assertIn("lane_bot_laning", duo["areas"])

    def test_muertes_no_rebajan_la_cuota(self):
        capture = make_capture({2: _still(MID_LANE, 100), 3: _still(MID_LANE, 100)})
        data = capture.to_dict()
        data["positions"]["alive"]["3"] = [1] * 50 + [0] * 50   # el mid muere a mitad
        from grid_minion import GameCapture
        jungler = next(p for p in proximity(GameCapture.from_dict(data))["teams"][0]["players"]
                       if p["role"] == "JUNGLE")
        near_mid = jungler["phases"][0]["near"]["MID"]
        self.assertEqual(near_mid["both_alive_s"], 50.0)
        self.assertEqual(near_mid["share"], 1.0)


class TestSupportOpeningsSynthetic(unittest.TestCase):

    def test_llegada_roam_con_jungla_y_kill(self):
        support = (_still(BLUE_BASE, 20) + _still(BOT_LANE, 130) + _still((7300, 7300), 21)
                   + _still(BOT_LANE, 80))
        capture = make_capture(
            {2: _still(MID_LANE, 251), 3: _still(MID_LANE, 251), 4: _still(BOT_LANE, 251),
             5: support},
            events=[{"t": 160.0, "kind": "kill", "pid": 5, "x": 7300, "y": 7300,
                     "detail": {"victim": 8, "assistants": [2]}}])
        sup = next(s for s in support_openings(capture)["supports"] if s["pid"] == 5)
        self.assertEqual(sup["own_lane"], "bot")
        self.assertEqual(sup["lane_arrival"], {"t": 20.0, "area": "lane_bot_laning"})
        self.assertEqual(len(sup["roams"]), 1)
        roam = sup["roams"][0]
        self.assertEqual((roam["type"], roam["target_area"], roam["target_lane"]),
                         ("roam", "lane_mid_laning", "mid"))
        self.assertEqual((roam["t_enter"], roam["t_exit"]), (150.0, 170.0))
        self.assertEqual(roam["with_jungler_s"], 21)
        self.assertEqual(roam["result"], "kill")

    def test_leash_y_paso_por_la_linea_no_son_roams(self):
        # Sale de base por la línea (3 s), ayuda al jungla (leash) y llega a línea.
        support = (_still(BLUE_BASE, 10) + _still(BOT_LANE, 3) + _still((7800, 4000), 40)
                   + _still(BOT_LANE, 120))
        capture = make_capture({4: _still(BOT_LANE, 173), 5: support})
        sup = next(s for s in support_openings(capture)["supports"] if s["pid"] == 5)
        self.assertEqual(sup["lane_arrival"]["t"], 53.0)
        self.assertEqual(sup["roams"], [])

    def test_ir_al_rio_de_su_linea_es_river(self):
        support = _still(BOT_LANE, 60) + _still((9870, 4430), 15) + _still(BOT_LANE, 60)
        capture = make_capture({4: _still(BOT_LANE, 135), 5: support})
        roam = support_openings(capture)["supports"][0]["roams"][0]
        self.assertEqual((roam["type"], roam["target_area"]), ("river", "dragon_pit"))

    def test_volver_a_base_andando_no_es_roam(self):
        support = _still(BOT_LANE, 60) + _still(BLUE_BASE, 30) + _still(BOT_LANE, 60)
        capture = make_capture({4: _still(BOT_LANE, 150), 5: support})
        self.assertEqual(support_openings(capture)["supports"][0]["roams"], [])


class TestScoutingPackagesOnRealGame(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(iter_jsonl(REAL / "riot_livestats.jsonl.gz"),
                                    series_id="2922522", game_number=1)

    def test_proximity(self):
        package = json.loads(json.dumps(proximity(self.capture)))
        self.assertEqual(package["schema"], "grid-minion/package/proximity/v1")
        for team in package["teams"]:
            roles = [p["role"] for p in team["players"]]
            self.assertEqual(roles, ["JUNGLE", "SUPPORT"])
            support = team["players"][1]
            # Fase de líneas: el support juega pegado a su ADC.
            self.assertGreater(support["phases"][0]["near"]["ADC"]["share"], 0.6)
            self.assertEqual(team["duo"]["together"][0]["kind"], "lvl1")

    def test_support_openings(self):
        package = json.loads(json.dumps(support_openings(self.capture)))
        self.assertEqual(package["schema"], "grid-minion/package/support_openings/v1")
        self.assertEqual([s["champion"] for s in package["supports"]], ["Karma", "Renata"])
        for sup in package["supports"]:
            self.assertEqual(sup["own_lane"], "bot")
            self.assertTrue(40 <= sup["lane_arrival"]["t"] <= 80)
            self.assertEqual(sorted(sup["level_one"]), ["30", "60", "85"])
            self.assertIsNotNone(sup["first_recall"])


if __name__ == "__main__":
    unittest.main()

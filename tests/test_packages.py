import json
import unittest

from grid_minion import GameCapture, build_capture, iter_jsonl
from grid_minion.capture import CAPTURE_SCHEMA
from grid_minion.exceptions import GridDataError
from grid_minion.packages import jungle_pathing, level_one, recall_openings

from test_contract_gridextractor import REAL

# Puntos de referencia (ver test_spatial).
BLUE_BASE = (1000, 1000)
BLUE_JUNGLE = (3800, 7900)      # jungle_blue_top
RED_JUNGLE = (7000, 10900)      # jungle_red_top
MID_LANE = (7400, 7400)
RED_BASE = (14000, 14000)

ROLES = {1: "TOP", 2: "JUNGLE", 3: "MID", 4: "ADC", 5: "SUPPORT"}


def make_capture(paths, events=(), seconds=None):
    """Captura sintética. `paths`: {pid: [(x, y) por segundo]}.

    Los jugadores sin trayectoria se quedan quietos en su base.
    """
    seconds = seconds or max(len(p) for p in paths.values())
    players, xs, ys, alive = {}, {}, {}, {}
    for pid in range(1, 11):
        side = "BLUE" if pid <= 5 else "RED"
        players[str(pid)] = {"name": f"P{pid}", "champion": f"Champ{pid}", "side": side,
                             "team_id": 100 if pid <= 5 else 200,
                             "role": ROLES[pid if pid <= 5 else pid - 5]}
        home = BLUE_BASE if side == "BLUE" else RED_BASE
        path = list(paths.get(pid, []))
        path += [path[-1] if path else home] * (seconds - len(path))
        xs[str(pid)] = [p[0] for p in path]
        ys[str(pid)] = [p[1] for p in path]
        alive[str(pid)] = [1] * seconds
    event_list = [{"x": None, "y": None, "side": None, "detail": {}, **e}
                  for e in sorted(events, key=lambda e: e["t"])]
    return GameCapture.from_dict({
        "schema": CAPTURE_SCHEMA,
        "game": {"series_id": "test", "game_number": 1, "duration_s": float(seconds)},
        "players": players,
        "positions": {"t_ms": [i * 1000 for i in range(seconds)],
                      "x": xs, "y": ys, "alive": alive},
        "events": event_list,
    })


def _still(point, seconds):
    return [point] * seconds


class TestJunglePathingSynthetic(unittest.TestCase):

    def _jungler(self, package, pid=2):
        return next(j for j in package["junglers"] if j["pid"] == pid)

    def test_gank_en_linea_con_rival_kill_y_summoner(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 10) + _still((7300, 7300), 8) + _still(BLUE_JUNGLE, 10),
                   8: _still(MID_LANE, 28)},
            events=[
                {"t": 14.0, "kind": "summoner_used", "pid": 8, "detail": {"spell": "SummonerFlash"}},
                {"t": 15.0, "kind": "kill", "pid": 2, "x": 7400, "y": 7400,
                 "detail": {"victim": 8, "assistants": [3]}},
            ])
        jungler = self._jungler(jungle_pathing(capture, until=28))
        self.assertEqual(len(jungler["lane_visits"]), 1)
        self.assertEqual(jungler["ganks"], jungler["lane_visits"])
        gank = jungler["ganks"][0]
        self.assertEqual(gank["lane"], "mid")
        self.assertEqual(gank["lane_position"], "laning")
        self.assertEqual(gank["areas"], ["lane_mid_laning"])
        self.assertEqual((gank["t_enter"], gank["t_exit"]), (10.0, 17.0))
        self.assertEqual(gank["came_from"], "jungle_blue_blue_buff")
        self.assertEqual(gank["went_to"], "jungle_blue_blue_buff")
        self.assertFalse(gank["crossed"])
        self.assertEqual([(e["pid"], e["role"], e["seconds"]) for e in gank["enemies_present"]],
                         [(8, "MID", 8)])
        self.assertEqual(gank["result"], "kill")
        self.assertEqual([s["spell"] for s in gank["enemy_summoners"]], ["SummonerFlash"])

    def test_cruzar_mid_sin_rivales_es_visita_pero_no_gank(self):
        # De la jungla de arriba (azul) a la de abajo (rojo azul) pasando por mid.
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still((5000, 5000), 3) + _still((7800, 4000), 5)})
        jungler = self._jungler(jungle_pathing(capture, until=13))
        self.assertEqual(jungler["ganks"], [])
        self.assertEqual(len(jungler["lane_visits"]), 1)
        visit = jungler["lane_visits"][0]
        self.assertEqual((visit["lane"], visit["lane_position"]), ("mid", "defensive"))
        self.assertTrue(visit["crossed"])
        self.assertFalse(visit["gank"])
        self.assertEqual(visit["enemies_present"], [])
        self.assertEqual(visit["result"], "none")
        self.assertEqual(visit["came_from"], "jungle_blue_blue_buff")
        self.assertEqual(visit["went_to"], "jungle_blue_red_buff")

    def test_rival_presente_menos_de_min_enemy_s_no_es_gank(self):
        # El rival solo coincide 1 s en el área de línea.
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still((7300, 7300), 6) + _still(BLUE_JUNGLE, 5),
                   8: _still(RED_BASE, 7) + [MID_LANE] + _still(RED_BASE, 8)})
        jungler = self._jungler(jungle_pathing(capture, until=16))
        self.assertEqual(len(jungler["lane_visits"]), 1)
        self.assertEqual(jungler["lane_visits"][0]["enemies_present"][0]["seconds"], 1)
        self.assertEqual(jungler["ganks"], [])
        relaxed = self._jungler(jungle_pathing(capture, until=16, min_enemy_s=1))
        self.assertEqual(len(relaxed["ganks"]), 1)

    def test_rival_en_otra_area_de_la_linea_no_cuenta(self):
        # Jungla en la zona de línea de mid; el rival, bajo sus torres.
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still((7300, 7300), 6) + _still(BLUE_JUNGLE, 5),
                   8: _still((9400, 9400), 16)})
        jungler = self._jungler(jungle_pathing(capture, until=16))
        self.assertEqual(jungler["lane_visits"][0]["enemies_present"], [])
        self.assertEqual(jungler["ganks"], [])

    def test_dive_bajo_las_torres_rivales(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still((9300, 9300), 6) + _still(BLUE_JUNGLE, 5),
                   8: _still((9400, 9400), 16)})
        gank = self._jungler(jungle_pathing(capture, until=16))["ganks"][0]
        self.assertEqual(gank["lane_position"], "dive")
        self.assertEqual(gank["areas"], ["lane_mid_red_back"])

    def test_invade_por_areas_con_campamento_robado(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still(RED_JUNGLE, 6) + _still(BLUE_JUNGLE, 5),
                   7: _still((7000, 12400), 16)},
            events=[
                {"t": 8.0, "kind": "monster_kill", "pid": 2, "x": 7000, "y": 10900,
                 "detail": {"monster": "redcamp", "in_enemy_jungle": True}},
                {"t": 9.0, "kind": "ward_placed", "pid": 2, "x": 7000, "y": 10900, "detail": {}},
            ])
        jungler = self._jungler(jungle_pathing(capture, until=16))
        self.assertEqual(len(jungler["invades"]), 1)
        invade = jungler["invades"][0]
        self.assertEqual(invade["areas"], ["jungle_red_red_buff"])
        self.assertEqual((invade["t_enter"], invade["t_exit"]), (5.0, 10.0))
        self.assertEqual(invade["camps_taken"], ["redcamp"])
        self.assertEqual(invade["wards_placed"], 1)
        self.assertEqual(invade["min_enemy_jungler_d"], 1500)
        self.assertEqual(jungler["camps"][0]["area"], "jungle_red_red_buff")
        # El jungla rojo en su propia jungla no está invadiendo.
        self.assertEqual(self._jungler(jungle_pathing(capture, until=16), pid=7)["invades"], [])

    def test_route_por_celdas_y_por_areas(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 5) + _still((5000, 5000), 3) + _still((7800, 4000), 5)})
        jungler = self._jungler(jungle_pathing(capture, until=13))
        self.assertEqual([a["area"] for a in jungler["area_route"]],
                         ["jungle_blue_blue_buff", "lane_mid_blue_back", "jungle_blue_red_buff"])
        self.assertEqual(jungler["area_route"][1]["t_enter"], 5.0)
        self.assertEqual(jungler["area_route"][1]["t_exit"], 7.0)
        self.assertEqual(len(jungler["route"]), 3)
        self.assertTrue(all("cell" in step and "area" in step for step in jungler["route"]))

    def test_ventana_hasta_el_primer_recall(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 40)},
            events=[
                {"t": 10.0, "kind": "channel_start", "pid": 2, "detail": {"type": "recall"}},
                {"t": 12.0, "kind": "channel_end", "pid": 2,
                 "detail": {"type": "recall", "interrupted": True}},
                {"t": 20.0, "kind": "channel_start", "pid": 2, "detail": {"type": "recall"}},
                {"t": 28.0, "kind": "channel_end", "pid": 2,
                 "detail": {"type": "recall", "interrupted": False}},
                {"t": 15.0, "kind": "level_up", "pid": 2, "detail": {"level": 3}},
            ])
        jungler = self._jungler(jungle_pathing(capture))
        self.assertEqual(jungler["window"], {"t0": 0, "t1": 28.0, "reason": "first_recall"})
        self.assertEqual(jungler["first_recall"], {"t_start": 20.0, "t_end": 28.0, "level": 3})
        self.assertEqual(jungler["path"][-1]["t"], 28.0)

    def test_ventana_se_corta_si_muere_antes(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 40)},
            events=[
                {"t": 9.0, "kind": "kill", "pid": 7, "detail": {"victim": 2, "assistants": []}},
                {"t": 28.0, "kind": "channel_end", "pid": 2,
                 "detail": {"type": "recall", "interrupted": False}},
            ])
        self.assertEqual(self._jungler(jungle_pathing(capture))["window"]["reason"], "first_death")

    def test_sin_recall_ni_muerte_usa_el_tope(self):
        capture = make_capture(paths={2: _still(BLUE_JUNGLE, 40)})
        window = self._jungler(jungle_pathing(capture, max_s=20))["window"]
        self.assertEqual((window["t1"], window["reason"]), (20, "max_s"))

    def test_linea_temporal_incluye_wards_rivales_cercanos(self):
        capture = make_capture(
            paths={2: _still(BLUE_JUNGLE, 20)},
            events=[
                {"t": 5.0, "kind": "ward_placed", "pid": 10, "x": 4000, "y": 8000, "detail": {}},
                {"t": 6.0, "kind": "ward_placed", "pid": 10, "x": 13000, "y": 3000, "detail": {}},
                {"t": 7.0, "kind": "ward_placed", "pid": 5, "x": 4000, "y": 8000, "detail": {}},
            ])
        timeline = self._jungler(jungle_pathing(capture, until=20))["timeline"]
        self.assertEqual([(e["t"], e["relation"]) for e in timeline], [(5.0, "enemy_ward_nearby")])
        self.assertEqual(timeline[0]["jungler_pos"]["zone"], "jungle_blue_top")


class TestRecallOpeningsSynthetic(unittest.TestCase):

    def _events(self, pid=3, start=5.0, end=13.0, interrupted=False):
        return [
            {"t": start, "kind": "channel_start", "pid": pid, "detail": {"type": "recall"}},
            {"t": end, "kind": "channel_end", "pid": pid,
             "detail": {"type": "recall", "interrupted": interrupted}},
        ]

    def _path(self):
        # En línea hasta el recall (la muestra del segundo 13 aún es la vieja),
        # 8 s en base, 5 s de camino y el resto en la línea central.
        walk = [(2500 + i * 900, 2500 + i * 900) for i in range(1, 6)]
        return _still(MID_LANE, 14) + _still(BLUE_BASE, 8) + walk + _still(MID_LANE, 60)

    def test_recall_completo(self):
        events = self._events() + [
            {"t": 14.0, "kind": "item_purchased", "pid": 3, "detail": {"item_id": 1052}},
            {"t": 16.0, "kind": "item_purchased", "pid": 3, "detail": {"item_id": 2031}},
            {"t": 60.0, "kind": "item_purchased", "pid": 3, "detail": {"item_id": 9999}},
        ]
        package = recall_openings(make_capture({3: self._path()}, events), window_s=30)
        self.assertEqual(len(package["recalls"]), 1)
        recall = package["recalls"][0]
        self.assertEqual((recall["t_start"], recall["t_end"]), (5.0, 13.0))
        self.assertEqual(recall["from"]["zone"], "lane_mid")
        self.assertEqual(recall["items_bought"], [1052, 2031])
        # La muestra vieja del segundo 13 no cuenta como salida de base.
        self.assertGreater(recall["t_leave_base"], 21)
        self.assertEqual(recall["destination"]["zone"], "lane_mid")
        self.assertGreater(recall["travel_s"], 0)
        self.assertEqual(recall["zones"][-1]["zone"], "lane_mid")
        self.assertIsNone(recall["teleport"])
        self.assertFalse(recall["died"])

    def test_recall_interrumpido_no_cuenta(self):
        capture = make_capture({3: self._path()}, self._events(interrupted=True))
        self.assertEqual(recall_openings(capture)["recalls"], [])

    def test_teleport_tras_el_recall(self):
        events = self._events() + [
            {"t": 18.0, "kind": "summoner_used", "pid": 3,
             "detail": {"spell": "S12_SummonerTeleportUpgrade"}},
        ]
        recall = recall_openings(make_capture({3: self._path()}, events))["recalls"][0]
        self.assertEqual(recall["teleport"], {"t": 18.0, "spell": "S12_SummonerTeleportUpgrade"})

    def test_jugador_que_no_sale_de_base(self):
        path = _still(MID_LANE, 14) + _still(BLUE_BASE, 80)
        recall = recall_openings(make_capture({3: path}, self._events()))["recalls"][0]
        self.assertIsNone(recall["t_leave_base"])
        self.assertIsNone(recall["destination"])
        self.assertEqual(recall["path"], [])

    def test_filtros_pids_y_until(self):
        events = self._events(pid=3) + self._events(pid=4, start=30.0, end=38.0)
        capture = make_capture({3: self._path(), 4: self._path()}, events)
        self.assertEqual(len(recall_openings(capture)["recalls"]), 2)
        self.assertEqual([r["pid"] for r in recall_openings(capture, pids=[4])["recalls"]], [4])
        self.assertEqual([r["pid"] for r in recall_openings(capture, until_s=20)["recalls"]], [3])


class TestLevelOneSynthetic(unittest.TestCase):

    def test_invade_de_nivel_uno_y_marcas(self):
        capture = make_capture(
            paths={1: _still(BLUE_BASE, 20) + _still(RED_JUNGLE, 10) + _still(BLUE_JUNGLE, 70)},
            events=[{"t": 40.0, "kind": "ward_placed", "pid": 1, "x": 7000, "y": 10900, "detail": {}},
                    {"t": 200.0, "kind": "ward_placed", "pid": 1, "x": 1, "y": 1, "detail": {}}],
            seconds=100)
        package = level_one(capture, until_s=90, marks_s=(25, 60, 120))
        self.assertEqual(len(package["invades"]), 1)
        invade = package["invades"][0]
        self.assertEqual((invade["pid"], invade["zone"]), (1, "jungle_red_top"))
        self.assertEqual((invade["t_enter"], invade["t_exit"]), (20.0, 29.0))
        self.assertEqual(invade["deepest"], {"x": 7000, "y": 10900})
        player = package["players"]["1"]
        self.assertEqual(player["marks"]["25"]["zone"], "jungle_red_top")
        self.assertEqual(player["marks"]["60"]["zone"], "jungle_blue_top")
        self.assertNotIn("120", player["marks"])          # fuera de la ventana
        self.assertEqual(len(player["path"]), 91)
        self.assertEqual([w["t"] for w in package["wards"]], [40.0])
        self.assertEqual(package["wards"][0]["zone"], "jungle_red_top")

    def test_quedarse_en_casa_no_es_invade(self):
        self.assertEqual(level_one(make_capture({1: _still(BLUE_JUNGLE, 95)}))["invades"], [])


class TestPackagesOnRealGame(unittest.TestCase):
    """Sobre la partida real recortada (ticks completos hasta el minuto 5)."""

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(iter_jsonl(REAL / "riot_livestats.jsonl.gz"),
                                    series_id="2922522", game_number=1)

    def test_pathing_del_jungla_azul(self):
        package = jungle_pathing(self.capture)
        self.assertEqual([j["pid"] for j in package["junglers"]], [2, 7])
        blue = package["junglers"][0]
        self.assertEqual(blue["champion"], "Zaahen")
        self.assertEqual(blue["enemy_jungler_pid"], 7)
        self.assertEqual(blue["window"]["reason"], "first_recall")
        self.assertAlmostEqual(blue["window"]["t1"], 208.666)
        self.assertEqual(blue["start_camp"], "redcamp")
        self.assertEqual(blue["start_side"], "bot")
        self.assertEqual(blue["first_clear"], [
            "redcamp", "krug", "raptor", "wolf", "bluecamp", "gromp", "scuttlecrab"])
        self.assertEqual(blue["first_recall"]["level"], 4)
        self.assertEqual([c["area"] for c in blue["camps"][:6]], [
            "jungle_blue_red_buff", "jungle_blue_krugs", "jungle_blue_raptors",
            "jungle_blue_wolves", "jungle_blue_blue_buff", "jungle_blue_gromp"])
        self.assertEqual(blue["invades"], [])
        self.assertEqual(blue["ganks"], [])
        # Cruza mid de raptors a lobos para hacer el lado de arriba.
        crossing = blue["lane_visits"][0]
        self.assertEqual((crossing["lane"], crossing["crossed"], crossing["gank"]),
                         ("mid", True, False))
        self.assertEqual((crossing["came_from"], crossing["went_to"]),
                         ("jungle_blue_raptors", "jungle_blue_wolves"))
        self.assertEqual([r["area"] for r in blue["river_crossings"]][:1], ["river_top"])

    def test_pathing_del_jungla_rojo(self):
        red = jungle_pathing(self.capture)["junglers"][1]
        self.assertEqual(red["first_clear"], [
            "raptor", "krug", "redcamp", "wolf", "gromp", "bluecamp", "scuttlecrab"])
        self.assertEqual(red["start_side"], "top")
        self.assertAlmostEqual(red["window"]["t1"], 193.761)

    def test_el_camino_y_los_eventos_estan_cruzados(self):
        blue = jungle_pathing(self.capture)["junglers"][0]
        self.assertGreater(len(blue["path"]), 200)
        self.assertTrue(all("zone" in s and "enemy_jungler_d" in s for s in blue["path"]))
        # Cada campamento de la línea temporal lleva la posición del jungla,
        # que está en la misma zona que el campamento.
        camps = [e for e in blue["timeline"] if e["kind"] == "monster_kill"]
        self.assertEqual(len(camps), 7)
        for event in camps[:6]:
            self.assertEqual(event["jungler_pos"]["zone"], event["zone"])
        kinds = {e["kind"] for e in blue["timeline"]}
        self.assertLessEqual({"monster_kill", "level_up", "channel_end", "ward_placed"}, kinds)

    def test_nivel_uno(self):
        package = level_one(self.capture)
        self.assertEqual(len(package["players"]), 10)
        self.assertEqual(package["invades"], [])
        self.assertEqual(package["kills"], [])
        at_60 = {p["role"] + "_" + p["side"]: p["marks"]["60"]["zone"]
                 for p in package["players"].values()}
        self.assertEqual(at_60["TOP_BLUE"], "lane_top")
        self.assertEqual(at_60["MID_RED"], "lane_mid")
        self.assertEqual(at_60["JUNGLE_BLUE"], "jungle_blue_bot")
        self.assertEqual(at_60["JUNGLE_RED"], "jungle_red_top")
        self.assertEqual(len(package["wards"]), 6)
        self.assertTrue(all(w["t"] <= 90 for w in package["wards"]))

    def test_openings_tras_recall(self):
        package = recall_openings(self.capture, until_s=235)
        recalls = package["recalls"]
        self.assertEqual(len(recalls), 9)
        jungler = next(r for r in recalls if r["pid"] == 2)
        self.assertEqual(jungler["items_bought"], [1028, 1036, 2055])
        self.assertEqual(jungler["from"]["zone"], "river_top")
        self.assertEqual(jungler["destination"]["zone"], "jungle_blue_top")
        self.assertEqual(jungler["level"], 4)
        mid = next(r for r in recalls if r["role"] == "MID" and r["side"] == "BLUE")
        self.assertIsNotNone(mid["teleport"])
        self.assertEqual(mid["destination"]["zone"], "lane_mid")
        for recall in recalls:
            self.assertIsNotNone(recall["t_leave_base"])
            self.assertGreater(recall["shop_s"], 3)

    def test_los_paquetes_son_json_y_llevan_esquema(self):
        for package, name in ((level_one(self.capture), "level_one"),
                              (jungle_pathing(self.capture), "jungle_pathing"),
                              (recall_openings(self.capture, until_s=235), "recall_openings")):
            data = json.loads(json.dumps(package))
            version = "v2" if name == "jungle_pathing" else "v1"
            self.assertEqual(data["schema"], f"grid-minion/package/{name}/{version}")
            self.assertEqual(data["game"]["series_id"], "2922522")

    def test_sin_eventos_o_sin_posiciones_falla_con_mensaje_claro(self):
        only_positions = build_capture(iter_jsonl(REAL / "riot_livestats.jsonl.gz"),
                                       include=("positions",))
        for package in (level_one, jungle_pathing, recall_openings):
            with self.assertRaises(GridDataError):
                package(only_positions)


if __name__ == "__main__":
    unittest.main()

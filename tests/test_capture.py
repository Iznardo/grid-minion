import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from grid_minion import GameCapture, build_capture, capture_game, iter_jsonl
from grid_minion.capture import CAPTURE_SCHEMA
from grid_minion.exceptions import GridDataError
from grid_minion.observers import (
    EVENT_KINDS, GameEventProcessor, GameEventsObserver, PositionObserver,
)

from test_contract_gridextractor import REAL

LIVESTATS = REAL / "riot_livestats.jsonl.gz"


def _tick(t_ms, players):
    """`players`: {pid: (x, z, alive)} o {pid: None} para un jugador sin posición."""
    participants = []
    for pid, value in players.items():
        p = {"participantID": pid, "teamID": 100 if pid <= 5 else 200,
             "championName": f"Champ{pid}", "playerName": f"P{pid}", "role": "Jungle"}
        if value is not None:
            x, z, alive = value
            p["position"] = {"x": x, "z": z}
            p["alive"] = alive
        participants.append(p)
    return {"rfc461Schema": "stats_update", "gameTime": t_ms, "participants": participants}


def _run(observer, events):
    proc = GameEventProcessor()
    proc.attach(observer)
    proc.process_events(events)
    return observer


class TestPositionObserver(unittest.TestCase):

    def test_registra_trayectoria_y_metadatos(self):
        obs = _run(PositionObserver(), [
            _tick(1000, {1: (100, 200, True), 6: (900, 800, True)}),
            _tick(2000, {1: (110, 210, True), 6: (890, 790, False)}),
        ])
        track = obs.get_track()
        self.assertEqual(track.pids, [1, 6])
        self.assertEqual(len(track), 2)
        self.assertEqual(track.path(1), [
            {"t": 1.0, "x": 100, "y": 200, "alive": True},
            {"t": 2.0, "x": 110, "y": 210, "alive": True},
        ])
        self.assertFalse(track.at(6, 2)["alive"])
        self.assertEqual(track.players[6], {
            "name": "P6", "champion": "Champ6", "team_id": 200, "side": "RED", "role": "JUNGLE"})

    def test_at_devuelve_la_ultima_muestra_anterior(self):
        track = _run(PositionObserver(), [
            _tick(1000, {1: (1, 1, True)}), _tick(2000, {1: (2, 2, True)}),
        ]).get_track()
        self.assertEqual(track.at(1, 1.9)["x"], 1)
        self.assertEqual(track.at(1, 2.0)["x"], 2)
        self.assertEqual(track.at(1, 99)["x"], 2)
        self.assertIsNone(track.at(1, 0.5))
        self.assertIsNone(track.at(7, 1))

    def test_between_incluye_los_extremos(self):
        track = _run(PositionObserver(), [
            _tick(t * 1000, {1: (t, t, True)}) for t in range(1, 6)
        ]).get_track()
        self.assertEqual([s["x"] for s in track.between(1, 2, 4)], [2, 3, 4])

    def test_ticks_repetidos_o_desordenados_se_descartan(self):
        obs = _run(PositionObserver(), [
            _tick(1000, {1: (1, 1, True)}), _tick(1000, {1: (9, 9, True)}),
            _tick(500, {1: (8, 8, True)}), _tick(2000, {1: (2, 2, True)}),
        ])
        self.assertEqual([s["x"] for s in obs.get_track().path(1)], [1, 2])
        self.assertEqual(obs.dropped_ticks, 2)

    def test_sample_every_ms_y_ventana(self):
        events = [_tick(t * 1000, {1: (t, t, True)}) for t in range(0, 10)]
        sampled = _run(PositionObserver(sample_every_ms=3000), events).get_track()
        self.assertEqual([s["x"] for s in sampled.path(1)], [0, 3, 6, 9])
        window = _run(PositionObserver(t_from_s=2, t_to_s=4), events).get_track()
        self.assertEqual([s["x"] for s in window.path(1)], [2, 3, 4])

    def test_jugador_ausente_o_sin_posicion_mantiene_la_alineacion(self):
        track = _run(PositionObserver(), [
            _tick(1000, {1: (1, 1, True)}),
            _tick(2000, {1: (2, 2, True), 2: (5, 5, True)}),   # el 2 aparece tarde
            _tick(3000, {1: None, 2: (6, 6, True)}),            # el 1 sin posición
            _tick(4000, {2: (7, 7, True)}),                     # el 1 no viene
        ]).get_track()
        self.assertEqual(len(track.x[1]), 4)
        self.assertEqual(len(track.x[2]), 4)
        self.assertEqual([s["t"] for s in track.path(1)], [1.0, 2.0])
        self.assertEqual([s["t"] for s in track.path(2)], [2.0, 3.0, 4.0])
        self.assertIsNone(track.at(1, 3))

    def test_ignora_otros_esquemas_y_declara_los_suyos(self):
        obs = _run(PositionObserver(), [{"rfc461Schema": "ward_placed", "gameTime": 1}])
        self.assertEqual(len(obs.get_track()), 0)
        self.assertEqual(PositionObserver.schemas, frozenset({"stats_update"}))

    def test_to_dict_y_from_dict_son_inversos(self):
        track = _run(PositionObserver(), [
            _tick(1000, {1: (1, 1, True)}), _tick(2000, {1: None, 2: (5, 5, False)}),
        ]).get_track()
        data = json.loads(json.dumps(track.to_dict()))
        self.assertEqual(data["x"]["1"], [1, None])
        self.assertEqual(data["alive"]["2"], [None, 0])
        again = type(track).from_dict(data, players=track.players)
        self.assertEqual(again.to_dict(), track.to_dict())
        self.assertEqual(list(again.rows()), list(track.rows()))


class TestGameEventsObserver(unittest.TestCase):

    def test_formato_comun_y_mapeo_de_posicion(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "epic_monster_kill", "gameTime": 79000, "killer": 2,
             "killerTeamID": 100, "monsterType": "redCamp", "inEnemyJungle": False,
             "killType": "kill", "assistants": [], "position": {"x": 7971, "z": 3508}},
        ])
        self.assertEqual(obs.get_events(), [{
            "t": 79.0, "kind": "monster_kill", "pid": 2, "side": "BLUE",
            "x": 7971, "y": 3508,
            "detail": {"monster": "redcamp", "dragon_type": None, "assistants": [],
                       "in_enemy_jungle": False, "kill_type": "kill"},
        }])

    def test_el_nombre_de_monstruo_coincide_entre_spawn_y_kill(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "neutral_minion_spawn", "gameTime": 53000,
             "monsterType": "RedCamp", "teamSide": "Order", "position": {"x": 1, "z": 2}},
            {"rfc461Schema": "epic_monster_kill", "gameTime": 79000, "killer": 2,
             "monsterType": "redCamp"},
        ])
        spawn, kill = obs.get_events()
        self.assertEqual(spawn["detail"]["monster"], kill["detail"]["monster"])
        self.assertEqual(spawn["detail"]["camp_side"], "BLUE")
        self.assertIsNone(spawn["pid"])

    def test_normaliza_los_tres_nombres_del_id_de_jugador(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "item_purchased", "gameTime": 1000, "participantID": 3, "itemID": 1055},
            {"rfc461Schema": "skill_level_up", "gameTime": 2000, "participant": 7, "skillSlot": 1},
            {"rfc461Schema": "champion_level_up", "gameTime": 3000, "participantId": 9, "level": 2},
        ])
        self.assertEqual([(e["pid"], e["side"]) for e in obs.get_events()],
                         [(3, "BLUE"), (7, "RED"), (9, "RED")])

    def test_actor_que_no_es_jugador_queda_sin_pid(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "building_destroyed", "gameTime": 1000, "lastHitter": 0,
             "teamID": 200, "buildingType": "turret", "lane": "mid", "turretTier": "outer"},
        ])
        event = obs.get_events()[0]
        self.assertIsNone(event["pid"])
        self.assertEqual(event["detail"]["owner_side"], "RED")

    def test_recall_completado_e_interrumpido(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "channeling_started", "gameTime": 1000, "participantID": 2,
             "channelingType": "recall"},
            {"rfc461Schema": "channeling_ended", "gameTime": 9000, "participantID": 2,
             "channelingType": "recall", "isInterrupted": False},
            {"rfc461Schema": "channeling_ended", "gameTime": 9500, "participantID": 3,
             "channelingType": "recall", "isInterrupted": True},
        ])
        start, done, cut = obs.get_events()
        self.assertNotIn("interrupted", start["detail"])
        self.assertFalse(done["detail"]["interrupted"])
        self.assertTrue(cut["detail"]["interrupted"])

    def test_filtros(self):
        obs = _run(GameEventsObserver(), [
            {"rfc461Schema": "ward_placed", "gameTime": 1000, "placer": 1, "wardType": "sight"},
            {"rfc461Schema": "ward_killed", "gameTime": 2000, "killer": 6, "wardType": "sight"},
            {"rfc461Schema": "ward_placed", "gameTime": 3000, "placer": 6, "wardType": "control"},
        ])
        self.assertEqual(len(obs.get_events("ward_placed")), 2)
        self.assertEqual(len(obs.get_events(("ward_placed", "ward_killed"), pid=6)), 2)
        self.assertEqual(len(obs.get_events(t0_s=2, t1_s=3)), 2)

    def test_kinds_limita_lo_registrado_y_los_esquemas(self):
        obs = GameEventsObserver(kinds={"kill"})
        self.assertEqual(obs.schemas, frozenset({"champion_kill", "game_info"}))
        _run(obs, [
            {"rfc461Schema": "champion_kill", "gameTime": 1000, "killer": 9, "victim": 4,
             "killerTeamID": 200},
            {"rfc461Schema": "ward_placed", "gameTime": 1000, "placer": 1},
        ])
        self.assertEqual([e["kind"] for e in obs.get_events()], ["kill"])
        with self.assertRaises(ValueError):
            GameEventsObserver(kinds={"no_existe"})

    def test_game_info_da_jugadores_y_parche_sin_generar_evento(self):
        obs = _run(GameEventsObserver(), [{
            "rfc461Schema": "game_info", "gameVersion": "16.8.765.5537",
            "participants": [{"participantID": 1, "teamID": 100, "championName": "Rumble",
                              "summonerName": "Uno", "role": "Top"}],
        }])
        self.assertEqual(obs.get_events(), [])
        self.assertEqual(obs.game_version, "16.8.765.5537")
        self.assertEqual(obs.players[1]["role"], "TOP")


class TestCaptureOnRealGame(unittest.TestCase):
    """Sobre la partida real recortada (LES 2922522 G1)."""

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(iter_jsonl(LIVESTATS), series_id="2922522", game_number=1)

    def test_metadatos(self):
        c = self.capture
        self.assertEqual(c.game["series_id"], "2922522")
        self.assertEqual(c.game["game_version"], "16.8.765.5537")
        self.assertAlmostEqual(c.game["duration_s"], 1945.11)
        self.assertEqual(sorted(c.players), list(range(1, 11)))
        self.assertEqual(c.players_by(role="JUNGLE"), [2, 7])
        self.assertEqual(c.players_by(role="JUNGLE", side="RED"), [7])
        self.assertEqual(c.players[2]["champion"], "Zaahen")

    def test_trayectorias_completas_en_los_primeros_cinco_minutos(self):
        track = self.capture.positions
        self.assertEqual(track.pids, list(range(1, 11)))
        early = track.between(2, 0, 300)
        self.assertGreaterEqual(len(early), 295)       # ~1 muestra por segundo
        self.assertTrue(all(0 <= s["x"] <= 15000 and 0 <= s["y"] <= 15000 for s in early))

    def test_primer_clear_del_jungla_azul(self):
        camps = self.capture.get_events("monster_kill", pid=2, t1_s=200)
        self.assertEqual(
            [e["detail"]["monster"] for e in camps],
            ["redcamp", "krug", "raptor", "wolf", "bluecamp", "gromp", "scuttlecrab"])
        self.assertFalse(any(e["detail"]["in_enemy_jungle"] for e in camps))

    def test_posicion_y_eventos_cuadran(self):
        """Un segundo antes de matar el rojo, el jungla está junto al campamento."""
        red = self.capture.get_events("monster_kill", pid=2)[0]
        pos = self.capture.position_at(2, red["t"] - 1)
        distance = ((pos["x"] - red["x"]) ** 2 + (pos["y"] - red["y"]) ** 2) ** 0.5
        self.assertLess(distance, 600)

    def test_primer_recall_y_compra(self):
        c = self.capture
        recall = next(e for e in c.get_events("channel_end", pid=2)
                      if e["detail"]["type"] == "recall" and not e["detail"]["interrupted"])
        self.assertAlmostEqual(recall["t"], 209, delta=1)
        bought = c.get_events("item_purchased", pid=2, t0_s=recall["t"], t1_s=recall["t"] + 10)
        self.assertEqual([e["detail"]["item_id"] for e in bought], [1028, 1036, 2055])

    def test_todos_los_tipos_registrados_son_conocidos(self):
        kinds = {e["kind"] for e in self.capture.events}
        self.assertLessEqual(kinds, EVENT_KINDS)
        self.assertIn("game_end", kinds)
        self.assertEqual(self.capture.get_events("game_end")[0]["detail"]["winning_side"], "BLUE")

    def test_guardar_y_cargar_es_identico(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "x.capture.json.gz")
            self.capture.save(path)
            again = GameCapture.load(path)
            self.assertEqual(again.to_dict(), self.capture.to_dict())
            self.assertEqual(again.position_at(2, 79), self.capture.position_at(2, 79))
            self.assertLess(os.path.getsize(path), 150_000)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_to_dict_es_json_con_esquema_versionado(self):
        data = json.loads(json.dumps(self.capture.to_dict()))
        self.assertEqual(data["schema"], CAPTURE_SCHEMA)
        self.assertEqual(sorted(data), ["events", "game", "players", "positions", "schema"])


class TestCaptureSections(unittest.TestCase):

    def test_solo_eventos(self):
        capture = build_capture(iter_jsonl(LIVESTATS), include=("events",))
        self.assertIsNone(capture.positions)
        self.assertEqual(sorted(capture.players), list(range(1, 11)))
        self.assertNotIn("positions", capture.to_dict())
        with self.assertRaises(GridDataError):
            capture.position_at(1, 10)

    def test_solo_posiciones(self):
        capture = build_capture(iter_jsonl(LIVESTATS), include=("positions",))
        self.assertIsNone(capture.events)
        self.assertEqual(capture.players[7]["role"], "JUNGLE")
        self.assertGreater(capture.game["duration_s"], 1900)
        with self.assertRaises(GridDataError):
            capture.get_events()

    def test_seccion_desconocida(self):
        with self.assertRaises(ValueError):
            build_capture([], include=("stats",))

    def test_esquema_no_soportado(self):
        with self.assertRaises(GridDataError):
            GameCapture.from_dict({"schema": "otra/v9"})


class TestCaptureGame(unittest.TestCase):

    def test_descarga_en_streaming_solo_los_esquemas_necesarios(self):
        client = MagicMock()
        client.iter_riot_livestats.return_value = iter_jsonl(LIVESTATS)
        capture = capture_game(client, "2922522", 1, include=("positions",))
        _, kwargs = client.iter_riot_livestats.call_args
        self.assertEqual(kwargs["schemas"], {"stats_update"})
        self.assertEqual(capture.game["game_number"], 1)
        self.assertEqual(len(capture.positions.pids), 10)

    def test_sin_livestats_devuelve_none(self):
        client = MagicMock()
        client.iter_riot_livestats.return_value = None
        self.assertIsNone(capture_game(client, "1", 1))


if __name__ == "__main__":
    unittest.main()

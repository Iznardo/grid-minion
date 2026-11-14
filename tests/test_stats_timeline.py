import json
import unittest

from grid_minion import GameCapture, build_capture, iter_jsonl
from grid_minion.observers import (
    STATS_GROUPS, ColumnTimeline, CooldownsObserver, GameEventProcessor,
    PositionObserver, StatsTimelineObserver,
)
from grid_minion.packages import recall_openings

from test_contract_gridextractor import REAL

LIVESTATS = REAL / "riot_livestats.jsonl.gz"


def _tick(t_ms, pid=1, team_gold=None, **fields):
    participant = {"participantID": pid, "teamID": 100, **fields}
    event = {"rfc461Schema": "stats_update", "gameTime": t_ms, "participants": [participant]}
    if team_gold is not None:
        event["teams"] = [{"teamID": 100, "totalGold": team_gold, "championsKills": 1,
                           "deaths": 0, "assists": 2, "towerKills": 0, "inhibKills": 0,
                           "dragonKills": 1, "baronKills": 0}]
    return event


def _run(observer, events):
    proc = GameEventProcessor()
    proc.attach(observer)
    proc.process_events(events)
    return observer


class TestStatsTimelineObserver(unittest.TestCase):

    def test_economia_cs_e_inventario(self):
        obs = _run(StatsTimelineObserver(groups=("economy",)), [
            _tick(1000, totalGold=500, currentGold=500, XP=0, level=1,
                  stats=[{"name": "MINIONS_KILLED", "value": 3},
                         {"name": "NEUTRAL_MINIONS_KILLED", "value": 4.0000076}],
                  items=[{"itemID": 1055}, {"itemID": 2003, "itemStacks": 1}]),
            _tick(2000, totalGold=540, currentGold=40, XP=120, level=2,
                  stats=[{"name": "MINIONS_KILLED", "value": 5}],
                  items=[{"itemID": 1055}, {"itemID": 2003, "itemStacks": 1}]),
            _tick(3000, totalGold=560, currentGold=60, XP=130, level=2,
                  items=[{"itemID": 1055}, {"itemID": 1036}]),
        ])
        tl = obs.get_timeline()
        self.assertEqual(tl.at(1, 1)["cs"], 7)
        self.assertEqual(tl.at(1, 2.5), {
            "t": 2.0, "gold_total": 540, "gold_current": 40, "xp": 120, "level": 2,
            "respawn_timer": None, "shutdown_value": None, "cs": 5})
        self.assertIsNone(tl.at(1, 3)["cs"])            # sin lista `stats`: no se inventa
        self.assertEqual(tl.values(1, "level"), [1, 2, 2])
        # El inventario solo se guarda cuando cambia.
        self.assertEqual(tl.items[1], [[1000, [1055, 2003]], [3000, [1055, 1036]]])
        self.assertEqual(tl.items_at(1, 2.9), [1055, 2003])
        self.assertEqual(tl.items_at(1, 3), [1055, 1036])
        self.assertIsNone(tl.items_at(1, 0.5))

    def test_contadores_acumulados_y_delta(self):
        def stats(damage):
            return [{"name": "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", "value": damage}]
        tl = _run(StatsTimelineObserver(groups=("combat_totals",)), [
            _tick(1000, stats=stats(0)), _tick(2000, stats=stats(150.5)),
            _tick(3000, stats=stats(400)),
        ]).get_timeline()
        self.assertEqual(tl.delta(1, "damage_to_champions", 1, 3), 400)
        self.assertEqual(tl.delta(1, "damage_to_champions", 2, 3), 249.5)
        self.assertEqual(tl.delta(1, "damage_to_champions", 0, 2), 150.5)   # antes del inicio = 0
        self.assertIsNone(tl.at(1, 3)["vision_score"])                        # ausente != 0
        self.assertNotIn("gold_total", tl.fields)

    def test_equipos(self):
        tl = _run(StatsTimelineObserver(groups=("teams",)), [
            _tick(1000, team_gold=2500), _tick(2000, team_gold=2600),
        ]).get_timeline()
        self.assertEqual(tl.fields, ())
        self.assertEqual(tl.team_at(100, 2), {
            "t": 2.0, "gold": 2600, "kills": 1, "deaths": 0, "assists": 2,
            "towers": 0, "inhibitors": 0, "dragons": 1, "barons": 0})
        self.assertIsNone(tl.team_at(200, 2))

    def test_grupos(self):
        self.assertEqual(StatsTimelineObserver().groups, STATS_GROUPS)
        with self.assertRaises(ValueError):
            StatsTimelineObserver(groups=("posiciones",))
        fields = StatsTimelineObserver(groups=("champion_stats",)).get_timeline().fields
        self.assertIn("ad", fields)
        self.assertNotIn("cs", fields)

    def test_jugador_que_aparece_tarde_queda_alineado(self):
        tl = _run(StatsTimelineObserver(groups=("economy",)), [
            _tick(1000, pid=1, level=1), _tick(2000, pid=2, level=3),
        ]).get_timeline()
        self.assertEqual(tl.values(1, "level"), [1, None])
        self.assertEqual(tl.values(2, "level"), [None, 3])

    def test_serializacion_ida_y_vuelta(self):
        tl = _run(StatsTimelineObserver(), [
            _tick(1000, team_gold=2500, totalGold=500, attackDamage=60.5,
                  items=[{"itemID": 1055}]),
            _tick(2000, team_gold=2600, totalGold=520, attackDamage=62),
        ]).get_timeline()
        data = json.loads(json.dumps(tl.to_dict()))
        again = ColumnTimeline.from_dict(data)
        self.assertEqual(again.to_dict(), tl.to_dict())
        self.assertEqual(again.at(1, 2)["ad"], 62)
        self.assertEqual(again.team_at(100, 1)["gold"], 2500)


class TestCooldownsObserver(unittest.TestCase):

    def test_cooldowns_y_disponibilidad(self):
        obs = _run(CooldownsObserver(), [
            _tick(1000, ultimateCooldownRemaining=0, summonerSpell1CooldownRemaining=0,
                  summonerSpell1Name="SummonerFlash", summonerSpell2Name="SummonerSmite",
                  ultimateName="ZaahenR"),
            _tick(2000, ultimateCooldownRemaining=79.5, summonerSpell1CooldownRemaining=300,
                  summonerSpell1Name="SummonerFlash",
                  summonerSpell2Name="S5_SummonerSmitePlayerGanker", ultimateName="ZaahenR"),
        ])
        self.assertTrue(obs.is_up(1, "ultimate", 1))
        self.assertFalse(obs.is_up(1, "ultimate", 2))
        self.assertFalse(obs.is_up(1, "summoner1", 2))
        self.assertIsNone(obs.is_up(1, "ability1", 2))      # sin dato
        self.assertIsNone(obs.is_up(1, "ultimate", 0.5))    # antes de la primera muestra
        self.assertIsNone(obs.is_up(9, "ultimate", 2))
        tl = obs.get_timeline()
        self.assertEqual(tl.value_at(1, "ultimate", 2), 79.5)
        # Se queda con el último nombre visto (el Smite cambia al mejorarse).
        self.assertEqual(tl.meta["spell_names"]["1"]["summoner2"], "S5_SummonerSmitePlayerGanker")
        again = ColumnTimeline.from_dict(json.loads(json.dumps(tl.to_dict())))
        self.assertEqual(again.meta["spell_names"]["1"]["ultimate"], "ZaahenR")


class TestSharedClock(unittest.TestCase):

    def test_los_tres_observers_guardan_los_mismos_instantes(self):
        for options in ({}, {"sample_every_ms": 5000}, {"t_from_s": 60, "t_to_s": 180}):
            with self.subTest(options=options):
                positions = PositionObserver(**options)
                stats = StatsTimelineObserver(**options)
                cooldowns = CooldownsObserver(**options)
                proc = GameEventProcessor()
                for observer in (positions, stats, cooldowns):
                    proc.attach(observer)
                proc.process_events(iter_jsonl(LIVESTATS))
                clock = list(positions.get_track().t_ms)
                self.assertGreater(len(clock), 10)
                self.assertEqual(list(stats.get_timeline().t_ms), clock)
                self.assertEqual(list(cooldowns.get_timeline().t_ms), clock)


class TestStatsOnRealGame(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.capture = build_capture(
            iter_jsonl(LIVESTATS),
            include=("positions", "events", "stats", "cooldowns"))

    def test_economia_del_jungla_azul_al_recall(self):
        stats = self.capture.stats
        row = stats.at(2, 208)
        self.assertEqual(row["level"], 4)
        self.assertGreater(row["gold_total"], 1000)
        self.assertGreater(row["cs"], 20)
        self.assertEqual(stats.at(2, 5)["level"], 1)

    def test_el_inventario_cuadra_con_las_compras(self):
        bought = [e["detail"]["item_id"] for e in
                  self.capture.get_events("item_purchased", pid=2, t0_s=208, t1_s=215)]
        after = self.capture.stats.items_at(2, 220)
        for item in bought:
            self.assertIn(item, after)

    def test_oro_de_equipo_crece(self):
        stats = self.capture.stats
        self.assertGreater(stats.team_at(100, 300)["gold"], stats.team_at(100, 60)["gold"])
        self.assertEqual(sorted(stats.teams), [100, 200])

    def test_cooldowns_del_feed(self):
        cooldowns = self.capture.cooldowns
        self.assertEqual(cooldowns.pids, list(range(1, 11)))
        names = cooldowns.meta["spell_names"]["2"]
        self.assertIn("Smite", names["summoner1"] + names["summoner2"])
        # A nivel 1 nadie tiene la definitiva en cooldown.
        self.assertEqual(cooldowns.value_at(2, "ultimate", 30), 0)

    def test_posicion_stats_y_cooldowns_del_mismo_instante(self):
        c = self.capture
        self.assertEqual(list(c.stats.t_ms), list(c.positions.t_ms))
        self.assertEqual(list(c.cooldowns.t_ms), list(c.positions.t_ms))
        self.assertEqual(c.stats.at(2, 100)["t"], c.position_at(2, 100)["t"])

    def test_guardar_y_cargar_con_todas_las_secciones(self):
        again = GameCapture.from_dict(json.loads(json.dumps(self.capture.to_dict())))
        self.assertEqual(again.to_dict(), self.capture.to_dict())
        self.assertEqual(again.stats.at(2, 208), self.capture.stats.at(2, 208))

    def test_el_paquete_de_recalls_usa_el_oro_si_hay_stats(self):
        with_stats = recall_openings(self.capture, pids=[2], until_s=235)["recalls"][0]
        self.assertGreater(with_stats["gold_at_recall"], 500)
        base = build_capture(iter_jsonl(LIVESTATS))
        self.assertIsNone(base.stats)
        without = recall_openings(base, pids=[2], until_s=235)["recalls"][0]
        self.assertIsNone(without["gold_at_recall"])
        self.assertEqual(without["items_bought"], with_stats["items_bought"])

    def test_la_captura_por_defecto_no_incluye_stats_ni_cooldowns(self):
        data = build_capture(iter_jsonl(LIVESTATS)).to_dict()
        self.assertNotIn("stats", data)
        self.assertNotIn("cooldowns", data)


if __name__ == "__main__":
    unittest.main()

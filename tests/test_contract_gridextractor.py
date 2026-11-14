"""Test de contrato: la salida que gridExtractor consume no puede cambiar.

gridExtractor monta siempre los mismos 8 observers, en el mismo orden
(`_build_processor()` en `src/extraction/_persistence.py`), y persiste lo que
devuelven. Este test reproduce ese montaje sobre una partida real recortada
(`tests/samples/real/`, con un redraft de ERL incluido) y compara TODA la
salida con un resultado guardado (`tests/golden/gridextractor_contract.json`).

Si este test falla, un cambio ha alterado datos que gridExtractor guarda en
su base de datos. Solo debe regenerarse el resultado guardado cuando el cambio
es deliberado y se ha revisado su efecto en gridExtractor:

    GRID_MINION_UPDATE_GOLDEN=1 python -m pytest tests/test_contract_gridextractor.py
"""
import gzip
import json
import os
import unittest
from pathlib import Path

from grid_minion.champions import ChampionResolver, set_default_resolver
from grid_minion.observers import (
    BuildObserver,
    DraftObserver,
    GameEventProcessor,
    MidGameStatsObserver,
    ObjectiveKilledObserver,
    PostGameObserver,
    SoloKillObserver,
    TeamsObserver,
    WardsObserver,
)

HERE = Path(__file__).resolve().parent
REAL = HERE / "samples" / "real"
GOLDEN = HERE / "golden" / "gridextractor_contract.json"


def load_real_fixture():
    """Devuelve `(grid_events, riot_summary, riot_livestats, champions)`."""
    with gzip.open(REAL / "grid_events.json.gz", "rt", encoding="utf-8") as fh:
        grid_events = json.load(fh)
    with gzip.open(REAL / "riot_summary.json.gz", "rt", encoding="utf-8") as fh:
        riot_summary = json.load(fh)
    with gzip.open(REAL / "riot_livestats.jsonl.gz", "rt", encoding="utf-8") as fh:
        riot_livestats = [json.loads(line) for line in fh if line.strip()]
    champions = json.loads((REAL / "champions.json").read_text(encoding="utf-8"))
    return grid_events, riot_summary, riot_livestats, champions


def run_gridextractor_pipeline(grid_events, riot_summary, riot_livestats):
    """Mismo montaje y mismas lecturas que `process_one_game` de gridExtractor."""
    proc = GameEventProcessor()
    teams = TeamsObserver()
    draft = DraftObserver()
    stats = PostGameObserver()
    objs = ObjectiveKilledObserver()
    wards = WardsObserver(teams_observer=teams)
    builds = BuildObserver()
    midgame = MidGameStatsObserver()
    solos = SoloKillObserver(teams_observer=teams)
    for observer in (teams, draft, stats, objs, wards, builds, midgame, solos):
        proc.attach(observer)

    proc.process_bundle(
        grid_livestats=grid_events,
        riot_summary=riot_summary,
        riot_livestats=riot_livestats,
    )

    participants = []
    for riot_id in range(1, 11):
        p = teams.get_player_by_id(riot_id)
        if p is None:
            continue
        participants.append({
            "riot_id": p.riot_id,
            "summoner_name": p.summoner_name,
            "team_id": p.team_id,
            "team_side": p.team_side,
            "champion_name": p.champion_name,
            "champion_id": p.champion_id,
            "grid_player_id": p.grid_player_id,
            "grid_team_id": p.grid_team_id,
            "puuid": p.puuid,
        })

    return {
        "draft": draft.get_draft(),
        "draft_status": draft.get_draft_status(),
        "game_stats": stats.get_game_stats(teams),
        "participants": participants,
        "objectives": objs.get_all_objectives(),
        "wards": wards.get_wards(),
        "solokills": solos.get_solokills(),
        "builds": builds.get_builds(),
        "midgame": midgame.get_mid_game_stats(),
        # Atributo privado que gridExtractor lee para estimar la duración.
        "midgame_max_game_time": midgame._max_game_time,
    }


def _normalize(value):
    """Pasa por JSON para comparar como lo vería quien lo persiste."""
    return json.loads(json.dumps(value, sort_keys=True))


class TestGridExtractorContract(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        grid_events, summary, livestats, champions = load_real_fixture()
        set_default_resolver(ChampionResolver.from_mapping(champions))
        self.result = _normalize(run_gridextractor_pipeline(grid_events, summary, livestats))

    def tearDown(self):
        set_default_resolver(None)

    def test_salida_identica_al_resultado_guardado(self):
        if os.environ.get("GRID_MINION_UPDATE_GOLDEN") == "1":
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(
                json.dumps(self.result, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8")
            self.skipTest("Resultado guardado regenerado.")
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.result), sorted(expected))
        for section in expected:
            with self.subTest(section=section):
                self.assertEqual(self.result[section], expected[section])

    def test_el_fixture_ejercita_todos_los_observers(self):
        """Si el fixture quedara vacío, el contrato pasaría sin probar nada."""
        r = self.result
        self.assertTrue(r["draft"]["is_complete"])
        self.assertIn(r["game_stats"]["meta"]["winner"], ("BLUE", "RED"))
        self.assertEqual(len(r["participants"]), 10)
        self.assertEqual(len(r["game_stats"]["players"]), 10)
        self.assertTrue(all(p["grid_player_id"] for p in r["participants"]))
        self.assertGreater(len(r["objectives"]["dragons"]), 0)
        self.assertGreater(len(r["wards"]), 0)
        self.assertEqual(len(r["builds"]), 10)
        self.assertEqual(len(r["midgame"]), 10)
        self.assertGreater(r["midgame_max_game_time"], 0)

    def test_redraft_erl_conserva_el_first_pick_del_primer_draft(self):
        """El primer draft fija el First Pick; el rehecho solo corrige los lados.

        En esta partida el equipo 52457 eligió First Pick y jugó en rojo: el
        draft del cliente (el segundo) lo abre el otro equipo, pero el First
        Pick correcto es el del primer draft.
        """
        draft = self.result["draft"]
        self.assertEqual(draft["fp"]["team_id"], "52457")
        self.assertEqual(draft["fp"]["picks"][0]["name"], "Ambessa")
        red = [p for p in self.result["participants"] if p["team_side"] == "RED"]
        self.assertEqual({p["grid_team_id"] for p in red}, {"52457"})


if __name__ == "__main__":
    unittest.main()

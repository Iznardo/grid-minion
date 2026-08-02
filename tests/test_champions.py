import unittest
import os
import json
import tempfile
import shutil
from unittest import mock

from grid_minion.champions import ChampionResolver
from grid_minion.exceptions import GridNetworkError


def _fake_response(payload, status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class TestChampionResolverMapping(unittest.TestCase):
    """normalize() sobre un resolver seedeado (sin red)."""

    def setUp(self):
        self.resolver = ChampionResolver.from_mapping({
            "MonkeyKing": {"name": "Wukong", "key": 62},
            "LeeSin": {"name": "Lee Sin", "key": 64},
            "JarvanIV": {"name": "Jarvan IV", "key": 59},
        })

    def test_display_name_de_grid_se_normaliza_a_clave_riot(self):
        self.assertEqual(self.resolver.normalize("Wukong"), ("MonkeyKing", 62))
        self.assertEqual(self.resolver.normalize("Lee Sin"), ("LeeSin", 64))

    def test_clave_riot_del_summary_resuelve_su_id_numerico(self):
        # El summary ya manda "MonkeyKing"; debe devolverse tal cual + su key.
        self.assertEqual(self.resolver.normalize("MonkeyKing"), ("MonkeyKing", 62))

    def test_campeon_desconocido_no_revienta(self):
        self.assertEqual(self.resolver.normalize("Zaahen"), ("Zaahen", None))

    def test_none_y_vacio(self):
        self.assertEqual(self.resolver.normalize(None), (None, None))
        self.assertEqual(self.resolver.normalize(""), ("", None))


class TestChampionResolverModeVariants(unittest.TestCase):
    """Reskins de modo (`Jade_*` de LoL Classic) que repiten el display name.

    Data Dragon 16.15.1 introdujo 60 entradas `Jade_X` con `name` igual al del
    campeón base y key = 60000 + key_base. No deben pisar a la base: el feed de
    draft de GRID emite display names y devolvía el id de la variante.
    """

    def test_variante_no_pisa_al_campeon_base(self):
        resolver = ChampionResolver.from_mapping({
            "Ezreal": {"name": "Ezreal", "key": 81},
            "Jade_Ezreal": {"name": "Ezreal", "key": 60081},
        })
        self.assertEqual(resolver.normalize("Ezreal"), ("Ezreal", 81))

    def test_el_orden_de_las_entradas_es_irrelevante(self):
        # "Jade_Zed" va antes que "Zed" alfabéticamente: no vale con quedarse
        # con la primera entrada vista, hay que comparar keys.
        resolver = ChampionResolver.from_mapping({
            "Jade_Zed": {"name": "Zed", "key": 60238},
            "Zed": {"name": "Zed", "key": 238},
        })
        self.assertEqual(resolver.normalize("Zed"), ("Zed", 238))

    def test_la_clave_interna_de_la_variante_se_ignora(self):
        # La variante no entra en ningún índice: su clave interna no puede
        # reintroducir el id 60xxx por la rama de by_id.
        resolver = ChampionResolver.from_mapping({
            "Ezreal": {"name": "Ezreal", "key": 81},
            "Jade_Ezreal": {"name": "Ezreal", "key": 60081},
        })
        self.assertEqual(resolver.normalize("Jade_Ezreal"), ("Jade_Ezreal", None))

    def test_entrada_sin_key_no_desbanca_a_la_que_si_tiene(self):
        resolver = ChampionResolver.from_mapping({
            "Ezreal": {"name": "Ezreal", "key": 81},
            "Jade_Ezreal": {"name": "Ezreal", "key": None},
        })
        self.assertEqual(resolver.normalize("Ezreal"), ("Ezreal", 81))


class TestChampionResolverNetwork(unittest.TestCase):
    """Flujo de descarga + caché + comprobación de versión (red mockeada)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_env = os.environ.get("GRID_MINION_CACHE_DIR")
        os.environ["GRID_MINION_CACHE_DIR"] = self.tmp

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("GRID_MINION_CACHE_DIR", None)
        else:
            os.environ["GRID_MINION_CACHE_DIR"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _champion_payload(self):
        return {"data": {
            "MonkeyKing": {"name": "Wukong", "key": "62"},
            "JarvanIV": {"name": "Jarvan IV", "key": "59"},
        }}

    def test_primera_carga_descarga_y_cachea(self):
        def fake_get(url, timeout=None):
            if url.endswith("versions.json"):
                return _fake_response(["14.10.1"])
            return _fake_response(self._champion_payload())

        with mock.patch("grid_minion.champions.requests.get", side_effect=fake_get) as m:
            resolver = ChampionResolver()
            self.assertEqual(resolver.normalize("Wukong"), ("MonkeyKing", 62))
            self.assertEqual(resolver.version, "14.10.1")
            # versions.json + champion.json
            self.assertEqual(m.call_count, 2)

        # La caché quedó escrita en disco.
        cache_file = os.path.join(self.tmp, "ddragon", "champions_en_US.json")
        self.assertTrue(os.path.exists(cache_file))
        with open(cache_file) as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], "14.10.1")

    def test_segunda_carga_misma_version_no_descarga_champion_json(self):
        # Pre-cachear en disco.
        cache_dir = os.path.join(self.tmp, "ddragon")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "champions_en_US.json"), "w") as fh:
            json.dump({
                "version": "14.10.1", "lang": "en_US",
                "champions": {"MonkeyKing": {"name": "Wukong", "key": 62}},
            }, fh)

        calls = []

        def fake_get(url, timeout=None):
            calls.append(url)
            if url.endswith("versions.json"):
                return _fake_response(["14.10.1"])
            raise AssertionError("No debería descargar champion.json si la versión coincide")

        with mock.patch("grid_minion.champions.requests.get", side_effect=fake_get):
            resolver = ChampionResolver()
            self.assertEqual(resolver.normalize("Wukong"), ("MonkeyKing", 62))

        # Solo se consultó versions.json.
        self.assertEqual(len(calls), 1)

    def test_sin_red_pero_con_cache_usa_la_cache(self):
        cache_dir = os.path.join(self.tmp, "ddragon")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, "champions_en_US.json"), "w") as fh:
            json.dump({
                "version": "14.9.1", "lang": "en_US",
                "champions": {"MonkeyKing": {"name": "Wukong", "key": 62}},
            }, fh)

        import requests
        with mock.patch("grid_minion.champions.requests.get",
                        side_effect=requests.exceptions.ConnectionError("offline")):
            resolver = ChampionResolver(max_retries=1)
            self.assertEqual(resolver.normalize("Wukong"), ("MonkeyKing", 62))

    def test_sin_red_ni_cache_lanza_excepcion(self):
        import requests
        with mock.patch("grid_minion.champions.requests.get",
                        side_effect=requests.exceptions.ConnectionError("offline")):
            resolver = ChampionResolver(max_retries=1)
            with self.assertRaises(GridNetworkError):
                resolver.normalize("Wukong")


if __name__ == "__main__":
    unittest.main()

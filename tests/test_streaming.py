import gzip
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from grid_minion import iter_jsonl
from grid_minion.champions import ChampionResolver, set_default_resolver
from grid_minion.exceptions import GridDataError, GridNetworkError
from grid_minion.io import line_schema, parse_jsonl_lines
from grid_minion.observers import GameEventProcessor, Observer
from grid_minion.rest_client import GridRestClient

from test_contract_gridextractor import (
    GOLDEN, REAL, _normalize, load_real_fixture, run_gridextractor_pipeline,
)


def _lines(*events):
    return [json.dumps(e).encode("utf-8") for e in events]


class TestParseJsonlLines(unittest.TestCase):

    def test_line_schema_con_y_sin_espacios(self):
        self.assertEqual(line_schema(b'{"a":1,"rfc461Schema":"stats_update"}'), "stats_update")
        self.assertEqual(line_schema(b'{"rfc461Schema": "ward_placed", "x": 1}'), "ward_placed")
        self.assertIsNone(line_schema(b'{"type": "team-picked-character"}'))

    def test_sin_filtro_devuelve_todo_y_salta_lineas_vacias(self):
        lines = _lines({"rfc461Schema": "a"}, {"rfc461Schema": "b"}) + [b"", b"  \n"]
        self.assertEqual(len(list(parse_jsonl_lines(lines))), 2)

    def test_filtro_descarta_antes_de_parsear(self):
        # La línea descartada no es JSON válido: si se parseara, reventaría.
        lines = [b'{"rfc461Schema":"stats_update", ROTO', b'{"rfc461Schema":"ward_placed"}']
        out = list(parse_jsonl_lines(lines, schemas={"ward_placed"}))
        self.assertEqual(out, [{"rfc461Schema": "ward_placed"}])

    def test_filtro_conserva_lineas_sin_esquema(self):
        lines = _lines({"type": "otro"}, {"rfc461Schema": "x"})
        self.assertEqual(list(parse_jsonl_lines(lines, schemas={"y"})), [{"type": "otro"}])

    def test_json_invalido_lanza_data_error_con_la_linea(self):
        with self.assertRaises(GridDataError) as ctx:
            list(parse_jsonl_lines([b'{"ok":1}', b'{mal'], source="prueba"))
        self.assertIn("línea 2", str(ctx.exception))


class TestIterJsonl(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fichero_plano_y_gz(self):
        events = [{"rfc461Schema": "a", "n": 1}, {"rfc461Schema": "b", "n": 2}]
        plain = os.path.join(self.tmp, "x.jsonl")
        with open(plain, "wb") as fh:
            fh.write(b"\n".join(_lines(*events)))
        packed = os.path.join(self.tmp, "x.jsonl.gz")
        with gzip.open(packed, "wb") as fh:
            fh.write(b"\n".join(_lines(*events)))
        self.assertEqual(list(iter_jsonl(plain)), events)
        self.assertEqual(list(iter_jsonl(packed, schemas={"b"})), events[1:])


class TestRestClientStreaming(unittest.TestCase):

    def setUp(self):
        self.client = GridRestClient(api_key="fake_key", max_retries=1)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _response(self, status=200, lines=(), chunks=()):
        response = MagicMock(status_code=status, headers={})
        response.__bool__ = lambda self: status < 400
        response.iter_lines.return_value = iter(lines)
        response.iter_content.return_value = iter(chunks)
        return response

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_iter_devuelve_eventos_y_cierra_la_respuesta(self, mock_request):
        response = self._response(lines=_lines({"rfc461Schema": "a"}, {"rfc461Schema": "b"}))
        mock_request.return_value = response
        events = self.client.iter_riot_livestats("1", 1, schemas={"b"})
        self.assertEqual(list(events), [{"rfc461Schema": "b"}])
        response.close.assert_called_once()
        self.assertTrue(mock_request.call_args.kwargs["stream"])

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_iter_404_devuelve_none(self, mock_request):
        mock_request.return_value = self._response(status=404)
        self.assertIsNone(self.client.iter_riot_livestats("1", 1))

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_iter_corte_de_red_lanza_network_error(self, mock_request):
        def broken():
            yield b'{"rfc461Schema":"a"}'
            raise requests.exceptions.ChunkedEncodingError("corte")
        response = self._response()
        response.iter_lines.return_value = broken()
        mock_request.return_value = response
        events = self.client.iter_riot_livestats("1", 1)
        self.assertEqual(next(events), {"rfc461Schema": "a"})
        with self.assertRaises(GridNetworkError):
            next(events)
        response.close.assert_called_once()

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_download_escribe_atomico_y_se_puede_releer(self, mock_request):
        body = b"\n".join(_lines({"rfc461Schema": "a"}, {"rfc461Schema": "b"}))
        mock_request.return_value = self._response(chunks=[body[:10], body[10:]])
        path = os.path.join(self.tmp, "sub", "game.jsonl.gz")
        written = self.client.download_riot_livestats("1", 1, path)
        self.assertEqual(str(written), path)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["game.jsonl.gz"])
        self.assertEqual(len(list(iter_jsonl(path))), 2)

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_download_interrumpido_no_deja_fichero(self, mock_request):
        def broken():
            yield b'{"rfc461Schema":"a"}'
            raise requests.exceptions.ConnectionError("corte")
        response = self._response()
        response.iter_content.return_value = broken()
        mock_request.return_value = response
        path = os.path.join(self.tmp, "game.jsonl")
        with self.assertRaises(GridNetworkError):
            self.client.download_riot_livestats("1", 1, path)
        self.assertEqual(os.listdir(self.tmp), [])

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_download_404_devuelve_none(self, mock_request):
        mock_request.return_value = self._response(status=404)
        self.assertIsNone(self.client.download_riot_livestats("1", 1, os.path.join(self.tmp, "x")))


class _Recorder(Observer):
    def __init__(self, schemas=None):
        if schemas is not None:
            self.schemas = frozenset(schemas)
        self.seen = []

    def notify_event(self, event):
        self.seen.append(event.get("rfc461Schema") or event.get("type"))


class TestProcessorSchemas(unittest.TestCase):

    EVENTS = [{"rfc461Schema": "stats_update"}, {"rfc461Schema": "ward_placed"},
              {"type": "team-picked-character"}]

    def test_observer_sin_schemas_recibe_todo(self):
        proc = GameEventProcessor()
        rec = _Recorder()
        proc.attach(rec)
        proc.process_events(self.EVENTS)
        self.assertEqual(rec.seen, ["stats_update", "ward_placed", "team-picked-character"])
        self.assertIsNone(proc.wanted_schemas())

    def test_observer_con_schemas_solo_recibe_los_suyos(self):
        proc = GameEventProcessor()
        only_stats = _Recorder({"stats_update"})
        everything = _Recorder()
        proc.attach(only_stats)
        proc.attach(everything)
        proc.process_bundle(riot_livestats=iter(self.EVENTS))
        self.assertEqual(only_stats.seen, ["stats_update"])
        self.assertEqual(len(everything.seen), 3)
        self.assertIsNone(proc.wanted_schemas())

    def test_wanted_schemas_es_la_union(self):
        proc = GameEventProcessor()
        proc.attach(_Recorder({"stats_update"}))
        proc.attach(_Recorder({"ward_placed", "stats_update"}))
        self.assertEqual(proc.wanted_schemas(), frozenset({"stats_update", "ward_placed"}))


class TestStreamingKeepsContract(unittest.TestCase):
    """Pasar el livestats como iterador da EXACTAMENTE lo mismo que como lista."""

    def setUp(self):
        _, _, _, champions = load_real_fixture()
        set_default_resolver(ChampionResolver.from_mapping(champions))

    def tearDown(self):
        set_default_resolver(None)

    def test_iterador_desde_disco_cumple_el_contrato(self):
        grid_events, summary, _, _ = load_real_fixture()
        stream = iter_jsonl(REAL / "riot_livestats.jsonl.gz")
        result = _normalize(run_gridextractor_pipeline(grid_events, summary, stream))
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()

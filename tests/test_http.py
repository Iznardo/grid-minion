import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

from grid_minion._http import MAX_RETRY_AFTER_S, parse_retry_after
from grid_minion.rest_client import GridRestClient


class TestParseRetryAfter(unittest.TestCase):

    def test_segundos(self):
        self.assertEqual(parse_retry_after("30", default=5), 30.0)

    def test_ausente_usa_default(self):
        self.assertEqual(parse_retry_after(None, default=7.5), 7.5)

    def test_fecha_http(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=60)
        wait = parse_retry_after(format_datetime(when, usegmt=True), default=5)
        self.assertTrue(50 <= wait <= 60, wait)

    def test_fecha_pasada_no_es_negativa(self):
        when = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertEqual(parse_retry_after(format_datetime(when, usegmt=True), default=5), 0.0)

    def test_basura_usa_default(self):
        self.assertEqual(parse_retry_after("mañana", default=5), 5.0)

    def test_tope(self):
        self.assertEqual(parse_retry_after("86400", default=5), MAX_RETRY_AFTER_S)


class TestClientRetryAfterHttpDate(unittest.TestCase):

    @patch("grid_minion.rest_client.time.sleep")
    @patch("grid_minion.rest_client.requests.Session.request")
    def test_429_con_fecha_http_no_revienta(self, mock_request, mock_sleep):
        limited = MagicMock(status_code=429,
                            headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
        ok = MagicMock(status_code=200, headers={})
        mock_request.side_effect = [limited, ok]

        client = GridRestClient(api_key="fake_key", max_retries=2)
        self.assertIs(client._request("GET", "/x"), ok)
        mock_sleep.assert_called_once_with(0.0)


if __name__ == "__main__":
    unittest.main()

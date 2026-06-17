import unittest
from unittest.mock import MagicMock, patch

import requests

from grid_minion.exceptions import GridAPIError, GridNetworkError
from grid_minion.rest_client import GridRestClient


class TestGridRestClient(unittest.TestCase):

    def setUp(self):
        self.client = GridRestClient(api_key="fake_key", max_retries=1)

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_404_returns_none_for_optional_files(self, mock_request):
        response = MagicMock()
        response.status_code = 404
        mock_request.return_value = response

        self.assertIsNone(self.client._request("GET", "/missing"))

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_400_raises_api_error(self, mock_request):
        response = MagicMock()
        response.status_code = 400
        mock_request.return_value = response

        with self.assertRaises(GridAPIError):
            self.client._request("GET", "/bad-request")

    @patch("grid_minion.rest_client.requests.Session.request")
    def test_request_exception_raises_network_error(self, mock_request):
        mock_request.side_effect = requests.exceptions.ConnectionError("offline")

        with self.assertRaises(GridNetworkError):
            self.client._request("GET", "/network")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch
from grid_minion.graphql_client import GridGraphQLClient

class TestGridGraphQLClient(unittest.TestCase):

    def setUp(self):
        self.api_key = "fake_key"
        self.client = GridGraphQLClient(self.api_key)

    def test_headers_init(self):
        """Prueba que los headers se inicializan con la API KEY"""
        headers = self.client.session.headers
        self.assertEqual(headers["x-api-key"], "fake_key")
        self.assertEqual(headers["Content-Type"], "application/json")

    @patch('grid_minion.graphql_client.requests.Session.post')
    def test_query_central_success(self, mock_post):
        """Prueba una query básica exitosa"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"series": {"id": "123"}}}
        mock_post.return_value = mock_response

        result = self.client.query_central("query { test }")
        
        self.assertEqual(result["series"]["id"], "123")
        mock_post.assert_called_with(
            self.client.URL_CENTRAL,
            json={'query': 'query { test }'},
            timeout=(10, 60)
        )

    @patch('grid_minion.graphql_client.requests.Session.post')
    def test_get_series_pagination_logic(self, mock_post):
        """
        Prueba crítica: El bucle while debe unir resultados de varias páginas
        """
        # Página 1
        page_1 = {
            "data": {
                "allSeries": {
                    "totalCount": 2,
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor_xyz"},
                    "edges": [{"node": {"id": "ID_A"}}]
                }
            }
        }
        # Página 2
        page_2 = {
            "data": {
                "allSeries": {
                    "totalCount": 2,
                    "pageInfo": {"hasNextPage": False, "endCursor": "cursor_end"},
                    "edges": [{"node": {"id": "ID_B"}}]
                }
            }
        }

        mock_resp_1 = MagicMock()
        mock_resp_1.json.return_value = page_1
        mock_resp_1.raise_for_status.return_value = None

        mock_resp_2 = MagicMock()
        mock_resp_2.json.return_value = page_2
        mock_resp_2.raise_for_status.return_value = None

        mock_post.side_effect = [mock_resp_1, mock_resp_2]

        ids = self.client.get_series(start_time="2024-01-01")

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(ids, ["ID_A", "ID_B"])
        print("\nTest de Paginacion superado (Grid Minion)")

if __name__ == '__main__':
    unittest.main()
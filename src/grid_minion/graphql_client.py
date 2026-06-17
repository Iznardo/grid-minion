import requests
import json
import time
import logging
import re
from typing import List, Dict, Any, Optional, Union
from .exceptions import GridAPIError, GridAuthError, GridRateLimitError, GridNetworkError, GridError

logger = logging.getLogger(__name__)
GRAPHQL_ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

class GridGraphQLClient:
    """
    Cliente para interactuar con las APIs de GraphQL de GRID.
    
    Proporciona métodos para consultar metadatos (Data Central) y el estado
    en tiempo real de las series (Live Data).
    """
    URL_CENTRAL = 'https://api.grid.gg/central-data/graphql'
    URL_LIVE = 'https://api.grid.gg/live-data-feed/series-state/graphql'

    def __init__(self, api_key: str, max_retries: int = 7, timeout: tuple = (10, 60)):
        """
        Inicializa el cliente GraphQL.

        Args:
            api_key (str): Tu clave de API de GRID.
            max_retries (int): Número máximo de reintentos para Rate Limits (default: 7).
            timeout (tuple): Timeout (connect, read) en segundos (default: (10, 60)).
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            "Content-Type": "application/json"
        })

    def _execute(self, url: str, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Ejecuta una petición GraphQL con lógica de reintentos y manejo de errores.
        """
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        # Bucle de reintentos
        for attempt in range(1, self.max_retries + 1):
            try:
                # Backoff exponencial (5s, 7.5s, 11.25s...)
                default_wait = 5 * (1.5 ** (attempt - 1))

                response = self.session.post(url, json=payload, timeout=self.timeout)
                
                # --- 1. GESTIÓN DE RATE LIMIT HTTP (429) ---
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else default_wait
                    
                    if attempt == self.max_retries:
                         raise GridRateLimitError(f"Rate Limit HTTP 429 persistente tras {self.max_retries} intentos.", status_code=429)

                    logger.warning(f"HTTP 429. Esperando {wait_time:.2f}s... ({attempt}/{self.max_retries})")
                    time.sleep(wait_time)
                    continue 

                # --- 2. GESTIÓN DE ERRORES DE AUTENTICACIÓN Y CLIENTE ---
                if response.status_code in [401, 403]:
                    raise GridAuthError(
                        f"Error de autenticación {response.status_code}: Verifique su API Key.",
                        status_code=response.status_code,
                    )

                if 400 <= response.status_code < 500:
                    raise GridAPIError(
                        f"Error HTTP {response.status_code} en GraphQL.",
                        status_code=response.status_code,
                    )

                # --- 3. GESTIÓN DE ERRORES DE SERVIDOR (5xx) ---
                if response.status_code >= 500:
                    if attempt == self.max_retries:
                        raise GridAPIError(
                            f"Error de servidor persistente {response.status_code}.",
                            status_code=response.status_code,
                        )
                    logger.warning(f"Error Servidor {response.status_code}. Reintentando en {default_wait:.2f}s...")
                    time.sleep(default_wait)
                    continue
                
                # --- 4. GESTIÓN DE ERRORES EN EL BODY (GraphQL) ---
                data = response.json()
                
                if "errors" in data:
                    is_rate_limit = False
                    error_msg = str(data['errors'])

                    for error in data["errors"]:
                        msg = error.get("message", "").lower()
                        code = error.get("extensions", {}).get("errorDetail", "")
                        
                        if "rate limit" in msg or "ENHANCE_YOUR_CALM" in code:
                            is_rate_limit = True
                            break
                    
                    if is_rate_limit:
                        # Si es el último intento, lanzamos error y no esperamos más
                        if attempt == self.max_retries:
                            raise GridRateLimitError(f"Rate Limit GraphQL persistente tras {self.max_retries} intentos. GRID dice: {error_msg}", details=data['errors'])

                        logger.warning(f"Rate Limit GraphQL detectado. Esperando {default_wait:.2f}s... ({attempt}/{self.max_retries})")
                        time.sleep(default_wait)
                        continue # Reintentamos

                    # Error legítimo de sintaxis o lógica (no se reintenta)
                    raise GridAPIError(f"GraphQL Error: {data['errors']}", details=data['errors'])
                
                # ÉXITO
                return data.get("data", {})

            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries:
                    raise GridNetworkError(f"Error de conexión Final tras {self.max_retries} intentos: {e}")
                
                logger.warning(f"Error de red ({e}). Reintentando en {default_wait:.2f}s...")
                time.sleep(default_wait)
                continue

        # Este punto teóricamente es inalcanzable por los 'raise' en el último intento,
        # pero por seguridad:
        raise GridError("Error crítico: Fallo en la lógica de reintentos.")

    def query_central(self, query_body: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Ejecuta una consulta en GRID Data Central (Metadatos).

        Args:
            query_body (str): Cuerpo de la consulta GraphQL.
            variables (Optional[Dict]): Variables para la consulta.

        Returns:
            Dict[str, Any]: Datos devueltos por la API.
        """
        return self._execute(self.URL_CENTRAL, query_body, variables)

    def query_live(self, query_body: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Ejecuta una consulta en GRID Live Data (Series State).

        Args:
            query_body (str): Cuerpo de la consulta GraphQL.
            variables (Optional[Dict]): Variables para la consulta.

        Returns:
            Dict[str, Any]: Datos devueltos por la API.
        """
        return self._execute(self.URL_LIVE, query_body, variables)

    def get_series(self, 
                   start_time: Optional[str] = None, 
                   end_time: Optional[str] = None, 
                   game_type: Optional[str] = None,
                   title_id: Union[int, List[int]] = 3, 
                   page_games: int = 25,
                   team_ids: Optional[Union[str, List[str]]] = None,
                   tournament_ids: Optional[Union[str, List[str]]] = None) -> List[str]:
        """
        Obtiene una lista de IDs de series filtradas por diversos criterios.
        Maneja automáticamente la paginación de la API.

        Args:
            start_time (Optional[str]): Fecha de inicio (ISO 8601).
            end_time (Optional[str]): Fecha de fin (ISO 8601).
            game_type (Optional[str]): Tipo de partida (ej: 'COMPETITIVE').
            title_id (Union[int, List[int]]): ID del juego (default: 3 para LoL).
            page_games (int): Número de series por página (max: 25).
            team_ids (Optional[Union[str, List[str]]]): IDs de equipos.
            tournament_ids (Optional[Union[str, List[str]]]): IDs de torneos.

        Returns:
            List[str]: Lista de IDs de series encontrados.
        """
        
        all_ids = []
        filter_parts = ""

        if start_time and end_time:
            filter_parts += f'startTimeScheduled: {{ gte: {json.dumps(start_time)}, lte: {json.dumps(end_time)} }}'
        elif start_time:
            filter_parts += f'startTimeScheduled: {{ gte: {json.dumps(start_time)} }}'
        elif end_time:
            filter_parts += f'startTimeScheduled: {{ lte: {json.dumps(end_time)} }}'

        filter_parts += f'\n titleIds: {{ in: {json.dumps(title_id)} }}'

        if game_type:
            if not GRAPHQL_ENUM_RE.match(str(game_type)):
                raise GridAPIError(
                    f"game_type no válido para GraphQL enum: {game_type!r}",
                    details={"game_type": game_type},
                )
            filter_parts += f'\n types: {game_type}'

        if team_ids:
            if isinstance(team_ids, list):
                filter_parts += f'\n teamIds: {{ in: {json.dumps(team_ids)} }}'
            else:
                filter_parts += f'\n teamId: {json.dumps(str(team_ids))}'

        if tournament_ids:
            if isinstance(tournament_ids, list):
                filter_parts += f'\n tournamentIds: {{ in: {json.dumps(tournament_ids)} }}'
            else:
                filter_parts += f'\n tournamentId: {json.dumps(str(tournament_ids))}'

        def _fetch_page(cursor=""):
            query = """
query GetGames($after: String, $first: Int!) {
    allSeries(
        after: $after
        first: $first
        filter: {
            FILTER_PLACEHOLDER
        }
        orderBy: StartTimeScheduled
    ) {
        totalCount
        pageInfo {
            hasNextPage
            endCursor
        }
        edges {
            node {
                id
            }
        }
    }
}
""".replace("FILTER_PLACEHOLDER", filter_parts)

            data = self.query_central(query, variables={"after": cursor, "first": page_games})
            series_data = data['allSeries']
            nodes = series_data['edges']
            current_ids = [n['node']['id'] for n in nodes]
            return current_ids, series_data['pageInfo']['hasNextPage'], series_data['pageInfo']['endCursor']

        has_next = True
        cursor = ""
        while has_next:
            new_ids, has_next, cursor = _fetch_page(cursor)
            all_ids.extend(new_ids)

        return all_ids

    def get_tournament_ids_by_name(self, parent_names: List[str]) -> List[str]:
        """
        Busca IDs de torneos basándose en una lista de nombres o fragmentos de nombres.

        Args:
            parent_names (List[str]): Lista de nombres a buscar (ej: ['LEC', 'LVP']).

        Returns:
            List[str]: Lista de IDs de torneos que coinciden con la búsqueda.
        """
        found_ids = set()
        query = """
query GetTournaments($name: String!, $after: String) {
    tournaments(first: 50, after: $after, filter: { name: { contains: $name } }) {
        pageInfo {
            hasNextPage
            endCursor
        }
        edges {
            node {
                id
                name
            }
        }
    }
}
"""
        for name in parent_names:
            cursor = None
            while True:
                data = self.query_central(query, variables={"name": name, "after": cursor})
                tournaments_data = data.get("tournaments", {})
                for edge in tournaments_data.get("edges", []):
                    if name in edge["node"]["name"]:
                        found_ids.add(edge["node"]["id"])
                page_info = tournaments_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
        return list(found_ids)

    def get_series_state(self, series_id: str) -> Dict[str, Any]:
        """
        Obtiene el estado actual de una serie (Live Data).

        Args:
            series_id (str): ID de la serie de GRID.

        Returns:
            Dict[str, Any]: Diccionario con el estado de la serie y sus partidas.
        """
        query = """
        query GetSeriesState($seriesId: ID!) {
            seriesState(id: $seriesId) {
                id
                games {
                    id
                    sequenceNumber
                    started
                    finished
                }
            }
        }
        """
        data = self.query_live(query, variables={"seriesId": str(series_id)})
        return data["seriesState"]

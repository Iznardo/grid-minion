import requests
import json
import time
import logging
from typing import List, Dict, Any, Optional, Union
from .exceptions import GridAPIError, GridRateLimitError, GridNetworkError, GridError

logger = logging.getLogger(__name__)

class GridGraphQLClient:
    """
    Cliente para interactuar con las APIs de GraphQL de GRID.
    
    Proporciona métodos para consultar metadatos (Data Central) y el estado
    en tiempo real de las series (Live Data).
    """
    URL_CENTRAL = 'https://api.grid.gg/central-data/graphql'
    URL_LIVE = 'https://api.grid.gg/live-data-feed/series-state/graphql'

    def __init__(self, api_key: str, max_retries: int = 7):
        """
        Inicializa el cliente GraphQL.

        Args:
            api_key (str): Tu clave de API de GRID.
            max_retries (int): Número máximo de reintentos para Rate Limits (default: 7).
        """
        self.max_retries = max_retries
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
                # Factor de espera: 2s, 3s, 4.5s, 6.75s... (Más lento que antes)
                default_wait = 10 * (1.5 ** (attempt - 1))

                response = self.session.post(url, json=payload)
                
                # --- 1. GESTIÓN DE RATE LIMIT HTTP (429) ---
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else default_wait
                    
                    if attempt == self.max_retries:
                         raise GridRateLimitError(f"Rate Limit HTTP 429 persistente tras {self.max_retries} intentos.", status_code=429)

                    logger.warning(f"HTTP 429. Esperando {wait_time:.2f}s... ({attempt}/{self.max_retries})")
                    time.sleep(wait_time)
                    continue 

                # Si es otro error HTTP (5xx, etc)
                response.raise_for_status()
                
                # --- 2. GESTIÓN DE ERRORES EN EL BODY (GraphQL) ---
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
                   team_ids: Union[str, List[str]] = None, 
                   tournament_ids: Union[str, List[str]] = None) -> List[str]:
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
            filter_parts += f'startTimeScheduled: {{ gte: "{start_time}", lte: "{end_time}" }}'
        elif start_time:
            filter_parts += f'startTimeScheduled: {{ gte: "{start_time}" }}'
        elif end_time:
            filter_parts += f'startTimeScheduled: {{ lte: "{end_time}" }}'

        filter_parts += f'\n titleIds: {{ in: {json.dumps(title_id)} }}'

        if game_type:
            filter_parts += f'\n types: {game_type}'

        if team_ids:
            if isinstance(team_ids, list):
                filter_parts += f'\n teamIds: {{ in: {json.dumps(team_ids)} }}'
            else:
                filter_parts += f'\n teamId: "{team_ids}"'

        if tournament_ids:
            if isinstance(tournament_ids, list):
                filter_parts += f'\n tournamentIds: {{ in: {json.dumps(tournament_ids)} }}'
            else:
                filter_parts += f'\n tournamentId: "{tournament_ids}"'

        def _fetch_page(cursor=""):
            body = """
            query GetGames {{
                allSeries(
                    after: "{cursor}"
                    first: {page_games}
                    filter: {{
                        {filter_parts}
                    }}
                    orderBy: StartTimeScheduled
                ) {{
                    totalCount
                    pageInfo {{
                        hasNextPage
                        endCursor
                    }}
                    edges {{
                        node {{
                            id
                        }}
                    }}
                }}
            }}
            """.format(cursor=cursor, page_games=page_games, filter_parts=filter_parts)
            
            data = self.query_central(body)
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
        for name in parent_names:
            query = f"""
            query {{
              tournaments(first: 50, filter: {{ name: {{ contains: "{name}" }} }}) {{
                edges {{
                  node {{
                    id
                    name
                  }}
                }}
              }}
            }}
            """
            data = self.query_central(query)
            edges = data.get("tournaments", {}).get("edges", [])
            for edge in edges:
                if name in edge["node"]["name"]:
                    found_ids.add(edge["node"]["id"])
        return list(found_ids)

    def get_series_state(self, series_id: str) -> Dict[str, Any]:
        """
        Obtiene el estado actual de una serie (Live Data).

        Args:
            series_id (str): ID de la serie de GRID.

        Returns:
            Dict[str, Any]: Diccionario con el estado de la serie y sus partidas.
        """
        query = f"""
        query {{
            seriesState(id: {series_id}) {{
                id
                games {{
                    id
                    sequenceNumber
                    started
                    finished
                }}
            }}
        }}
        """
        data = self.query_live(query)
        return data["seriesState"]

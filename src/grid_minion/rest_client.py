import requests
import time
import json
import logging
from zipfile import ZipFile
from io import BytesIO
from typing import Dict, Any, Optional, List, Union
from .exceptions import (
    GridAPIError, 
    GridAuthError, 
    GridRateLimitError, 
    GridResourceNotFoundError, 
    GridNetworkError, 
    GridDataError
)

logger = logging.getLogger(__name__)

class GridRestClient:
    """
    Cliente para interactuar con las APIs REST de GRID para la descarga de archivos.
    
    Permite descargar resúmenes de Riot (End State), timelines (LiveStats) y 
    eventos comprimidos de GRID.
    """
    BASE_URL = "https://api.grid.gg"

    def __init__(self, api_key: str, max_retries: int = 5, timeout: tuple = (10, 60)):
        """
        Inicializa el cliente REST.

        Args:
            api_key (str): Tu clave de API de GRID.
            max_retries (int): Número máximo de reintentos para Rate Limits o errores 5xx (default: 5).
            timeout (tuple): Timeout (connect, read) en segundos (default: (10, 60)).
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            # Aceptamos JSON y zip
            "Accept": "application/json, application/zip" 
        })

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, stream: bool = False) -> Optional[requests.Response]:
        """
        Método interno que gestiona la construcción de URL, reintentos y errores comunes.
        """
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        
        for attempt in range(1, self.max_retries + 1):
            try:
                # Backoff exponencial (5s, 7.5s, 11.25s, 16.9s, 25.3s)
                default_wait = 5 * (1.5 ** (attempt - 1))

                response = self.session.request(method, url, params=params, stream=stream, timeout=self.timeout)

                # --- 1. GESTIÓN RATE LIMIT (429) ---
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else default_wait
                    
                    if attempt == self.max_retries:
                        raise GridRateLimitError(f"Rate Limit HTTP 429 persistente tras {self.max_retries} intentos.", status_code=429)

                    logger.warning(f"HTTP 429. Esperando {wait_time:.2f}s... ({attempt}/{self.max_retries})")
                    time.sleep(wait_time)
                    continue

                # --- 2. GESTIÓN DE ARCHIVO NO ENCONTRADO (404) ---
                if response.status_code == 404:
                    logger.warning(f"404 No encontrado: {endpoint}")
                    return None

                # --- 3. GESTIÓN DE ERRORES DE AUTENTICACIÓN (401, 403) ---
                if response.status_code in [401, 403]:
                    raise GridAuthError(f"Error de autenticación {response.status_code}: Verifique su API Key.", status_code=response.status_code)

                # --- 4. GESTIÓN DE ERRORES DE SERVIDOR (5xx) ---
                if response.status_code >= 500:
                    if attempt == self.max_retries:
                         raise GridAPIError(f"Error de servidor persistente {response.status_code}.", status_code=response.status_code)
                    
                    logger.warning(f"Error Servidor {response.status_code}. Reintentando en {default_wait}s...")
                    time.sleep(default_wait)
                    continue

                # Si llegamos aquí y no es 200 OK, lanzamos error genérico de API
                response.raise_for_status()
                
                return response

            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries:
                    raise GridNetworkError(f"Error de conexión final tras {self.max_retries} intentos: {e}")
                
                logger.warning(f"Error de red ({e}). Reintentando...")
                time.sleep(default_wait)
                continue
        
        return None

    def get_available_files(self, series_id: str) -> List[Dict[str, Any]]:
        """
        Consulta qué archivos están listos para descargar para una serie.

        Args:
            series_id (str): ID de la serie de GRID.

        Returns:
            List[Dict[str, Any]]: Lista de metadatos de archivos disponibles.
        """
        endpoint = f"/file-download/list/{series_id}"
        response = self._request("GET", endpoint)
        if response:
            return response.json().get("files", [])
        return []

    def get_riot_summary(self, series_id: str, game_number: int = 1) -> Optional[Dict[str, Any]]:
        """
        Descarga el resumen del juego (End State) proporcionado por Riot.

        Args:
            series_id (str): ID de la serie de GRID.
            game_number (int): Número de partida dentro de la serie (default: 1).

        Returns:
            Optional[Dict[str, Any]]: Diccionario con los datos finales de la partida.
        """
        endpoint = f"/file-download/end-state/riot/series/{series_id}/games/{game_number}/summary"
        response = self._request("GET", endpoint)
        
        if response:
            try:
                return response.json()
            except json.JSONDecodeError as e:
                raise GridDataError(f"Error parseando JSON de Riot Summary: {e}")
        return None

    def get_riot_livestats(self, series_id: str, game_number: int = 1, parse_json: bool = True) -> Union[List[Dict], str, None]:
        """
        Descarga los LiveStats de Riot (Timeline en formato JSON Lines).

        Args:
            series_id (str): ID de la serie de GRID.
            game_number (int): Número de partida (default: 1).
            parse_json (bool): Si es True, parsea el JSONL a una lista de diccionarios.

        Returns:
            Union[List[Dict], str, None]: Lista de eventos, string crudo o None.
        """
        endpoint = f"/file-download/events/riot/series/{series_id}/games/{game_number}"
        response = self._request("GET", endpoint)

        if not response:
            return None

        content = response.text
        if parse_json:
            try:
                events = [json.loads(line) for line in content.strip().split("\n") if line.strip()]
                return events
            except json.JSONDecodeError as e:
                raise GridDataError(f"Error parseando JSONL de Riot LiveStats: {e}")
        return content

    def get_grid_events(self, series_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Descarga y descomprime los eventos de GRID (ZIP conteniendo archivos JSONL).

        Args:
            series_id (str): ID de la serie de GRID.

        Returns:
            Optional[List[Dict[str, Any]]]: Lista combinada de todos los eventos encontrados.
        """
        endpoint = f"/file-download/events/grid/series/{series_id}"
        response = self._request("GET", endpoint)

        if not response:
            return None

        try:
            combined_events = []
            with ZipFile(BytesIO(response.content)) as zip_file:
                filelist = zip_file.namelist()
                if not filelist:
                    logger.warning(f"ZIP vacío para series {series_id}")
                    return None

                for filename in filelist:
                    if filename.endswith(".jsonl"):
                        with zip_file.open(filename) as infile:
                            lines = infile.read().decode('utf-8').strip().split('\n')
                            events = [json.loads(line) for line in lines if line.strip()]
                            combined_events.extend(events)
            return combined_events
        except Exception as e:
            raise GridDataError(f"Error procesando ZIP de GRID para {series_id}: {e}")

    def get_grid_endstate(self, series_id: str) -> Optional[Dict[str, Any]]:
        """
        Descarga el estado final de la serie de GRID (Rosters y PUUIDs).

        Args:
            series_id (str): ID de la serie de GRID.

        Returns:
            Optional[Dict[str, Any]]: Diccionario con el EndState de GRID.
        """
        endpoint = f"/file-download/end-state/grid/series/{series_id}"
        response = self._request("GET", endpoint)
        
        if response:
            try:
                return response.json()
            except json.JSONDecodeError as e:
                raise GridDataError(f"Error parseando JSON del EndState de GRID para {series_id}: {e}")
        return None

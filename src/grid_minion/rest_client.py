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
    BASE_URL = "https://api.grid.gg"

    def __init__(self, api_key: str, max_retries: int = 5):
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            # Aceptamos JSON y zip
            "Accept": "application/json, application/zip" 
        })

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, stream: bool = False) -> Optional[requests.Response]:
        """
        Método interno que gestiona:
        1. Construcción de URL
        2. Rate Limits (429)
        3. Errores de Servidor (5xx)
        4. Retorno de None si es 404 (Archivo no encontrado)
        """
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        
        for attempt in range(1, self.max_retries + 1):
            try:
                # Estrategia de espera (Base 5s)
                default_wait = 5 + attempt 

                response = self.session.request(method, url, params=params, stream=stream)

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

    # ---------------------------------------------------------
    # 1. MÉTODOS DE UTILIDAD (Documentación GRID)
    # ---------------------------------------------------------

    def get_available_files(self, series_id: str) -> List[Dict[str, Any]]:
        """
        Consulta qué archivos están listos para descargar (Endpoint /file-download/list).
        Útil para debugging, aunque no es obligatorio llamar antes de descargar.
        """
        endpoint = f"/file-download/list/{series_id}"
        response = self._request("GET", endpoint)
        if response:
            return response.json().get("files", [])
        return []

    # ---------------------------------------------------------
    # 2. MÉTODOS DE RIOT (Summary & LiveStats)
    # ---------------------------------------------------------

    def get_riot_summary(self, series_id: str, game_number: int = 1) -> Optional[Dict[str, Any]]:
        """
        Descarga el resumen del juego (End State).
        Equivalente a tu función 'get_summary'.
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
        Descarga los LiveStats de Riot (JSON Lines).
        
        :param parse_json: Si es True, devuelve una lista de diccionarios (eventos).
                           Si es False, devuelve el texto crudo (str).
        """
        endpoint = f"/file-download/events/riot/series/{series_id}/games/{game_number}"
        response = self._request("GET", endpoint)

        if not response:
            return None

        content = response.text
        if parse_json:
            # Convertimos el JSONL (linea a linea) en lista de dicts
            try:
                events = [json.loads(line) for line in content.strip().split("\n") if line.strip()]
                return events
            except json.JSONDecodeError as e:
                raise GridDataError(f"Error parseando JSONL de Riot LiveStats: {e}")
        return content

    # ---------------------------------------------------------
    # 3. MÉTODOS DE GRID (Events & State)
    # ---------------------------------------------------------

    def get_grid_events(self, series_id: str) -> Optional[List[Dict[str, Any]]]:
            """
            Descarga y DESCOMPRIME los eventos de GRID (ZIP -> JSONL).
            Lee TODOS los archivos .jsonl que vengan dentro del ZIP.
            """
            endpoint = f"/file-download/events/grid/series/{series_id}"
            response = self._request("GET", endpoint, stream=True)

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
                            # logger.info(f"Procesando archivo del ZIP: {filename}")
                            with zip_file.open(filename) as infile:
                                lines = infile.read().decode('utf-8').strip().split('\n')
                                events = [json.loads(line) for line in lines if line.strip()]
                                combined_events.extend(events)
                
                return combined_events

            except Exception as e:
                raise GridDataError(f"Error procesando ZIP de GRID para {series_id}: {e}")

    def get_grid_endstate(self, series_id: str) -> Optional[Dict[str, Any]]:
        """
        Descarga el estado final de la serie de GRID en formato JSON.
        Contiene el Roster global y los PUUIDs de los jugadores.
        """
        endpoint = f"/file-download/end-state/grid/series/{series_id}"
        response = self._request("GET", endpoint)
        
        if response:
            try:
                return response.json()
            except json.JSONDecodeError as e:
                raise GridDataError(f"Error parseando JSON del EndState de GRID para {series_id}: {e}")
        return None

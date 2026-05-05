class GridError(Exception):
    """Clase base para todas las excepciones de Grid Minion."""
    pass

class GridAPIError(GridError):
    """Error devuelto por la API de GRID (status codes no exitosos o errores en el body)."""
    def __init__(self, message, status_code=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

class GridAuthError(GridAPIError):
    """Error de autenticación (API Key inválida o sin permisos)."""
    pass

class GridRateLimitError(GridAPIError):
    """Límite de velocidad (Rate Limit) alcanzado y reintentos agotados."""
    pass

class GridResourceNotFoundError(GridAPIError):
    """El recurso solicitado (ej: archivo de partida) no existe (404)."""
    pass

class GridNetworkError(GridError):
    """Error de conexión, timeout o red al intentar comunicar con GRID."""
    pass

class GridDataError(GridError):
    """Error al procesar, descomprimir o parsear los datos recibidos."""
    pass

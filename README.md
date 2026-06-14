# Grid Minion

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

Grid Minion es un cliente de Python ligero y robusto para interactuar con las APIs de GRID.gg (Data Central y Live Data), diseñado específicamente para analistas y desarrolladores de League of Legends.

Esta libreria no solo facilita la descarga de datos, sino que implementa un sistema de Observadores que procesa y cruza automaticamente los datos de GRID y Riot (PUUIDs, KDA, Objetivos, Vision) en tiempo real o diferido.

## Caracteristicas

*   Clientes Especializados:
    *   GridGraphQLClient: Consulta de metadatos, torneos y series con paginacion automatica.
    *   GridRestClient: Descarga de archivos (Summary, LiveStats, GRID Events) con gestion de descompresion ZIP.
*   Sistema de Observadores: Procesa eventos complejos y mantiene el estado de la partida (Drafts, Inventarios, Posicionamiento).
*   Robustez Industrial:
    *   Gestion avanzada de Rate Limits (HTTP 429 y GraphQL body errors) con reintentos exponenciales.
    *   Jerarquia de excepciones personalizadas (GridRateLimitError, GridAuthError, etc.).
    *   Sistema de logging integrado.

## Instalacion

```bash
pip install grid-minion
```
(O clonando el repo para desarrollo)
```bash
git clone https://github.com/tu_usuario/pyGrid.git
cd pyGrid
pip install -e .
```

## Inicio Rapido

### 1. Consultar Series (GraphQL)
```python
from grid_minion import GridGraphQLClient

client = GridGraphQLClient(api_key="TU_API_KEY")

# Obtener IDs de series de la LEC en un rango de fechas
series_ids = client.get_series(
    start_time="2024-01-01T00:00:00Z",
    tournament_ids=["ID_TORNEO"]
)
```

### 2. Descargar y Procesar Partidas (Observers)
El corazon de la libreria es el GameEventProcessor. Puedes enganchar diferentes observadores para obtener exactamente los datos que necesitas.

```python
from grid_minion import GridRestClient, split_grid_series
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver, 
    PostGameObserver, ObjectiveKilledObserver, WardsObserver
)

client = GridRestClient(api_key="TU_API_KEY")
processor = GameEventProcessor()

# Instanciar observadores
teams_obs = TeamsObserver()
draft_obs = DraftObserver()
wards_obs = WardsObserver(teams_observer=teams_obs) # Dependencia de equipos

# Suscribirlos al procesador
processor.attach(teams_obs)
processor.attach(draft_obs)
processor.attach(wards_obs)

# Descargar datos
grid_events = client.get_grid_events("SERIES_ID")
riot_summary = client.get_riot_summary("SERIES_ID", game_number=1)

# Procesar todo el paquete (el procesador gestiona el orden de ingesta)
processor.process_bundle(
    grid_livestats=grid_events,
    riot_summary=riot_summary
)

# Acceder a los resultados limpios
print(f"Draft: {draft_obs.get_draft()}")
print(f"Wards totales: {len(wards_obs.get_wards())}")
```

### 3. Normalizacion de campeones (Data Dragon)

GRID y el summary de Riot nombran los campeones de forma distinta: el draft de
GRID usa el *display name* (`"Wukong"`, `"Lee Sin"`) y el summary la *clave
interna* de Riot (`"MonkeyKing"`, `"LeeSin"`). Para poder cruzarlos sin
ambiguedad, la libreria normaliza ambos lados contra **Data Dragon**.

- En la **primera normalizacion** se consulta la ultima version de Data Dragon
  y se cachea en disco (`~/.cache/grid_minion/ddragon/`, configurable con la
  variable de entorno `GRID_MINION_CACHE_DIR`). En ejecuciones posteriores solo
  se comprueba la version; sin cambios, se usa la cache.
- Sin red pero con cache previa → se usa la cache (con un aviso). Sin red **ni**
  cache → se lanza `GridNetworkError`.

`get_draft()` devuelve cada pick/ban ya normalizado como
`{"name": <clave Riot>, "id": <id numerico>}` (los baneos saltados son `None`):

```python
draft = draft_obs.get_draft()
# {
#   "draft_found": True, "is_complete": True,
#   "fp": {"team_id": "52457",
#          "picks": [{"name": "MonkeyKing", "id": 62}, ...],
#          "bans":  [{"name": "JarvanIV", "id": 59}, None, ...]},
#   "sp": {...}
# }
```

`get_game_stats(teams_obs)` añade `champion_id` (id numerico de Riot) por jugador,
junto al `champion` (clave de Riot) ya existente. Tambien puedes normalizar a mano:

```python
from grid_minion import normalize_champion
normalize_champion("Wukong")      # -> ("MonkeyKing", 62)
normalize_champion("MonkeyKing")  # -> ("MonkeyKing", 62)
```

## Manejo de Errores

```python
from grid_minion import GridRateLimitError, GridAuthError

try:
    data = client.query_central(query)
except GridRateLimitError:
    print("Se agotaron los reintentos de velocidad.")
except GridAuthError:
    print("API Key invalida.")
```

## Tests

Ejecutar la suite de pruebas unitarias e integracion:
```bash
python -m unittest discover tests
```

## Licencia

Este proyecto esta bajo la Licencia MIT. Consulta el archivo LICENSE para mas detalles.

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

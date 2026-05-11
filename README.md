# Grid Minion

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

Cliente de Python no oficial para las APIs de [GRID.gg](https://grid.gg/), enfocado en datos competitivos de League of Legends. Combina descarga de datos (GraphQL + REST) con un sistema de observers que reconstruye automáticamente equipos, drafts, objetivos, visión y estadísticas a partir del cruce de datos de GRID y Riot.

---

## Tabla de contenidos

- [Instalación](#instalación)
- [Quick start](#quick-start)
- [Conceptos clave](#conceptos-clave)
- [Clientes](#clientes)
  - [GridGraphQLClient](#gridgraphqlclient)
  - [GridRestClient](#gridrestclient)
- [Observers](#observers)
  - [TeamsObserver](#teamsobserver)
  - [DraftObserver](#draftobserver)
  - [PostGameObserver](#postgameobserver)
  - [ObjectiveKilledObserver](#objectivekilledobserver)
  - [WardsObserver](#wardsobserver)
- [GameEventProcessor](#gameeventprocessor)
- [Utilidades](#utilidades)
- [Manejo de errores](#manejo-de-errores)
- [Logging](#logging)
- [Desarrollo](#desarrollo)

---

## Instalación

```bash
pip install grid-minion
```

Para desarrollo (modo editable con dependencias de test):
```bash
git clone https://github.com/Iznardo/grid-minion.git
cd grid-minion
pip install -e ".[dev]"
```

Requiere Python 3.8+ y una API Key válida de GRID.

---

## Quick start

Analizar una serie completa desde GRID y obtener equipos, draft y resultado final:

```python
import os
from dotenv import load_dotenv
from grid_minion import GridRestClient, split_grid_series
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver, DraftObserver, PostGameObserver
)

load_dotenv()
client = GridRestClient(api_key=os.getenv("GRID_API_KEY"))

# 1. Descargar la serie completa
series_id = "2922522"
grid_events = client.get_grid_events(series_id)

# 2. Partir la serie en partidas individuales
games = split_grid_series(grid_events)
print(f"Detectadas {len(games)} partidas")

# 3. Procesar la primera partida
processor = GameEventProcessor()
teams_obs = TeamsObserver()
draft_obs = DraftObserver()
stats_obs = PostGameObserver()

processor.attach(teams_obs)
processor.attach(draft_obs)
processor.attach(stats_obs)

processor.process_bundle(
    grid_livestats=games[0],
    riot_summary=client.get_riot_summary(series_id, game_number=1),
    riot_livestats=client.get_riot_livestats(series_id, game_number=1)
)

# 4. Acceder a los resultados
print(f"Ganador: {stats_obs.get_game_stats(teams_obs)['meta']['winner']}")
print(f"Picks del FP: {draft_obs.get_draft()['fp']['picks']}")
```

---

## Conceptos clave

### Arquitectura

La librería tiene dos capas independientes:

1. **Clientes (`GridGraphQLClient`, `GridRestClient`):** descargan datos de GRID y Riot. Gestionan rate limits, reintentos, timeouts y autenticación.
2. **Observers:** clases que reciben eventos uno a uno y mantienen estado parcial del partido. Cada observer es una vista especializada (equipos, draft, objetivos, etc.) y se acopla al `GameEventProcessor`.

Esto permite componer únicamente lo que necesitas. Si solo quieres el draft, instancias `DraftObserver` y nada más.

### Fuentes de datos

GRID expone tres fuentes complementarias para una serie de LoL:

| Fuente | Qué contiene | Cuándo llega |
|--------|--------------|--------------|
| **GRID livestats** | Picks/bans del draft, IDs internos de GRID, eventos administrativos (invalidaciones, side rotation) | Durante y tras la partida |
| **Riot summary** | End-state oficial: ganador, KDA final, oro, daño, versión del juego | Tras el final de la partida |
| **Riot livestats** | Timeline detallado: kills, wards, objetivos, posiciones | Durante la partida (JSONL) |

`process_bundle` los combina en el orden correcto: GRID state → Riot summary → GRID livestats → Riot livestats.

### El cruce PUUID

Los IDs de Riot (1–10) y los IDs internos de GRID (numéricos) se cruzan a través del PUUID que aparece en ambos lados. `TeamsObserver` mantiene un mapa `puuid → grid_player_id` y actualiza retroactivamente los `Participant` cuando llega información de cualquier fuente.

---

## Clientes

### `GridGraphQLClient`

Cliente para los endpoints GraphQL de GRID (Data Central y Live Data).

```python
from grid_minion import GridGraphQLClient

client = GridGraphQLClient(
    api_key="TU_API_KEY",
    max_retries=7,           # opcional, default 7
    timeout=(10, 60)         # opcional: (connect, read) en segundos
)
```

**Métodos:**

```python
# Buscar series con filtros. Pagina automáticamente.
series_ids = client.get_series(
    start_time="2025-01-01T00:00:00Z",
    end_time="2025-03-01T00:00:00Z",
    game_type="COMPETITIVE",            # opcional
    title_id=3,                          # 3 = LoL (default)
    tournament_ids=["775514"],           # opcional, str o List[str]
    team_ids=None,                       # opcional, str o List[str]
    page_games=25                        # max 25 por página
)

# Resolver torneos por nombre/fragmento. Pagina automáticamente.
ids = client.get_tournament_ids_by_name(["LEC", "LCK"])

# Estado de una serie concreta
state = client.get_series_state(series_id="2922522")
# {'id': '2922522', 'games': [{'id': '...', 'started': True, ...}]}

# Consultas crudas si necesitas algo no envuelto
data = client.query_central(query, variables={...})
data = client.query_live(query, variables={...})
```

Las queries internas usan **variables GraphQL parametrizadas** (no interpolación de strings).

### `GridRestClient`

Cliente para los endpoints REST de descarga de archivos.

```python
from grid_minion import GridRestClient

client = GridRestClient(
    api_key="TU_API_KEY",
    max_retries=5,           # opcional, default 5
    timeout=(10, 60)         # opcional: (connect, read)
)
```

**Métodos:**

```python
# Listar qué archivos están disponibles para una serie
files = client.get_available_files(series_id="2922522")

# Resumen de Riot (end-state oficial)
summary = client.get_riot_summary(series_id="2922522", game_number=1)
# → dict o None si no disponible

# Timeline de Riot (JSONL parseado a lista de dicts por defecto)
events = client.get_riot_livestats(series_id="2922522", game_number=1)
# Si quieres el texto crudo: parse_json=False

# Eventos de GRID (ZIP con JSONLs, ya descomprimidos y combinados)
grid_events = client.get_grid_events(series_id="2922522")

# End-state de GRID (rosters y PUUIDs)
endstate = client.get_grid_endstate(series_id="2922522")
```

Ambos clientes gestionan automáticamente:
- HTTP 429 con `Retry-After`.
- Rate limits en el body GraphQL (`ENHANCE_YOUR_CALM`).
- Reintentos con backoff exponencial (`5 × 1.5^(n-1)`).
- Errores 5xx con reintentos, 4xx con excepciones tipadas.

---

## Observers

Todos los observers heredan de la clase abstracta `Observer` y exponen un método `notify_event(event)`. El consumidor accede al estado a través de getters específicos por observer.

### `TeamsObserver`

Mantiene la lista de los 10 participantes y el cruce PUUID→GRID ID.

```python
from grid_minion.observers import TeamsObserver

teams = TeamsObserver()
# ... tras process_bundle ...

player = teams.get_player_by_id(1)  # Participant | None
print(player.summoner_name)         # "T1 Faker"
print(player.champion_name)         # "Orianna"
print(player.team_side)             # "BLUE" (@property derivada de team_id)
print(player.grid_player_id)        # ID interno de GRID, o None si no cruzado
print(player.puuid)                 # PUUID hex

teams.get_player_name(1)            # "T1 Faker" o "Unknown"
teams.get_player_team(1)            # "BLUE" / "RED" / "UNKNOWN"
```

`Participant` es un `@dataclass`; `team_side` se calcula desde `team_id` (100 → BLUE, 200 → RED).

### `DraftObserver`

Reconstruye la fase de picks/bans y maneja remakes.

```python
from grid_minion.observers import DraftObserver

draft = DraftObserver()
# ... tras process_bundle ...

if draft.draft_found:
    d = draft.get_draft()
    print(d["fp"]["picks"])       # ['Ambessa', 'Nocturne', ...]
    print(d["fp"]["bans"])        # ['Jarvan IV', ...] (None = baneo saltado)
    print(d["fp"]["team_id"])     # ID GRID del First Pick
    print(d["sp"]["picks"])       # picks del Second Pick
    print(d["is_complete"])       # True si ambos tienen 5 picks

# Historial de borradores invalidados (remakes)
for past in draft.draft_history:
    print(past)
```

**Lógica:** si la serie se rehace solo para invertir lados (ej. ligas ERL sin tecnología de side-pick), `get_draft()` detecta que los 20 campeones coinciden exactamente con un draft anterior y devuelve el original (con la asignación de lados primera). Si cambia algún pick, se considera remake real.

### `PostGameObserver`

Recopila KDA, oro, CS, daño y determina el ganador.

```python
from grid_minion.observers import PostGameObserver

stats = PostGameObserver()
# ... tras process_bundle ...

# Necesita un TeamsObserver para enriquecer con nombres y campeones
report = stats.get_game_stats(teams_observer=teams)

report["meta"]["winner"]         # "BLUE" / "RED"
report["meta"]["winner_source"]  # "summary" | "game_end" | "gold_heuristic"
report["meta"]["version"]        # "14.1"

for pid, p in report["players"].items():
    print(f"{p['name']} ({p['champion']}): {p['kda_str']}")
    # p = {'kills', 'deaths', 'assists', 'gold', 'cs', 'damage_dealt',
    #      'kda_str', 'name', 'champion', 'side', 'source'}
```

**Jerarquía del ganador:**

1. **`summary`:** Riot Summary expone `team.win == True`.
2. **`game_end`:** evento `rfc461Schema: "game_end"` de Riot LiveStats con campo `winningTeam` (100 = BLUE, 200 = RED). Si la partida termina sana pero sin summary.
3. **`gold_heuristic`:** fallback para scrims donde nadie esperó al final. Se infiere del último `stats_update`: equipo con más oro = ganador. Marcado explícitamente como `gold_heuristic`.

### `ObjectiveKilledObserver`

Cuenta dragones, barones, heraldos, voidgrubs y atakhans.

```python
from grid_minion.observers import ObjectiveKilledObserver

objs = ObjectiveKilledObserver()
# ... tras process_bundle ...

result = objs.get_all_objectives()
# {'dragons': [...], 'barons': [...], 'heralds': [...],
#  'voidgrubs': [...], 'atakhans': [...]}

for d in result["dragons"]:
    print(f"[{d['time']:.0f}s] {d['team']} {d['type']}")
    # d = {'time', 'team', 'type', 'killer_id'}
    # team: "BLUE" / "RED" / "NEUTRAL"
```

### `WardsObserver`

Registra colocación de centinelas. Depende de `TeamsObserver` para resolver el nombre y lado del jugador.

```python
from grid_minion.observers import WardsObserver

wards = WardsObserver(teams_observer=teams)  # inyección obligatoria
# ... tras process_bundle ...

for w in wards.get_wards():
    print(f"[{w['time']:.0f}s] {w['placer']} ({w['team']}) → {w['type']}")
    # w = {'time', 'placer', 'team', 'type', 'position': {'x', 'y'}}
```

---

## GameEventProcessor

El orquestador que distribuye eventos a los observers en el orden correcto.

```python
from grid_minion.observers import GameEventProcessor

processor = GameEventProcessor()
processor.attach(observer)
```

### Orden de `attach()` (importante)

Los observers que dependen de otro deben registrarse **después**:

```python
processor.attach(teams_obs)        # sin dependencias
processor.attach(draft_obs)        # sin dependencias
processor.attach(stats_obs)        # usa TeamsObserver en get_game_stats
processor.attach(objectives_obs)   # sin dependencias
processor.attach(wards_obs)        # depende de TeamsObserver (inyectado en __init__)
```

### Procesar un bundle completo

```python
processor.process_bundle(
    grid_state=None,                  # opcional: dict con estado pre-partida
    riot_summary=riot_summary,        # opcional: dict con end-state
    grid_livestats=grid_events,       # opcional: lista de eventos GRID
    riot_livestats=riot_events        # opcional: lista de eventos Riot timeline
)
```

El procesador ordena las fuentes así: `GRID_STATE → RIOT_SUMMARY → GRID livestats → Riot livestats`. Cualquier subconjunto es válido (puedes procesar solo `riot_summary` si quieres únicamente stats finales).

### Procesar eventos sueltos

```python
processor.process_events(lista_de_eventos)
```

### Aislamiento de excepciones

`_notify_all` envuelve cada observer en `try/except`: si uno explota procesando un evento concreto, se loggea con `logger.exception` y el resto continúa. Un evento corrupto no destruye el procesamiento del bundle completo.

### Crear tu propio observer

```python
from grid_minion.observers import Observer

class MyKillsObserver(Observer):
    def __init__(self):
        self.kills = []

    def notify_event(self, event):
        if event.get("rfc461Schema") == "champion_kill":
            self.kills.append({
                "time": event.get("gameTime", 0) / 1000,
                "killer": event.get("killer"),
                "victim": event.get("victim")
            })

processor.attach(MyKillsObserver())
```

---

## Utilidades

### `split_grid_series`

Divide los eventos crudos de una serie completa en bloques de partidas individuales.

```python
from grid_minion import split_grid_series

grid_events_full = client.get_grid_events("2922522")
games = split_grid_series(grid_events_full)
# games es List[List[Dict]] — una lista por partida
```

La heurística detecta el final de partida por la aparición de un nuevo draft (`team-picked-character` / `team-banned-character`) mientras `game_in_progress` está activo. Funciona con BO1/BO3/BO5 y maneja remakes intermedios.

---

## Manejo de errores

Jerarquía de excepciones:

```
GridError                    # base
├── GridAPIError             # error devuelto por la API
│   ├── GridAuthError        # 401, 403
│   ├── GridRateLimitError   # 429 o ENHANCE_YOUR_CALM tras agotar reintentos
│   └── GridResourceNotFoundError
├── GridNetworkError         # timeout, DNS, conexión perdida
└── GridDataError            # JSON malformado, ZIP corrupto
```

```python
from grid_minion import GridRateLimitError, GridAuthError, GridNetworkError

try:
    series = client.get_series(start_time="2025-01-01T00:00:00Z")
except GridAuthError:
    print("API Key inválida o sin permisos para este endpoint.")
except GridRateLimitError as e:
    print(f"Rate limit persistente: {e}")
except GridNetworkError as e:
    print(f"Timeout o conexión perdida: {e}")
```

---

## Logging

La librería usa `logging.getLogger(__name__)` en cada módulo y nunca configura handlers (comportamiento estándar para librerías). Para ver mensajes en tu aplicación:

```python
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Solo logs de grid-minion:
logging.getLogger("grid_minion").setLevel(logging.DEBUG)
```

Niveles que usa la librería:
- `DEBUG`: cruces PUUID exitosos, eventos parsedos.
- `WARNING`: rate limits, retries, 404s, eventos malformados que se ignoran.
- `EXCEPTION`: observers que lanzan excepciones (no detienen el procesamiento).

---

## Desarrollo

### Ejecutar tests

```bash
python -m unittest discover tests/
```

La suite incluye:
- Tests unitarios por observer (`test_observers_unit.py`).
- Test de integración con samples (`test_observers.py`).
- Tests de paginación y mock de GraphQL (`test_gql_mock.py`).
- Tests de `split_grid_series` (`test_utils.py`).

### Estructura del repositorio

```
src/grid_minion/
├── __init__.py
├── exceptions.py
├── graphql_client.py        # cliente GraphQL (Data Central + Live Data)
├── rest_client.py           # cliente REST (descarga de archivos)
├── utils.py                 # split_grid_series
└── observers/
    ├── __init__.py
    ├── base.py              # clase abstracta Observer
    ├── processor.py         # GameEventProcessor
    ├── teams.py             # TeamsObserver + Participant
    ├── drafts.py            # DraftObserver
    ├── stats.py             # PostGameObserver
    ├── objectives.py        # ObjectiveKilledObserver
    └── vision.py            # WardsObserver

tests/
├── samples/                 # JSON/JSONL de ejemplo para integración
├── test_observers.py        # integración end-to-end
├── test_observers_unit.py   # unitarios por observer
├── test_gql_mock.py         # mocks del cliente GraphQL
└── test_utils.py
```

### Configuración de API Key

Para los scripts de ejemplo y manual_test.py:

```bash
# .env en la raíz del proyecto
GRID_API_KEY=tu_clave_aquí
```

---

## Licencia

MIT. Ver `LICENSE`.

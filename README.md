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
  - [BuildObserver](#buildobserver)
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

GRID expone varias fuentes complementarias para una serie de LoL:

| Fuente | Qué contiene | Cuándo llega |
|--------|--------------|--------------|
| **GRID livestats** | Picks/bans del draft, IDs internos de GRID, eventos administrativos (invalidaciones, side rotation) | Durante y tras la partida |
| **Riot summary** | End-state oficial: ganador, KDA final, oro, daño, versión del juego | Tras el final de la partida |
| **Riot livestats** | Timeline detallado: kills, wards, objetivos, posiciones | Durante la partida (JSONL) |
| **GRID end-state** | Estado final de serie: games, rosters, draftActions, sides, versión | Tras la serie |
| **Tencent details** | End-state LPL: ganador, stats finales, runas, items, picks/bans agregados | Tras la partida |

`process_bundle` los combina en el orden correcto: GRID state → GRID game state → Tencent details → Riot summary → GRID livestats → Riot livestats.

En LPL puede no existir Riot summary. En ese caso `Tencent details` actúa como
end-state autoritativo para stats finales, runas, items y ganador. Riot summary
sigue teniendo prioridad si existe.

### El cruce PUUID

Los IDs de Riot (1–10) y los IDs internos de GRID (numéricos) se cruzan a través del PUUID que aparece en ambos lados. `TeamsObserver` mantiene un mapa `puuid → grid_player_id` y actualiza retroactivamente los `Participant` cuando llega información de cualquier fuente.

En LPL, Tencent details y GRID end-state pueden no traer PUUID. Si se procesa
Riot LiveStats y aparece el evento `game_info`, `TeamsObserver` enriquece los
participantes ya creados desde Tencent con el `puuid` de Riot.

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

# Manifest de fragments Riot LiveStats listados por GRID
manifest = client.get_riot_livestats_manifest(series_id="2923634")

# Descargar fragments Riot usando solo el manifest (útil en LPL)
fragments = client.get_riot_livestats_fragments(series_id="2923634")

# End-state Tencent para LPL
tencent = client.get_tencent_details(series_id="2923634", game_number=1)

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
print(player.puuid)                 # PUUID hex, o "" si la fuente no lo trae
print(player.tencent_player_id)     # ID Tencent, o None si no aplica

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

# Diagnóstico de calidad/fallback (útil en LPL)
draft.get_draft_status()
```

**Lógica:** si la serie se rehace solo para invertir lados (ej. ligas ERL sin tecnología de side-pick), `get_draft()` detecta que los 20 campeones coinciden exactamente con un draft anterior y devuelve el original (con la asignación de lados primera). Si cambia algún pick, se considera remake real.

En LPL, si los eventos GRID quedan incompletos por invalidaciones pero `GRID
end-state` trae las 20 `draftActions`, `get_draft()` puede usar ese draft como
fallback. La forma de salida no cambia; `get_draft_status()` indica si se usó.

### `PostGameObserver`

Recopila KDA, oro, CS, daño y determina el ganador.

```python
from grid_minion.observers import PostGameObserver

stats = PostGameObserver()
# ... tras process_bundle ...

# Necesita un TeamsObserver para enriquecer con nombres y campeones
report = stats.get_game_stats(teams_observer=teams)

report["meta"]["winner"]         # "BLUE" / "RED"
report["meta"]["winner_source"]  # "summary" | "tencent_details" | "game_end" | "gold_heuristic"
report["meta"]["version"]        # "14.1"

for pid, p in report["players"].items():
    print(f"{p['name']} ({p['champion']}): {p['kda_str']}")
    # p = {'kills', 'deaths', 'assists', 'gold', 'cs', 'damage_dealt',
    #      'kda_str', 'name', 'champion', 'champion_id', 'side', 'source',
    #      'runes', 'final_items'}
```

**`runes` y `final_items`** salen del Riot Summary. En LPL, si no hay Riot
Summary, salen de Tencent details. Si no existe ninguna de esas fuentes, son
`None`:

```python
p["final_items"]  # [3047, 3157, 6653, ...]  ← item0..item6 sin los ceros
p["runes"]        # {'primary_style': 8200, 'primary': [8229, 8275, 8233, 8237],
                  #  'sub_style': 8400, 'sub': [8473, 8242],
                  #  'stat_perks': [5008, 5008, 5011]}
```

**Jerarquía del ganador:**

1. **`summary`:** Riot Summary expone `team.win == True`.
2. **`tencent_details`:** Tencent details expone el ganador LPL cuando no hay Riot Summary.
3. **`game_end`:** evento `rfc461Schema: "game_end"` de Riot LiveStats con campo `winningTeam` (100 = BLUE, 200 = RED). Si la partida termina sana pero sin summary.
4. **`gold_heuristic`:** fallback para scrims donde nadie esperó al final. Se infiere del último `stats_update`: equipo con más oro = ganador. Marcado explícitamente como `gold_heuristic`.

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

### `BuildObserver`

Reconstruye, por jugador, la **build path** (compras/ventas en orden) y el
**skill order**, a partir de la timeline de Riot (`riot_livestats`). Sin
dependencias; se cruza con el resto por `participantId` (1-10).

```python
from grid_minion.observers import BuildObserver

builds = BuildObserver()
# ... tras process_bundle (necesita riot_livestats) ...

for pid, b in builds.get_builds().items():
    print(pid, b["skill_order"])              # "EQWQQRQEQEREEWWRWW"
    # b = {
    #   "skill_order": "QWEQ...",              # solo subidas normales (sin evoluciones)
    #   "build_path": [
    #       {"ts_s": 3,   "action": "BUY",  "item_id": 1056},
    #       {"ts_s": 430, "action": "BUY",  "item_id": 3047},
    #       {"ts_s": 803, "action": "SELL", "item_id": 2003},
    #   ],
    # }
```

`build_path` está en orden cronológico, con los `item_undo` ya resueltos. El
`skill_order` mapea `1→Q, 2→W, 3→E, 4→R` y excluye evoluciones (Kha'Zix, Viktor).

---

## GameEventProcessor

El orquestador que distribuye eventos a los observers en el orden correcto.

```python
from grid_minion.observers import GameEventProcessor

processor = GameEventProcessor()
processor.attach(observer)
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
    grid_state=None,                  # opcional: estado global de GRID
    grid_game_state=None,             # opcional: GameState concreto de GRID
    tencent_details=None,             # opcional: end-state Tencent (LPL)
    riot_summary=riot_summary,        # opcional: dict con end-state Riot
    grid_livestats=grid_events,       # opcional: lista de eventos GRID
    riot_livestats=riot_events,       # opcional: lista de eventos Riot timeline
    lpl_diagnostics=None              # opcional: diagnóstico externo
)
```

El procesador ordena las fuentes así: `GRID_STATE → GRID_GAME_STATE →
TENCENT_DETAILS → RIOT_SUMMARY → GRID livestats → Riot livestats →
LPL_DIAGNOSTICS`. Cualquier subconjunto es válido (puedes procesar solo
`riot_summary` si quieres únicamente stats finales).

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

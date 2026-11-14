# Guía — Posiciones, capturas y paquetes de scouting

Desde la 0.5.0 grid-minion puede extraer **dónde está cada jugador** a lo
largo de una partida y recortar esa información en **paquetes** listos para
analizar: nivel 1, pathing de jungla y openings tras backear.

Todo lo de esta guía es opcional. Los observers que ya existían
(`TeamsObserver`, `DraftObserver`, `PostGameObserver`...) no cambian.

- [Las tres capas](#las-tres-capas)
- [Captura de una partida](#captura-de-una-partida)
- [Paquetes](#paquetes)
  - [`level_one`](#level_one)
  - [`jungle_pathing`](#jungle_pathing)
  - [`recall_openings`](#recall_openings)
- [Geometría del mapa](#geometría-del-mapa)
- [Stats y cooldowns](#stats-y-cooldowns)
- [Observers de eventos detallados](#observers-de-eventos-detallados)
- [Lectura en streaming](#lectura-en-streaming)
- [Límites del dato](#límites-del-dato)

---

## Las tres capas

```
Riot LiveStats (~100 MB por partida)
   │  una pasada en streaming
   ▼
1. Captura     posiciones + registro de eventos (~100 KB)
   ▼
2. Paquetes    recortes de la captura: nivel 1, jungla, backs
   ▼
3. Scouting    agregar paquetes entre partidas (fuera de la librería)
```

Los paquetes se calculan sobre la **captura**, no sobre el feed. Si guardas
las capturas, un paquete nuevo se puede sacar de todo tu histórico sin volver
a descargar nada de GRID.

---

## Captura de una partida

```python
import os
from grid_minion import GridRestClient, capture_game

client = GridRestClient(api_key=os.environ["GRID_API_KEY"])

capture = capture_game(client, "2922522", game_number=1)   # None si no hay livestats
capture.save("2922522_g1.capture.json.gz")                  # ~100 KB
```

`capture_game` descarga en streaming: el fichero de ~100 MB nunca se carga
entero (pico de ~2 MB de memoria). Para reprocesar un fichero ya guardado:

```python
from grid_minion import build_capture, iter_jsonl, GameCapture

capture = build_capture(iter_jsonl("livestats.jsonl.gz"))
capture = GameCapture.load("2922522_g1.capture.json.gz")
```

### Qué contiene

| Atributo | Contenido |
|---|---|
| `capture.game` | `series_id`, `game_number`, `game_version`, `duration_s` |
| `capture.players` | `{pid: {name, champion, team_id, side, role}}`; `role` ∈ `TOP`, `JUNGLE`, `MID`, `ADC`, `SUPPORT` (viene del feed) |
| `capture.positions` | `PositionTrack`: trayectoria de cada jugador, 1 muestra por segundo |
| `capture.events` | lista ordenada de eventos en formato común |

### Consultas

```python
capture.players_by(role="JUNGLE", side="BLUE")     # [2]
capture.position_at(2, 90)                         # {'t': 89.0, 'x': 7512, 'y': 3980, 'alive': True}
capture.positions_between(2, 60, 120)              # [{t, x, y, alive}, ...]
capture.get_events("monster_kill", pid=2, t1_s=200)
capture.get_events(("ward_placed", "ward_killed"), t0_s=0, t1_s=90)
```

`position_at` devuelve la última muestra con `t <= t_s`. Las coordenadas son
unidades de mapa de Riot (0 a ~14 800), con `y` = el `z` del feed.

### Formato de los eventos

Todos comparten la forma `{t, kind, pid, side, x, y, detail}`:

| `kind` | `pid` | `detail` |
|---|---|---|
| `kill` | quien mata | `victim`, `assistants`, `bounty`, `shutdown_bounty`, `killstreak`, `fight_duration` |
| `kill_special` | quien mata | `type` (`firstBlood`, `ace`, `multi`) |
| `ward_placed` / `ward_killed` | quien lo pone / lo quita | `ward_type` |
| `monster_kill` | quien lo mata | `monster`, `dragon_type`, `assistants`, `in_enemy_jungle`, `kill_type` |
| `monster_spawn` | — | `monster`, `dragon_type`, `camp_side` |
| `building_destroyed` | último golpe | `building_type`, `lane`, `tier`, `owner_side`, `assistants` |
| `plate_destroyed` | último golpe | `lane`, `owner_side`, `assistants` |
| `channel_start` / `channel_end` | quien canaliza | `type` (`recall`, `summonerSpell`, `skill`), `spell`, `interrupted` (solo en el `end`) |
| `summoner_used` | quien lo usa | `spell`, `slot` |
| `item_purchased` / `item_sold` / `item_destroyed` / `item_undo` | el jugador | `item_id` (+ `gold_gain`, `reason`) |
| `level_up` / `skill_level_up` | el jugador | `level` / `slot`, `evolved` |
| `pause_start` / `pause_end` / `reconnect` / `quit` | — / el jugador | — |
| `game_end` | — | `winning_side` |

`monster` va en minúsculas (`redcamp`, `bluecamp`, `krug`, `raptor`, `wolf`,
`gromp`, `scuttlecrab`, `dragon`, `baron`, `riftherald`, `voidgrub`): el feed
usa mayúsculas distintas al aparecer y al morir. `JUNGLE_CAMPS` lista los
campamentos de jungla.

---

## Paquetes

Funciones puras `GameCapture -> dict`, serializables a JSON y con su propia
versión de esquema (`grid-minion/package/<nombre>/v1`).

```python
from grid_minion.packages import level_one, jungle_pathing, recall_openings
```

### `level_one`

Posicionamiento de los 10 jugadores desde 0:00 hasta 1:30.

```python
package = level_one(capture, until_s=90, marks_s=(30, 60, 85))

package["players"]["2"]["marks"]["60"]   # {'x': ..., 'y': ..., 'zone': 'jungle_blue_bot'}
package["players"]["2"]["path"]          # [{t, x, y, alive, zone}, ...]
package["wards"], package["kills"], package["summoners"]
package["invades"]                       # [{pid, side, role, champion, zone, t_enter, t_exit, duration, deepest}]
```

`invades` son tramos de al menos 3 s en jungla o base del equipo contrario.

### `jungle_pathing`

Recorrido de cada jungla cruzado, segundo a segundo, con los eventos.

```python
package = jungle_pathing(capture)                 # hasta su primer recall (o muerte)
package = jungle_pathing(capture, until=900)      # hasta el minuto 15

blue = package["junglers"][0]
blue["first_clear"]     # ['redcamp', 'krug', 'raptor', 'wolf', 'bluecamp', 'gromp', 'scuttlecrab']
blue["start_side"]      # 'bot'
blue["first_recall"]    # {'t_start': 200.6, 't_end': 208.7, 'level': 4}
```

Por cada jungla:

| Campo | Contenido |
|---|---|
| `window` | `{t0, t1, reason}`; `reason` ∈ `first_recall`, `first_death`, `max_s`, `fixed`, `game_end` |
| `camps` | `[{order, t, monster, x, y, zone, in_enemy_jungle}]` |
| `path` | `[{t, x, y, alive, zone, enemy_jungler_d, nearest_enemy_pid, nearest_enemy_d}]` |
| `timeline` | eventos en orden, cada uno con `relation` y `jungler_pos` |
| `levels`, `first_recall`, `river_crossings` | niveles, primer recall y entradas al río |
| `ganks` | candidatos a gank (ver abajo) |
| `invades` | candidatos a invade (ver abajo) |

`timeline` cruza eventos y posición: cada entrada lleva dónde estaba el
jungla en ese instante. `relation` es `self` (lo hace él), `victim` /
`assist` (kills en las que participa) o `enemy_ward_nearby` (un rival pone un
ward cerca de él).

**Ganks e invades son candidatos, no veredictos.** Cada uno lleva los datos
que lo justifican, y los umbrales son parámetros:

```python
gank = blue["ganks"][0]
# {'lane': 'mid', 't_enter': 841.0, 't_exit': 868.0, 'duration': 27.0,
#  'in_lane_s': 9, 'near_enemy_s': 28,
#  'closest': {'pid': 9, 'role': 'ADC', 'champion': 'Kalista', 'distance': 88, 't': ...},
#  'result': 'kill', 'kills': [...], 'assists': [...], 'deaths': [...],
#  'enemy_summoners': [{'t': ..., 'pid': 9, 'spell': 'SummonerFlash', 'distance': ...}]}
```

- Un **gank** es un tramo en el que el jungla está a menos de `gank_radius`
  (1500) de un rival de línea durante `min_near_s` (2 s), o dentro de una
  línea durante `min_lane_s` (6 s) con un rival a menos de
  `lane_enemy_radius` (3500). `result` ∈ `kill`, `assist`, `death`,
  `summoner_burned`, `none`.
- Un **invade** es un tramo de al menos `min_invade_s` (3 s) en jungla
  enemiga. Lleva `zone`, `camps_taken`, `wards_placed`,
  `min_enemy_jungler_d` y el mismo `result`.

### `recall_openings`

Qué hace cada jugador al volver de base.

```python
package = recall_openings(capture, window_s=60, until_s=600)

package["recalls"][0]
# {'pid': 2, 'side': 'BLUE', 'role': 'JUNGLE', 'champion': 'Zaahen',
#  't_start': 200.6, 't_end': 208.7, 'level': 4, 'gold_at_recall': None,
#  'from': {'x': ..., 'y': ..., 'zone': 'river_top'},
#  'items_bought': [1028, 1036, 2055],
#  't_leave_base': 217.0, 'shop_s': 8.3,
#  'path': [...], 'zones': [{'zone': ..., 't_enter': ...}],
#  'destination': {'x': ..., 'y': ..., 'zone': 'jungle_blue_top', 't': ...},
#  'travel_s': 57.0, 'teleport': None, 'died': False}
```

Solo cuenta los recalls completados. `gold_at_recall` se rellena si la
captura incluye la sección `stats` (ver más abajo).

---

## Geometría del mapa

`grid_minion.spatial` son funciones puras sobre coordenadas:

```python
from grid_minion import spatial

spatial.zone_of(7800, 4000)                 # 'jungle_blue_bot'
spatial.is_enemy_jungle("jungle_red_top", "BLUE")   # True
spatial.lane_of("lane_mid")                 # 'mid'
spatial.normalize(7375, 7430)               # (0.5, 0.5)
spatial.to_blue_perspective(x, y, side)     # gira 180° las posiciones del lado rojo
spatial.distance(a, b)
```

Zonas: `base_blue`, `base_red`, `lane_top`, `lane_mid`, `lane_bot`,
`river_top`, `river_bot`, `jungle_blue_top`, `jungle_blue_bot`,
`jungle_red_top`, `jungle_red_bot`. En las de jungla, `blue`/`red` es el
equipo dueño y `top`/`bot` el lado respecto a la línea central.

Son una aproximación por rectas, calibrada con partidas reales: los
campamentos caen en su cuadrante y coinciden con el indicador
`inEnemyJungle` del feed en más del 95 % de los casos.

`to_blue_perspective` sirve para comparar a un equipo entre partidas en
lados distintos: tras el giro, su jungla y su base caen siempre en el mismo
sitio. Las líneas laterales se intercambian (la superior pasa a la inferior).

---

## Stats y cooldowns

Van en observers separados de las posiciones, y se piden aparte:

```python
capture = capture_game(client, "2922522", 1,
                       include=("positions", "events", "stats", "cooldowns"))

capture.stats.at(2, 840)             # {'t', 'gold_total', 'level', 'cs', 'ad', 'hp_max', ...}
capture.stats.delta(2, "damage_to_champions", 780, 900)   # daño hecho en ese tramo
capture.stats.team_at(100, 840)      # {'gold', 'kills', 'towers', 'dragons', ...}
capture.stats.items_at(2, 840)       # inventario en ese instante
capture.cooldowns.value_at(2, "ultimate", 840)            # segundos restantes (0 = disponible)
capture.cooldowns.meta["spell_names"]["2"]                # nombres de summoners y definitiva
```

`stats_groups` elige qué guardar: `economy`, `champion_stats`,
`combat_totals` (contadores acumulados) y `teams`. `sample_every_ms` reduce
la frecuencia de las tres secciones a la vez.

Los tres observers (`PositionObserver`, `StatsTimelineObserver`,
`CooldownsObserver`) comparten reloj: la muestra `i` de cada uno es del mismo
instante, así que se cruzan sin interpolar.

Tamaño de una partida de 32 minutos, comprimida:

| Secciones | Tamaño |
|---|---|
| Posiciones + eventos (por defecto) | ~106 KB |
| + stats completas + cooldowns | ~430 KB |
| Todo, 1 muestra cada 5 s | ~200 KB |

También se pueden enganchar a mano, como cualquier observer:

```python
from grid_minion.observers import (
    GameEventProcessor, PositionObserver, StatsTimelineObserver, CooldownsObserver,
)

processor = GameEventProcessor()
positions = PositionObserver()
stats = StatsTimelineObserver(groups=("economy", "teams"))
processor.attach(positions)
processor.attach(stats)
processor.process_events(client.iter_riot_livestats("2922522", 1))

positions.get_track().at(2, 90)
stats.get_timeline().at(2, 90)
```

---

## Observers de eventos detallados

Para quien necesite más detalle que el registro común de eventos:

| Observer | Qué da |
|---|---|
| `CombatObserver` | kills con asistentes, botín y desglose de daño; eventos especiales; KDA en el tiempo |
| `WardEventsObserver` | colocación y destrucción de wards |
| `BuildingObserver` | torres, inhibidores, nexo, placas y reapariciones |
| `ObjectiveSpawnObserver` | spawns de dragón y barón, tipo de grieta, próximo dragón |
| `MobilityObserver` | summoners, canalizaciones (recall, TP), habilidades y activas de objeto |

`CombatObserver`, `WardEventsObserver` y `BuildingObserver` reciben un
`TeamsObserver` para resolver nombres y lados; engánchalo antes.

---

## Lectura en streaming

```python
events = client.iter_riot_livestats("2922522", 1)          # iterador, o None si no existe
client.download_riot_livestats("2922522", 1, "g1.jsonl.gz")  # fichero crudo a disco

from grid_minion import iter_jsonl
for event in iter_jsonl("g1.jsonl.gz"):
    ...
```

`process_bundle(riot_livestats=...)` y `process_events(...)` aceptan estos
iteradores. `get_riot_livestats()` sigue devolviendo una lista, como siempre.

Un observer puede declarar los esquemas que necesita
(`schemas = frozenset({"stats_update"})`): solo recibe esos eventos, y
`processor.wanted_schemas()` permite descartar el resto sin parsearlo
(`iter_riot_livestats(..., schemas=...)`).

---

## Límites del dato

- **Una posición por segundo.** Es la resolución del feed; no hay más.
  Sirve para pathing, rotaciones y openings. Un flash o un dash se ve como un
  salto: `MobilityObserver` dice qué se usó, pero no dónde.
- **Jugador muerto**: conserva la posición donde murió, con `alive = False`.
- **LPL**: si el livestats llega en fragmentos, agrúpalos antes con
  `group_riot_livestats_fragments`. Sin livestats de Riot no hay posiciones.
- **Redrafts de ERL**: no afectan. La posición depende del lado en partida,
  no de quién tuvo el First Pick.

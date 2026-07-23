# Guía — Observadores de timeline granular

Esta guía cubre los **cinco observadores nuevos** que extraen telemetría detallada
(tipo replay) de una partida de LoL a partir del **Riot LiveStats (RFC461)** que sirve
GRID:

| Observer | Qué extrae | Dependencias |
|---|---|---|
| [`PlayerTimelineObserver`](#playertimelineobserver) | Estado por jugador a máx. frecuencia: posición, oro, nivel, XP, CS, items, **stats de campeón** y **cooldowns** (ultimate/habilidades/summoners) | ninguna |
| [`CombatObserver`](#combatobserver) | Muertes con asistentes, posición, botín y **desglose de daño** | `TeamsObserver` |
| [`WardEventsObserver`](#wardeventsobserver) | Colocación **y destrucción** de wards | `TeamsObserver` |
| [`BuildingObserver`](#buildingobserver) | Torres, inhibidores, nexo y placas | `TeamsObserver` (opcional) |
| [`ObjectiveSpawnObserver`](#objectivespawnobserver) | Spawns de dragón/barón → **tipo de grieta** y **tipo de Nashor** | ninguna |

> Referencia de API resumida: ver también las secciones de cada observer en el
> [README](README.md). Esta guía es el manual de uso completo, con el patrón correcto,
> las tablas de campos de salida y los *gotchas* del feed.

---

## 1. El patrón correcto: una descarga, muchos observers

Los observers **no hacen ninguna petición de red**. Solo implementan `notify_event(event)`
y parsean un evento (un `dict`) que ya está en memoria. El flujo es siempre:

```python
import os
from dotenv import load_dotenv
from grid_minion import GridRestClient
from grid_minion.observers import (
    GameEventProcessor, TeamsObserver,
    PlayerTimelineObserver, CombatObserver, WardEventsObserver,
    BuildingObserver, ObjectiveSpawnObserver,
)

load_dotenv()
client = GridRestClient(api_key=os.getenv("GRID_API_KEY"))
series_id = "2930129"
game_num = 1

# --- 1 SOLA descarga del timeline de la partida (queda en memoria) ---
riot_events = client.get_riot_livestats(series_id, game_number=game_num)
riot_summary = client.get_riot_summary(series_id, game_number=game_num)  # opcional

# --- Instanciar observers y engancharlos ---
processor = GameEventProcessor()
teams = TeamsObserver()
timeline = PlayerTimelineObserver()
combat = CombatObserver(teams_observer=teams)
wards = WardEventsObserver(teams_observer=teams)
buildings = BuildingObserver(teams_observer=teams)
spawns = ObjectiveSpawnObserver()

processor.attach(teams)       # sin dependencias  → PRIMERO
processor.attach(timeline)
processor.attach(combat)      # depende de teams  → DESPUÉS
processor.attach(wards)
processor.attach(buildings)
processor.attach(spawns)

# --- Una única pasada reparte la MISMA lista en memoria a todos ---
processor.process_bundle(riot_summary=riot_summary, riot_livestats=riot_events)

# --- Leer resultados por los getters ---
print(timeline.get_positions(1)[:3])
print(spawns.get_rift_type())
```

**Regla de oro:** descarga el `riot_livestats` **una vez** por partida y reparte esa
lista a todos los observers que quieras. Enganchar 5 o 20 observers **no** añade
descargas. Un ejemplo runnable completo está en
[`examples/process_timeline.py`](examples/process_timeline.py).

### Orden de `attach()`

Los observers que reciben `TeamsObserver` (`CombatObserver`, `WardEventsObserver`,
`BuildingObserver`) deben engancharse **después** de `teams` para que los nombres/lados
estén resueltos cuando procesen sus eventos. `PlayerTimelineObserver` y
`ObjectiveSpawnObserver` no dependen de nadie.

---

## PlayerTimelineObserver

Reconstruye el **estado continuo por jugador a máxima frecuencia** desde el evento
`stats_update` (llega ~cada 0.5 s con los 10 jugadores). Un único observer lee ese evento
una vez y guarda por `participantID` una serie de snapshots; los getters proyectan vistas.

```python
timeline = PlayerTimelineObserver()   # sin dependencias
# ... process_bundle ...

timeline.get_players()               # [1, 2, ..., 10]
timeline.get_positions(1)            # [{'t': 12.0, 'x': 1200, 'y': 3400}, ...]
timeline.get_all_positions()         # {pid: [{'t','x','y'}]}
timeline.get_economy(1)              # [{'t','gold_total','gold_current','xp','level','cs','items'}]
timeline.get_champion_stats(1)       # [{'t','ad','ap','armor','mr','attack_speed','hp_max', ...}]
timeline.get_ability_availability(1) # [{'t','ultimate','abilities':{1,2,3,4},'summoner1','summoner2'}]
timeline.get_team_gold_series()      # {100: [{'t','gold'}], 200: [...]}

# Consulta puntual: último snapshot con t <= t_s
snap = timeline.snapshot_at(1, 600)

# Disponibilidad DIRECTA (cooldownRemaining == 0). Devuelve True/False/None.
timeline.is_ultimate_up(1, 600)
timeline.is_summoner_up(1, slot=1, t_s=600)
```

**Campos de cada snapshot** (`get_series(pid)` devuelve la lista completa):

| Campo | Tipo | Notas |
|---|---|---|
| `t` | float (s) | instante del snapshot |
| `position` | `{x, y}` o `None` | `y` mapea el `z` de Riot |
| `alive` | bool | |
| `respawn_timer` | float | segundos hasta reaparecer |
| `gold_total` / `gold_current` | int | total ganado / disponible |
| `xp`, `level`, `cs` | int | CS incluye jungla |
| `items` | list | inventario en ese instante |
| `champion_stats` | dict | `ad, ap, armor, mr, attack_speed, hp, hp_max, hp_regen, armor_pen, armor_pen_pct, magic_pen, magic_pen_pct, lifesteal, spell_vamp, cdr, cc_reduction` |
| `cooldowns` | dict | `ultimate`, `abilities:{1,2,3,4}`, `summoner1`, `summoner2` (segundos restantes) |

> **Power spikes:** las stats de combate vienen **ya sumadas** con items y niveles; no hay
> que calcular nada. La disponibilidad de ultimate/summoners es señal **directa** del feed
> (no se estima por CDR), así que cubre resets y reducciones condicionales.

---

## CombatObserver

Timeline de combate desde `champion_kill` y `champion_kill_special`.

```python
combat = CombatObserver(teams_observer=teams)   # inyección obligatoria
# ... process_bundle ...

combat.get_kills()            # todas las muertes con contexto
combat.get_special_events()   # firstBlood / ace / multi
combat.get_kda_timeline()     # {pid: [{'time','kills','deaths','assists'}]} acumulado
```

**Campos de cada kill (`get_kills()`):**

| Campo | Notas |
|---|---|
| `time` | segundos |
| `killer` / `killer_id` / `killer_side` | nombre, id 1-10, `BLUE`/`RED` |
| `victim` / `victim_id` / `victim_side` | |
| `assistants` | `[{'id','name','side'}]` |
| `position` | `{x, y}` |
| `fight_duration` | duración de la pelea (s) |
| `killstreak`, `shutdown_bounty`, `bounty` | |
| `damage_breakdown` | `[{'source','caster_id','breakdown'}]` desde `deathRecap` |

Los eventos especiales (`get_special_events()`) traen `time`, `type`
(`firstBlood`/`ace`/`multi`), `killer` (+id/lado), `killstreak` y `position`.

> Diferencia con `SoloKillObserver`: aquel solo aísla las muertes 1v1; `CombatObserver`
> registra **todas** las muertes con su contexto completo.

---

## WardEventsObserver

Ciclo de vida de la visión: colocación (`ward_placed`) **y** destrucción (`ward_killed`).
Es independiente del `WardsObserver` clásico (que solo registra colocación).

```python
wards = WardEventsObserver(teams_observer=teams)   # inyección obligatoria
# ... process_bundle ...

wards.get_placements()   # [{'time','player','player_id','team','type','position'}]
wards.get_kills()        # [{'time','killer','killer_id','team','type','position'}]
wards.get_events()       # colocaciones + destrucciones fusionadas y ordenadas por tiempo
```

`type` ∈ `{sight, control, blueTrinket, yellowTrinket, unknown}`. En `get_events()` cada
entrada añade `action` ∈ `{placed, killed}`.

---

## BuildingObserver

Estructuras destruidas y placas.

```python
buildings = BuildingObserver(teams_observer=teams)   # teams_observer opcional
# ... process_bundle ...

buildings.get_buildings()    # todas (torres + inhibidores + nexo)
buildings.get_turrets()      # solo torres
buildings.get_inhibitors()   # solo inhibidores
buildings.get_plates()       # placas de torreta
buildings.get_respawns()     # reapariciones de inhibidor
```

**Campos de cada estructura:**

| Campo | Notas |
|---|---|
| `time` | segundos |
| `building_type` | `turret` / `inhibitor` / `nexus` |
| `lane` | `top` / `mid` / `bot` |
| `turret_tier` | `outer` / `inner` / `base` / `nexus` |
| `owner_team` | equipo **dueño** de la estructura (`BLUE`/`RED`) |
| `killed_by_team` | el equipo contrario (quien la derribó) |
| `last_hitter` / `last_hitter_id` | requiere `teams_observer` para el nombre |
| `assistants` | lista de ids |
| `bounty_gold`, `position` | |

> **Ojo:** `teamID` en el feed es el equipo **dueño** de la estructura; por eso se expone
> `killed_by_team` ya calculado (el contrario).

---

## ObjectiveSpawnObserver

Rastrea la **aparición** (spawn) de objetivos para derivar el **tipo de grieta elemental**
y el **tipo de Nashor**, sin depender de los kills.

```python
spawns = ObjectiveSpawnObserver()   # sin dependencias
# ... process_bundle ...

spawns.get_rift_type()      # {'type': 'fire', 'time': 996.8}  → 3.er dragón elemental
spawns.get_nashor_type()    # {'type': 'Baron', 'time': 1180.0}
spawns.get_dragon_spawns()  # [{'time','dragon_type'}, ...] en orden de aparición
spawns.get_queued_dragon()  # {'time','next_dragon_name','next_spawn_time'} (próximo)
spawns.get_baron_spawns()   # apariciones del barón
spawns.get_herald_spawns()  # apariciones del heraldo
spawns.get_kills()          # [{'time','monster_type','dragon_type','kill_type','killer_id','team'}]
```

**Reglas de derivación:**

- **Tipo de grieta** = elemento (`dragon_type`) del **3.er dragón elemental que spawnea**
  (los `elder` se excluyen). Devuelve `None` hasta que hay 3 dragones elementales.
  `dragon_type` ∈ `{air, chemtech, earth, fire, hextech, water}`.
- **Tipo de Nashor** = tipo del **primer spawn real** del barón. En el parche actual el
  feed expone el barón **siempre como `"Baron"`** (sin variantes; los dragones sí llevan
  elemento, el barón no). El getter queda preparado para exponer variantes vía
  `monsterName` si un parche futuro las añade.

> No sustituye al `ObjectiveKilledObserver` (que cuenta *kills*): se solapan a propósito,
> uno mira spawns y el otro muertes.

---

## Gotchas del feed (importantes)

- **Unidades:** en los eventos de Riot, `gameTime` viene en **milisegundos**, pero
  `spawnTime` / `nextDragonSpawnTime` vienen en **segundos**. Los observers ya lo
  normalizan: **todos los tiempos de salida están en segundos.**
- **Coordenadas:** el feed usa `position.{x, z}`. Los observers mapean `z → y`, así que
  la salida es siempre `{x, y}`.
- **Tamaño de los ficheros:** el `riot_livestats` de una partida pesa **~149 MB**. Descarga
  cada partida **una sola vez** y reparte la lista en memoria; no re-descargues para iterar.
- **Casing del id de jugador:** el feed mezcla `participantID`, `participant` y
  `participantId` según el evento. Los observers normalizan las tres variantes.
- **`.env` en scripts fuera del repo:** `python-dotenv`'s `load_dotenv()` busca el `.env`
  desde el directorio del **archivo**, no del CWD. Si tu script vive fuera del repo, pásale
  la ruta: `load_dotenv("/ruta/al/proyecto/.env")` (o `load_dotenv(find_dotenv(usecwd=True))`).

---

## Ejemplo completo

Ver [`examples/process_timeline.py`](examples/process_timeline.py) para un script runnable
que procesa las 5 partidas de una serie y vuelca posiciones, economía/stats, disponibilidad
de ultimate, combate, visión, estructuras y tipo de grieta/Nashor.

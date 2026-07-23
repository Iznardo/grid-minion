import logging
from typing import Dict, Any, List, Optional
from .base import Observer

logger = logging.getLogger(__name__)


class ObjectiveSpawnObserver(Observer):
    """
    Observador de **aparición de objetivos** (spawns), pensado para derivar el
    **tipo de grieta elemental** y el **tipo de Nashor** sin depender de los kills.

    Reglas operativas (definidas con el usuario):
      - **Tipo de grieta** = elemento (`dragonType`) del **3.er dragón que spawnea**
        en la partida. El terreno se considera indefinido hasta que hay 3 dragones.
        Los dragones `elder` se excluyen del cómputo (no transforman el terreno).
      - **Tipo de Nashor** = tipo del spawn del barón.

    Fuentes RFC461 verificadas contra el feed real (serie `2930129`):
      - `epic_monster_spawn`: `monsterType`=dragon, `dragonType` ∈ {air, chemtech,
        earth, elder, fire, hextech, water}, `gameTime`.
      - `queued_dragon_info`: `nextDragonName`, `nextDragonSpawnTime` (próximo dragón).
      - `queued_epic_monster_info`: `monsterName`, `spawnTime`, `position` (barón/herald
        en cola, con su tipo por nombre).
      - `neutral_minion_spawn`: `monsterType` (incl. `Baron`, `RiftHerald`, `VoidGrub`,
        campamentos), `teamSide`, `position`.
      - `epic_monster_kill`: `monsterType`, `dragonType`, `killType` ∈ {kill, steal},
        `killer`, `killerTeamID` (kills, en paralelo al `ObjectiveKilledObserver`).

    No toca el `ObjectiveKilledObserver` existente; se solapan a propósito.
    """

    _SIDE = {100: "BLUE", 200: "RED"}

    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        self.dragon_spawns: List[Dict[str, Any]] = []
        self.queued_dragons: List[Dict[str, Any]] = []
        self.queued_epics: List[Dict[str, Any]] = []
        self.baron_spawns: List[Dict[str, Any]] = []
        self.herald_spawns: List[Dict[str, Any]] = []
        self.kills: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa los eventos de spawn/queue/kill de objetivos."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        try:
            if schema == "epic_monster_spawn":
                self._process_epic_spawn(event)
            elif schema == "queued_dragon_info":
                self._process_queued_dragon(event)
            elif schema == "queued_epic_monster_info":
                self._process_queued_epic(event)
            elif schema == "neutral_minion_spawn":
                self._process_neutral_spawn(event)
            elif schema == "epic_monster_kill":
                self._process_kill(event)
        except Exception:
            logger.warning("Error procesando spawn de objetivo (%s)", schema, exc_info=True)

    def _process_epic_spawn(self, event: Dict[str, Any]):
        monster = str(event.get("monsterType", "")).lower()
        if "dragon" in monster:
            self.dragon_spawns.append({
                "time": event.get("gameTime", 0) / 1000,
                "dragon_type": event.get("dragonType"),
            })

    def _process_queued_dragon(self, event: Dict[str, Any]):
        # OJO: en el feed real `gameTime` viene en ms pero `nextDragonSpawnTime` en
        # SEGUNDOS (p.ej. 300 = 5:00). No dividir el spawn entre 1000.
        spawn_s = event.get("nextDragonSpawnTime")
        self.queued_dragons.append({
            "time": event.get("gameTime", 0) / 1000,
            "next_dragon_name": event.get("nextDragonName"),
            "next_spawn_time": float(spawn_s) if isinstance(spawn_s, (int, float)) else None,
        })

    def _process_queued_epic(self, event: Dict[str, Any]):
        # Igual que en queued_dragon: `spawnTime` viene en SEGUNDOS (1200 = 20:00),
        # mientras `gameTime` está en ms.
        spawn_s = event.get("spawnTime")
        self.queued_epics.append({
            "time": event.get("gameTime", 0) / 1000,
            "monster_name": event.get("monsterName"),
            "spawn_time": float(spawn_s) if isinstance(spawn_s, (int, float)) else None,
            "position": self._position(event),
        })

    def _process_neutral_spawn(self, event: Dict[str, Any]):
        monster = str(event.get("monsterType", ""))
        low = monster.lower()
        record = {
            "time": event.get("gameTime", 0) / 1000,
            "monster_type": monster,
            "team_side": event.get("teamSide"),
            "position": self._position(event),
        }
        if "baron" in low:
            self.baron_spawns.append(record)
        elif "herald" in low:
            self.herald_spawns.append(record)

    def _process_kill(self, event: Dict[str, Any]):
        team_id = event.get("killerTeamID")
        self.kills.append({
            "time": event.get("gameTime", 0) / 1000,
            "monster_type": event.get("monsterType"),
            "dragon_type": event.get("dragonType"),
            "kill_type": event.get("killType"),
            "killer_id": event.get("killer"),
            "team": self._side_from_team_id(team_id),
        })

    # ---------- derivaciones ----------

    def get_rift_type(self) -> Optional[Dict[str, Any]]:
        """Tipo de grieta = `dragonType` del 3.er dragón (no-elder) que spawnea.

        Devuelve `{"type": <elemento>, "time": <s>}` o `None` si aún no han
        aparecido 3 dragones elementales.
        """
        elemental = [
            d for d in self.dragon_spawns
            if d.get("dragon_type") and str(d["dragon_type"]).lower() != "elder"
        ]
        if len(elemental) < 3:
            return None
        third = elemental[2]
        return {"type": third["dragon_type"], "time": third["time"]}

    def get_nashor_type(self) -> Optional[Dict[str, Any]]:
        """Tipo de Nashor y su primer spawn real.

        **Nota sobre el feed:** en el parche actual el barón se expone SIEMPRE como
        `"Baron"`, sin variantes (los dragones sí llevan elemento, el barón no). Este
        getter se mantiene por completitud y preparado para un futuro parche que
        exponga variantes vía `monsterName`.

        `time` es el instante del **primer spawn real** del barón
        (`neutral_minion_spawn`), no el placeholder de pre-partida. Cae al spawn
        programado del `queued_epic_monster_info` si no se vio el spawn real. `None`
        si el barón no ha aparecido ni está en cola.
        """
        # Nombre más específico que el genérico "Baron", si un parche lo expusiera.
        specific = next(
            (q["monster_name"] for q in self.queued_epics
             if q.get("monster_name")
             and "baron" in str(q["monster_name"]).lower()
             and str(q["monster_name"]).lower() != "baron"),
            None,
        )
        if self.baron_spawns:
            first = self.baron_spawns[0]
            return {"type": specific or first["monster_type"], "time": first["time"]}
        queued_baron = next(
            (q for q in self.queued_epics
             if q.get("monster_name") and "baron" in str(q["monster_name"]).lower()),
            None,
        )
        if queued_baron:
            return {"type": specific or queued_baron["monster_name"],
                    "time": queued_baron["spawn_time"]}
        return None

    def get_dragon_spawns(self) -> List[Dict[str, Any]]:
        """Secuencia de dragones que han aparecido, con tipo e instante."""
        return self.dragon_spawns

    def get_queued_dragon(self) -> Optional[Dict[str, Any]]:
        """Última información de dragón en cola (el próximo en aparecer)."""
        return self.queued_dragons[-1] if self.queued_dragons else None

    def get_baron_spawns(self) -> List[Dict[str, Any]]:
        """Apariciones del barón."""
        return self.baron_spawns

    def get_herald_spawns(self) -> List[Dict[str, Any]]:
        """Apariciones del heraldo."""
        return self.herald_spawns

    def get_kills(self) -> List[Dict[str, Any]]:
        """Kills de objetivos épicos (kill/steal), en paralelo al ObjectiveKilledObserver."""
        return self.kills

    # ---------- helpers ----------

    @staticmethod
    def _position(event: Dict[str, Any]) -> Dict[str, Any]:
        raw = event.get("position", {}) or {}
        x = raw.get("x")
        y = raw.get("z") if "z" in raw else raw.get("y")
        return {"x": x, "y": y}

    def _side_from_team_id(self, team_id: Any) -> str:
        try:
            return self._SIDE.get(int(team_id), "NEUTRAL") if team_id is not None else "NEUTRAL"
        except (TypeError, ValueError):
            return "NEUTRAL"

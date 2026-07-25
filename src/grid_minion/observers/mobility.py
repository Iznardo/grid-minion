import logging
from typing import Dict, Any, List, Optional
from .base import Observer
from .teams import TeamsObserver

logger = logging.getLogger(__name__)


class MobilityObserver(Observer):
    """
    Observador de **desplazamiento no-caminado**: los eventos con los que un
    campeon cambia de sitio (o de disponibilidad para hacerlo) sin recorrer la
    distancia a pie.

    Existe porque la posicion por si sola no distingue "corrio" de "flasheo",
    "hizo recall" o "uso un dash": a 1 Hz, un recall y un TP se ven igual que una
    velocidad imposible. Estos eventos son la unica forma de etiquetar esos saltos
    con lo que realmente paso.

    Nomenclatura RFC461 verificada contra el feed real de GRID (serie `2930129`,
    partida 2; volumenes observados entre parentesis):
      - `summoner_spell_used` (131): `participantID`, `summonerSpellName`
        (`"SummonerFlash"`, `"SummonerTeleport"`, `"SummonerSmite"`...),
        `summonerSpellSlot`, `maxCooldown`, `chargesRemaining`, `maxCharges`.
      - `channeling_started` / `channeling_ended` (205 / 206): `channelingType`
        (`"recall"` 166, `"summonerSpell"` 33 = los TP, `"skill"` 6/7),
        `isInterrupted` (solo en el `ended`; 53 de 206 interrumpidas),
        `skillSlot`, `summonerSpellName`, `inventorySlot`.
      - `skill_used` (3851): `participant`, `skillSlot`, `maxCooldown`,
        `chargesRemaining`, `maxCharges`.
      - `item_active_ability_used` (346): `participantID`, `itemID`,
        `inventorySlot`, `maxCooldown`.

    Ojo (mismo gotcha que `BuildObserver`): el id de jugador llega con tres
    casings distintos segun el evento (`participantID` en summoners/items,
    `participant` en skills); se normalizan todos.

    **Gotcha verificado con datos reales**: `summonerSpellName` NO es constante
    durante la partida — cambia cuando el hechizo se mejora. En 2930129_g2 se
    observan `SummonerTeleport` y `S12_SummonerTeleportUpgrade` para el mismo
    slot del mismo jugador, y hasta cuatro nombres de Smite
    (`SummonerSmite`, `S5_SummonerSmitePlayerGanker`,
    `SummonerSmiteAvatarUtility`, `SummonerSmiteAvatarOffensive`). El nombre se
    expone **crudo**: agrupar variantes en una "familia" de hechizo es
    derivacion y vive en el consumidor. Comparar strings exactos contra
    `"SummonerTeleport"` es un bug esperando a pasar.

    **No empareja** `channeling_started` con `channeling_ended`: se exponen las
    dos listas crudas, igual que `WardEventsObserver` expone `placed` y `killed`
    sin emparejar. Emparejar es derivacion y vive en el consumidor.

    Unidades: `time` en segundos (el feed da `gameTime` en ms). Los cooldowns se
    exponen **crudos, en milisegundos** y con el nombre `*_ms`: el feed los da en
    ms para los summoners (Smite = 15000), pero no se ha verificado que la escala
    sea la misma en todos los esquemas, y no se convierte lo que no se ha
    comprobado.
    """

    def __init__(self, teams_observer: Optional[TeamsObserver] = None):
        """
        Args:
            teams_observer (Optional[TeamsObserver]): Si se pasa, cada registro se
                enriquece con el nombre y el lado del jugador. Sin el, solo el id.
        """
        self.teams_observer = teams_observer
        self.reset()

    def reset(self):
        """Reinicia las listas registradas."""
        self.summoner_spell_uses: List[Dict[str, Any]] = []
        self.channeling_starts: List[Dict[str, Any]] = []
        self.channeling_ends: List[Dict[str, Any]] = []
        self.skill_uses: List[Dict[str, Any]] = []
        self.item_actives: List[Dict[str, Any]] = []

    def notify_event(self, event: Dict[str, Any]):
        """Procesa los cinco esquemas de movilidad."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "summoner_spell_used":
            self._record(self.summoner_spell_uses, event, self._summoner_spell_fields)
        elif schema == "channeling_started":
            self._record(self.channeling_starts, event, self._channeling_fields)
        elif schema == "channeling_ended":
            self._record(self.channeling_ends, event, self._channeling_end_fields)
        elif schema == "skill_used":
            self._record(self.skill_uses, event, self._skill_fields)
        elif schema == "item_active_ability_used":
            self._record(self.item_actives, event, self._item_active_fields)

    def _record(self, bucket: List[Dict[str, Any]], event: Dict[str, Any], fields):
        try:
            pid = self._pid(event)
            bucket.append({
                "time": event.get("gameTime", 0) / 1000,
                "player": self._name(pid),
                "player_id": pid,
                "team": self._side(pid),
                **fields(event),
            })
        except Exception:
            schema = event.get("rfc461Schema") or event.get("eventType")
            logger.warning("Error procesando %s en MobilityObserver", schema, exc_info=True)

    # ---------- extractores por esquema ----------

    @staticmethod
    def _summoner_spell_fields(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "spell_name": event.get("summonerSpellName"),
            "spell_slot": event.get("summonerSpellSlot"),
            "max_cooldown_ms": event.get("maxCooldown"),
            "charges_remaining": event.get("chargesRemaining"),
            "max_charges": event.get("maxCharges"),
        }

    @staticmethod
    def _channeling_fields(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "channeling_type": event.get("channelingType"),
            "spell_name": event.get("summonerSpellName"),
            "spell_slot": event.get("summonerSpellSlot"),
            "skill_slot": event.get("skillSlot"),
            "inventory_slot": event.get("inventorySlot"),
        }

    @classmethod
    def _channeling_end_fields(cls, event: Dict[str, Any]) -> Dict[str, Any]:
        # `isInterrupted` solo aparece en el `ended`; se preserva tal cual (puede
        # ser None si el feed no lo trae en algun evento).
        return {**cls._channeling_fields(event), "interrupted": event.get("isInterrupted")}

    @staticmethod
    def _skill_fields(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "skill_slot": event.get("skillSlot"),
            "max_cooldown_ms": event.get("maxCooldown"),
            "charges_remaining": event.get("chargesRemaining"),
            "max_charges": event.get("maxCharges"),
        }

    @staticmethod
    def _item_active_fields(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "item_id": event.get("itemID"),
            "inventory_slot": event.get("inventorySlot"),
            "max_cooldown_ms": event.get("maxCooldown"),
        }

    # ---------- helpers ----------

    @staticmethod
    def _pid(event: Dict[str, Any]) -> Optional[int]:
        """Normaliza el id de jugador (summoners/items: `participantID`,
        skills: `participant`)."""
        for key in ("participantID", "participant", "participantId"):
            value = event.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _name(self, pid: Optional[int]) -> Optional[str]:
        if self.teams_observer is None or pid is None:
            return None
        return self.teams_observer.get_player_name(pid)

    def _side(self, pid: Optional[int]) -> Optional[str]:
        if self.teams_observer is None or pid is None:
            return None
        return self.teams_observer.get_player_team(pid)

    # ---------- getters ----------

    def get_summoner_spell_uses(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Usos de hechizo de invocador (Flash, TP, Smite...), en orden.
        Con `pid`, solo los de ese jugador."""
        return self._filter(self.summoner_spell_uses, pid)

    def get_channeling_starts(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Inicios de canalizacion (recall, teleport...), en orden."""
        return self._filter(self.channeling_starts, pid)

    def get_channeling_ends(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Finales de canalizacion, con `interrupted`, en orden."""
        return self._filter(self.channeling_ends, pid)

    def get_skill_uses(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Lanzamientos de habilidad (`skill_slot` 1-4), en orden."""
        return self._filter(self.skill_uses, pid)

    def get_item_actives(self, pid: Optional[int] = None) -> List[Dict[str, Any]]:
        """Usos de activo de objeto, en orden."""
        return self._filter(self.item_actives, pid)

    def get_events(self) -> List[Dict[str, Any]]:
        """Todo lo registrado, fusionado y ordenado por tiempo, con `kind` para
        distinguir el origen."""
        merged = (
            [{**e, "kind": "summoner_spell"} for e in self.summoner_spell_uses]
            + [{**e, "kind": "channeling_started"} for e in self.channeling_starts]
            + [{**e, "kind": "channeling_ended"} for e in self.channeling_ends]
            + [{**e, "kind": "skill"} for e in self.skill_uses]
            + [{**e, "kind": "item_active"} for e in self.item_actives]
        )
        return sorted(merged, key=lambda e: e["time"])

    @staticmethod
    def _filter(records: List[Dict[str, Any]], pid: Optional[int]) -> List[Dict[str, Any]]:
        if pid is None:
            return records
        return [r for r in records if r["player_id"] == pid]

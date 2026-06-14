import logging
from typing import Dict, Any, List, Optional
from .base import Observer

logger = logging.getLogger(__name__)


class BuildObserver(Observer):
    """
    Observador que reconstruye, por jugador, la **build path** (compras/ventas de
    items en orden) y el **skill order** (orden de subida de habilidades) a partir
    de la timeline de Riot (livestats RFC461).

    Se mantiene separado de `PostGameObserver` a propósito: parsear la timeline de
    items/skills es un trabajo distinto del end-state. La salida se cruza con el
    resto por `participantId` (1-10).

    Nomenclatura RFC461 verificada contra el feed real de GRID:
      - `item_purchased` / `item_sold` / `item_undo` → build path.
      - `skill_level_up` → skill order.

    Ojo: el id de jugador llega con tres casing distintos según el evento
    (`participantID` en items, `participant` en skills); se normalizan todos.
    """

    SLOT_MAP = {1: "Q", 2: "W", 3: "E", 4: "R"}

    def __init__(self):
        self.reset()

    def reset(self):
        """Reinicia el estado para una nueva partida."""
        # pid -> [{"ts_s": int, "action": "BUY"|"SELL", "item_id": int}, ...]
        self._builds: Dict[int, List[Dict[str, Any]]] = {}
        # pid -> ["Q", "W", ...]
        self._skills: Dict[int, List[str]] = {}

    def notify_event(self, event: Dict[str, Any]):
        """Procesa eventos de item y de subida de habilidad de la timeline."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema == "item_purchased":
            self._record_item(event, "BUY")
        elif schema == "item_sold":
            self._record_item(event, "SELL")
        elif schema == "item_undo":
            self._undo_item(event)
        elif schema == "skill_level_up":
            self._record_skill(event)

    @staticmethod
    def _participant_id(event: Dict[str, Any]) -> Optional[int]:
        """Normaliza el id de jugador (item: `participantID`, skill: `participant`)."""
        pid = event.get("participantID")
        if pid is None:
            pid = event.get("participant")
        if pid is None:
            pid = event.get("participantId")
        return int(pid) if pid is not None else None

    @staticmethod
    def _ts_seconds(event: Dict[str, Any]) -> int:
        """`gameTime` (ms) → segundos enteros."""
        return int(event.get("gameTime", 0)) // 1000

    def _record_item(self, event: Dict[str, Any], action: str):
        pid = self._participant_id(event)
        item_id = event.get("itemID")
        if pid is None or item_id is None:
            return
        self._builds.setdefault(pid, []).append({
            "ts_s": self._ts_seconds(event),
            "action": action,
            "item_id": int(item_id),
        })

    def _undo_item(self, event: Dict[str, Any]):
        """Resuelve un `item_undo`.

        El feed de GRID no usa `beforeId`/`afterId`: trae `itemID` y `goldGain`.
        Si se devolvió oro (`goldGain >= 0`) se deshizo una **compra**; si se pagó
        (`goldGain < 0`) se deshizo una **venta**. Eliminamos la última entrada
        coincidente (item + acción) del jugador.
        """
        pid = self._participant_id(event)
        item_id = event.get("itemID")
        if pid is None or item_id is None:
            return
        item_id = int(item_id)
        target_action = "BUY" if event.get("goldGain", 0) >= 0 else "SELL"
        entries = self._builds.get(pid, [])
        for i in range(len(entries) - 1, -1, -1):
            if entries[i]["item_id"] == item_id and entries[i]["action"] == target_action:
                del entries[i]
                return

    def _record_skill(self, event: Dict[str, Any]):
        # Excluir evoluciones (Kha'Zix, Viktor...): no consumen punto de habilidad.
        if event.get("evolved"):
            return
        pid = self._participant_id(event)
        letter = self.SLOT_MAP.get(event.get("skillSlot"))
        if pid is None or letter is None:
            return
        self._skills.setdefault(pid, []).append(letter)

    def get_builds(self) -> Dict[int, Dict[str, Any]]:
        """
        Devuelve, por `participantId`, la build path y el skill order.

        Returns:
            Dict[int, Dict[str, Any]]: `{pid: {"build_path": [...], "skill_order": "QWER..."}}`
        """
        pids = set(self._builds) | set(self._skills)
        return {
            pid: {
                "build_path": list(self._builds.get(pid, [])),
                "skill_order": "".join(self._skills.get(pid, [])),
            }
            for pid in sorted(pids)
        }

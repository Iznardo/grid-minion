"""Reloj compartido de los observers que muestrean `stats_update`.

`PositionObserver`, `StatsTimelineObserver` y `CooldownsObserver` leen el
mismo evento. Para poder cruzar sus series por índice (posición, oro y
cooldown del mismo instante, sin interpolar) los tres deciden qué ticks
guardan con esta misma lógica: con las mismas opciones, guardan exactamente
los mismos instantes.
"""
from typing import Any, Dict, Optional


class TickSampler:
    """Decide qué `stats_update` se guardan."""

    def __init__(self, sample_every_ms: Optional[int] = None,
                 t_from_s: Optional[float] = None, t_to_s: Optional[float] = None):
        self.sample_every_ms = sample_every_ms
        self.t_from_ms = None if t_from_s is None else int(t_from_s * 1000)
        self.t_to_ms = None if t_to_s is None else int(t_to_s * 1000)
        self.reset()

    def reset(self):
        self.last_t: Optional[int] = None
        # Ticks descartados por llegar repetidos o desordenados.
        self.dropped = 0

    def accept(self, event: Dict[str, Any]) -> Optional[int]:
        """Tiempo de juego (ms) si el evento es un tick a guardar; si no, `None`."""
        schema = event.get("rfc461Schema") or event.get("eventType")
        if schema != "stats_update":
            return None
        game_time = event.get("gameTime")
        if game_time is None:
            return None
        t = int(game_time)
        if self.t_from_ms is not None and t < self.t_from_ms:
            return None
        if self.t_to_ms is not None and t > self.t_to_ms:
            return None
        if self.last_t is not None:
            if t <= self.last_t:
                # Repetido o desordenado: el reloj debe ser creciente.
                self.dropped += 1
                return None
            if self.sample_every_ms and t - self.last_t < self.sample_every_ms:
                return None
        self.last_t = t
        return t


def participant_id(participant: Dict[str, Any]) -> Optional[int]:
    pid = participant.get("participantID")
    if pid is None:
        pid = participant.get("participantId")
    return int(pid) if pid is not None else None

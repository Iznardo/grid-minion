"""Utilidades HTTP internas compartidas por los clientes."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

# Tope de espera ante un Retry-After: un servidor que pida horas no debe
# congelar una extracción entera. Es holgado a propósito (15 min) para no
# reintentar antes de que se abra una ventana de cuota real: antes del tope se
# respetaba cualquier valor y no queremos cambiar ese comportamiento normal.
MAX_RETRY_AFTER_S = 900.0


def parse_retry_after(value: Optional[str], default: float,
                      cap: float = MAX_RETRY_AFTER_S) -> float:
    """Segundos a esperar según la cabecera `Retry-After`.

    Acepta los dos formatos de RFC 9110: segundos (`"30"`) o fecha HTTP
    (`"Wed, 21 Oct 2026 07:28:00 GMT"`). Si falta o no se puede interpretar,
    devuelve `default`. El resultado se acota a `[0, cap]`.
    """
    if not value:
        return min(default, cap)
    wait: Optional[float] = None
    try:
        wait = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            wait = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, IndexError):
            wait = None
    if wait is None:
        wait = default
    return max(0.0, min(wait, cap))

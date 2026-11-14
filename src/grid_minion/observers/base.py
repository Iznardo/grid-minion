from abc import ABC, abstractmethod
from typing import Dict, Any, FrozenSet, Optional

class Observer(ABC):
    """
    Clase base abstracta para todos los observadores de eventos.

    Cualquier clase que desee procesar eventos del GameEventProcessor debe
    heredar de esta clase e implementar el método notify_event.

    Atributo opcional `schemas`: conjunto de `rfc461Schema` de Riot LiveStats
    que el observer necesita (p. ej. `frozenset({"stats_update"})`). Con el
    valor por defecto, `None`, el observer recibe TODOS los eventos, de
    cualquier fuente. Un observer que lo declara solo recibe los eventos de
    esos esquemas, y permite a quien lee el fichero descartar el resto sin
    parsearlo (ver `GameEventProcessor.wanted_schemas()`).
    """
    schemas: Optional[FrozenSet[str]] = None

    @abstractmethod
    def notify_event(self, event: Dict[str, Any]):
        """
        Recibe un evento y decide si procesarlo.

        Args:
            event (Dict[str, Any]): Diccionario que contiene los datos del evento,
                incluyendo 'source', 'type' o 'rfc461Schema' dependiendo de la fuente.
        """
        pass
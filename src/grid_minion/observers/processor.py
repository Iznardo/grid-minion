import logging
from typing import List, Dict, Any, Optional
from .base import Observer

logger = logging.getLogger(__name__)

class GameEventProcessor:
    """
    Orquestador principal que distribuye eventos a los observadores registrados.

    Permite procesar eventos individuales o paquetes (bundles) de múltiples fuentes
    (GRID y Riot) asegurando un orden lógico de procesamiento.
    """
    def __init__(self):
        self._observers: List[Observer] = []

    def attach(self, observer: Observer):
        """
        Registra un observador para recibir eventos.

        IMPORTANTE: el orden de attach() importa. Los observers que dependen
        de otro deben registrarse DESPUÉS de sus dependencias:

            processor.attach(teams_obs)       # sin dependencias
            processor.attach(draft_obs)       # sin dependencias
            processor.attach(stats_obs)       # usa TeamsObserver en get_game_stats
            processor.attach(objectives_obs)  # sin dependencias
            processor.attach(wards_obs)       # recibe TeamsObserver en __init__

        Args:
            observer (Observer): Instancia de un observador.
        """
        self._observers.append(observer)

    def process_events(self, events: List[Dict[str, Any]]):
        """
        Itera sobre una lista simple de eventos y notifica a los observadores.

        Args:
            events (List[Dict[str, Any]]): Lista de eventos crudos.
        """
        for event in events:
            self._notify_all(event)

    def process_bundle(self, 
                       grid_state: Optional[Dict] = None, 
                       riot_summary: Optional[Dict] = None, 
                       riot_livestats: Optional[List[Dict]] = None,
                       grid_livestats: Optional[List[Dict]] = None):
        """
        Procesa múltiples fuentes de datos en el orden lógico correcto.

        El orden es:
        1. GRID State (Contexto global)
        2. Riot Summary (End state si está disponible)
        3. GRID LiveStats (Draft y eventos de GRID)
        4. Riot LiveStats (Timeline detallado)

        Args:
            grid_state (Optional[Dict]): Datos del estado global de GRID.
            riot_summary (Optional[Dict]): Resumen final de Riot.
            riot_livestats (Optional[List[Dict]]): Eventos del timeline de Riot.
            grid_livestats (Optional[List[Dict]]): Eventos de la partida de GRID.
        """

        # TODO: GRID_STATE será consumido por TeamsObserver u observers futuros
        # para disponer del roster pre-partida como fuente autoritativa antes
        # de que lleguen los eventos de Riot. En desarrollo para próximas versiones.
        if grid_state:
            context_event = {
                "source": "GRID_STATE",
                "rfc461Schema": "grid_state",
                "payload": grid_state
            }
            self._notify_all(context_event)

        if riot_summary:
            context_event = {
                "source": "RIOT_SUMMARY",
                "rfc461Schema": "riot_summary",
                "payload": riot_summary
            }
            self._notify_all(context_event)

        if grid_livestats:
            for event in grid_livestats:
                self._notify_all(event)

        if riot_livestats:
            for event in riot_livestats:
                self._notify_all(event)

    def _notify_all(self, event: Dict[str, Any]):
        """Helper privado para notificar a todos los observadores."""
        for observer in self._observers:
            try:
                observer.notify_event(event)
            except Exception:
                logger.exception(
                    "Observer %s falló procesando evento '%s'",
                    type(observer).__name__,
                    event.get("rfc461Schema", event.get("type", "?"))
                )
"""
Normalización de nombres de campeón mediante Data Dragon (ddragon) de Riot.

Motivo (decisión de diseño):
    Las dos fuentes que cruza la librería hablan idiomas distintos para el mismo
    campeón. El feed de draft de GRID emite el *display name* (`"Wukong"`,
    `"Lee Sin"`, `"Nunu & Willump"`), mientras que el summary de Riot emite la
    *clave interna* (`"MonkeyKing"`, `"LeeSin"`, `"Nunu"`). Slugificar no basta:
    `Wukong` ↔ `MonkeyKing` no es algorítmico. La única fuente autoritativa del
    mapeo es Data Dragon, así que la incorporamos aquí en lugar de mantener una
    tabla estática a mano (que tendría el mismo coste de mantenimiento que
    bundlear ddragon, pero peor).

Funcionamiento:
    - En la **primera petición** de la librería (lazy), el resolver consulta la
      última versión de Data Dragon. Si difiere de la cacheada en disco, descarga
      el `champion.json` nuevo y refresca la caché. Si coincide, usa la caché.
    - La caché vive en disco (`~/.cache/grid_minion/ddragon/`, overridable con la
      env var `GRID_MINION_CACHE_DIR`) para no re-descargar en cada ejecución.
    - Sin red pero con caché → se usa la caché (aunque esté vieja) con un warning.
    - Sin red **ni** caché → excepción explícita: es una librería de APIs, sin red
      no puede funcionar.

`normalize()` acepta tanto el display name de GRID como la clave de Riot y
devuelve siempre `(riot_id, numeric_key)`, donde `riot_id` es la clave interna
(`"MonkeyKing"`) — la misma que ya usa `TeamsObserver`/`PostGameObserver` desde
el summary — y `numeric_key` el id numérico de Riot (`62`).
"""
import os
import json
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests

from .exceptions import GridNetworkError, GridDataError

logger = logging.getLogger(__name__)

DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPION_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/{lang}/champion.json"
)
DEFAULT_LANG = "en_US"


def _is_lower_key(new: Optional[int], current: Optional[int]) -> bool:
    """True si `new` es una key numérica mejor (más baja) que `current`.

    Un `None` es siempre el peor candidato: una entrada sin key no debe
    desbancar a una que sí la tiene.
    """
    if new is None:
        return False
    if current is None:
        return True
    return new < current


class ChampionResolver:
    """
    Resuelve nombres de campeón contra Data Dragon, con caché en disco y
    comprobación de versión perezosa (en la primera normalización).

    Acepta tanto el display name de GRID como la clave interna de Riot.
    """

    def __init__(
        self,
        lang: str = DEFAULT_LANG,
        timeout: Tuple[int, int] = (10, 60),
        max_retries: int = 5,
    ):
        self._lang = lang
        self._timeout = timeout
        self._max_retries = max_retries

        self._lock = threading.Lock()
        self._initialized = False
        self._version: Optional[str] = None
        # display name -> {"id": <clave Riot>, "key": <id numérico>}
        self._by_name: Dict[str, Dict[str, Any]] = {}
        # clave Riot -> id numérico
        self._by_id: Dict[str, Optional[int]] = {}

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    @classmethod
    def from_mapping(
        cls, champions: Dict[str, Dict[str, Any]], version: str = "manual"
    ) -> "ChampionResolver":
        """
        Crea un resolver ya cargado desde un mapeo, sin tocar la red.

        `champions` tiene la forma `{<clave Riot>: {"name": <display>, "key": <int>}}`.
        Útil para tests y para escenarios offline controlados.
        """
        resolver = cls()
        resolver._apply(
            {"version": version, "lang": resolver._lang, "champions": champions}
        )
        resolver._initialized = True
        return resolver

    @property
    def version(self) -> Optional[str]:
        """Versión de Data Dragon con la que se construyó el mapeo activo."""
        return self._version

    def normalize(self, name: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
        """
        Devuelve `(riot_id, numeric_key)` para un nombre de cualquier fuente.

        Acepta el display name de GRID (`"Wukong"`) y la clave de Riot
        (`"MonkeyKing"`). Si el campeón no se reconoce, devuelve el nombre tal
        cual con `numeric_key = None` y registra un warning (no revienta: un
        campeón nuevo aún no cacheado no debe abortar el resto del draft).
        """
        if not name:
            return name, None

        self._ensure_loaded()

        # display name de GRID
        entry = self._by_name.get(name)
        if entry is not None:
            return entry["id"], entry["key"]

        # clave de Riot del summary (ya viene normalizada)
        if name in self._by_id:
            return name, self._by_id[name]

        logger.warning("Campeón no reconocido en Data Dragon: %r", name)
        return name, None

    def refresh(self) -> None:
        """Fuerza una recarga desde Data Dragon en la próxima normalización."""
        with self._lock:
            self._initialized = False

    # ------------------------------------------------------------------ #
    # Carga y caché
    # ------------------------------------------------------------------ #
    def _ensure_loaded(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._load()
            self._initialized = True

    def _load(self) -> None:
        cached = self._read_cache()

        try:
            latest = self._fetch_latest_version()
        except GridNetworkError as exc:
            if cached:
                logger.warning(
                    "No se pudo consultar la versión de Data Dragon (%s). "
                    "Usando caché local v%s.",
                    exc,
                    cached.get("version"),
                )
                self._apply(cached)
                return
            raise GridNetworkError(
                "No hay conexión con Data Dragon ni caché local de campeones. "
                "grid-minion necesita red para normalizar campeones."
            ) from exc

        if cached and cached.get("version") == latest:
            self._apply(cached)
            return

        try:
            fresh = self._fetch_champions(latest)
        except (GridNetworkError, GridDataError) as exc:
            if cached:
                logger.warning(
                    "No se pudo descargar champion.json v%s (%s). "
                    "Usando caché local v%s.",
                    latest,
                    exc,
                    cached.get("version"),
                )
                self._apply(cached)
                return
            raise

        self._write_cache(fresh)
        self._apply(fresh)
        logger.debug(
            "Data Dragon actualizado a v%s (%d campeones).",
            latest,
            len(fresh["champions"]),
        )

    def _apply(self, data: Dict[str, Any]) -> None:
        """Construye los índices en memoria desde la estructura cacheada.

        Data Dragon publica reskins de modos alternativos (las entradas `Jade_*`
        de LoL Classic, desde el parche 16.15.1) que **repiten el display name
        del campeón base** con otra key numérica: `Jade_Ezreal` -> name
        `"Ezreal"`, key `60081` (= 60000 + 81). Indexando en orden, la variante
        pisaba a la base y `normalize("Ezreal")` — el display name que emite el
        feed de draft de GRID — devolvía `("Jade_Ezreal", 60081)`.

        Regla: las variantes se ignoran. Ante un display name repetido gana la
        entrada con la key numérica más baja (los campeones reales están todos
        por debajo de 1000; las familias de reskins usan el offset 60000), y la
        descartada no entra en ningún índice — tampoco en `_by_id`, para que su
        clave interna no reintroduzca el id de la variante por la otra rama de
        `normalize`.
        """
        self._version = data.get("version")
        champions: Dict[str, Dict[str, Any]] = data.get("champions", {}) or {}

        # 1) Display name -> clave Riot ganadora (la de key más baja).
        winner_by_name: Dict[str, str] = {}
        for riot_id, champ in champions.items():
            name = champ.get("name")
            if not name:
                continue
            current = winner_by_name.get(name)
            if current is None or _is_lower_key(
                champ.get("key"), champions[current].get("key")
            ):
                winner_by_name[name] = riot_id

        # 2) Índices solo con las ganadoras (y con las entradas sin display
        #    name, que no compiten con nadie).
        by_name: Dict[str, Dict[str, Any]] = {}
        by_id: Dict[str, Optional[int]] = {}
        for riot_id, champ in champions.items():
            name = champ.get("name")
            key = champ.get("key")
            if not name:
                by_id[riot_id] = key
                continue
            if winner_by_name[name] != riot_id:
                continue  # variante de modo: se ignora
            by_name[name] = {"id": riot_id, "key": key}
            by_id[riot_id] = key

        self._by_name = by_name
        self._by_id = by_id

        ignored = len(champions) - len(by_id)
        if ignored:
            logger.debug(
                "Data Dragon v%s: %d entradas ignoradas por repetir el display "
                "name de un campeón base (reskins de modo).",
                self._version,
                ignored,
            )

    # ------------------------------------------------------------------ #
    # Disco
    # ------------------------------------------------------------------ #
    def _cache_dir(self) -> Path:
        base = os.environ.get("GRID_MINION_CACHE_DIR")
        root = Path(base) if base else Path.home() / ".cache" / "grid_minion"
        return root / "ddragon"

    def _cache_file(self) -> Path:
        return self._cache_dir() / f"champions_{self._lang}.json"

    def _read_cache(self) -> Optional[Dict[str, Any]]:
        path = self._cache_file()
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("champions"):
                return data
            logger.warning("Caché de campeones con formato inesperado: %s", path)
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("No se pudo leer la caché de campeones (%s).", exc)
            return None

    def _write_cache(self, data: Dict[str, Any]) -> None:
        path = self._cache_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Escritura atómica: tmp + replace, para no corromper la caché.
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("No se pudo escribir la caché de campeones (%s).", exc)

    # ------------------------------------------------------------------ #
    # Red (Data Dragon)
    # ------------------------------------------------------------------ #
    def _fetch_latest_version(self) -> str:
        data = self._get_json(DDRAGON_VERSIONS_URL)
        if not isinstance(data, list) or not data:
            raise GridDataError(
                "versions.json de Data Dragon vacío o con formato inesperado."
            )
        return data[0]

    def _fetch_champions(self, version: str) -> Dict[str, Any]:
        url = DDRAGON_CHAMPION_URL.format(version=version, lang=self._lang)
        data = self._get_json(url)
        raw = data.get("data", {}) if isinstance(data, dict) else {}

        champions: Dict[str, Dict[str, Any]] = {}
        for riot_id, champ in raw.items():
            try:
                key: Optional[int] = int(champ.get("key"))
            except (TypeError, ValueError):
                key = None
            champions[riot_id] = {"name": champ.get("name"), "key": key}

        if not champions:
            raise GridDataError(
                f"champion.json de Data Dragon (v{version}) sin campeones."
            )
        return {"version": version, "lang": self._lang, "champions": champions}

    def _get_json(self, url: str) -> Any:
        """GET con reintentos y backoff exponencial (5 × 1.5^(n-1)), como los clientes."""
        for attempt in range(1, self._max_retries + 1):
            wait = 5 * (1.5 ** (attempt - 1))
            try:
                response = requests.get(url, timeout=self._timeout)

                if response.status_code >= 500:
                    if attempt == self._max_retries:
                        raise GridNetworkError(
                            f"Data Dragon error {response.status_code} persistente "
                            f"tras {self._max_retries} intentos."
                        )
                    logger.warning(
                        "Data Dragon %s. Reintentando en %.1fs... (%d/%d)",
                        response.status_code,
                        wait,
                        attempt,
                        self._max_retries,
                    )
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    raise GridDataError(
                        f"Respuesta de Data Dragon no es JSON válido: {exc}"
                    )

            except requests.exceptions.RequestException as exc:
                if attempt == self._max_retries:
                    raise GridNetworkError(
                        f"Error de red consultando Data Dragon tras "
                        f"{self._max_retries} intentos: {exc}"
                    )
                logger.warning(
                    "Error de red con Data Dragon (%s). Reintentando en %.1fs...",
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue


# ---------------------------------------------------------------------- #
# Resolver por defecto a nivel de módulo (patrón "default session")
# ---------------------------------------------------------------------- #
_default_resolver: Optional[ChampionResolver] = None
_default_lock = threading.Lock()


def get_default_resolver() -> ChampionResolver:
    """Devuelve el resolver por defecto, creándolo perezosamente."""
    global _default_resolver
    if _default_resolver is None:
        with _default_lock:
            if _default_resolver is None:
                _default_resolver = ChampionResolver()
    return _default_resolver


def set_default_resolver(resolver: Optional[ChampionResolver]) -> None:
    """
    Sustituye el resolver por defecto.

    Útil para inyectar un resolver seedeado (`ChampionResolver.from_mapping`) en
    tests o entornos offline controlados. Pasar `None` lo reinicia.
    """
    global _default_resolver
    _default_resolver = resolver


def normalize_champion(name: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """
    Atajo: normaliza un nombre de campeón con el resolver por defecto.

    Devuelve `(riot_id, numeric_key)`. Ver `ChampionResolver.normalize`.
    """
    return get_default_resolver().normalize(name)

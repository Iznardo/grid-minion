"""Lectura en streaming de ficheros JSON Lines (Riot LiveStats).

El livestats de una partida pesa ~100 MB y, cargado entero como lista de
diccionarios, ocupa ~500 MB en memoria. Estas utilidades lo recorren línea a
línea: en memoria solo vive el evento que se está procesando.
"""
import gzip
import json
import re
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, Iterator, Optional, Union

from .exceptions import GridDataError

_SCHEMA_RE = re.compile(rb'"rfc461Schema"\s*:\s*"([^"]*)"')


def line_schema(line: bytes) -> Optional[str]:
    """Esquema RFC461 de una línea cruda, sin parsear el JSON.

    Devuelve `None` si la línea no declara esquema (no se puede descartar a
    ciegas: quien filtra debe conservarla).
    """
    match = _SCHEMA_RE.search(line)
    return match.group(1).decode("utf-8", "replace") if match else None


def parse_jsonl_lines(
    lines: Iterable[bytes],
    schemas: Optional[Collection[str]] = None,
    source: str = "JSONL",
) -> Iterator[Dict[str, Any]]:
    """Convierte líneas crudas en eventos, uno a uno.

    Args:
        lines: Líneas en bytes (de un fichero o de una respuesta HTTP).
        schemas: Si se indica, las líneas cuyo `rfc461Schema` no esté en el
            conjunto se descartan **antes** de parsear el JSON. `None` = todas.
        source: Nombre de la fuente, para los mensajes de error.

    Raises:
        GridDataError: si una línea no es JSON válido.
    """
    wanted = frozenset(schemas) if schemas is not None else None
    for number, line in enumerate(lines, start=1):
        if not line or not line.strip():
            continue
        if wanted is not None:
            schema = line_schema(line)
            if schema is not None and schema not in wanted:
                continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as exc:
            raise GridDataError(f"Error parseando {source} (línea {number}): {exc}")


def iter_jsonl(
    path: Union[str, Path],
    schemas: Optional[Collection[str]] = None,
) -> Iterator[Dict[str, Any]]:
    """Recorre un fichero JSON Lines (admite `.gz`) sin cargarlo en memoria.

    Pensado para reprocesar un livestats guardado con
    `GridRestClient.download_riot_livestats()`.
    """
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as fh:
        yield from parse_jsonl_lines(fh, schemas=schemas, source=str(path))

"""Paquetes de scouting: recortes de una `GameCapture` listos para analizar.

Cada paquete es una función pura `GameCapture -> dict` serializable a JSON,
con su propia versión de esquema. No leen el feed ni hacen peticiones: se
calculan sobre una captura ya hecha (o cargada de disco), así que añadir un
paquete nuevo no obliga a volver a descargar nada.

    capture = capture_game(client, "2922522", 1)
    level_one(capture)          # posicionamiento hasta 1:30
    jungle_pathing(capture)     # ruta de cada jungla, ganks e invades
    recall_openings(capture)    # compras y destino tras cada recall
"""
from .jungle import jungle_pathing
from .level_one import level_one
from .recalls import recall_openings

__all__ = ["level_one", "jungle_pathing", "recall_openings"]

"""Captura una partida y saca los tres paquetes de scouting.

Uso:
    python examples/scouting_packages.py <series_id> [game_number]

Necesita GRID_API_KEY en el entorno o en un .env. Guarda la captura en
`<series>_g<n>.capture.json.gz` (si ya existe, la reutiliza sin descargar).
"""
import os
import sys

from dotenv import find_dotenv, load_dotenv

from grid_minion import GameCapture, GridRestClient, capture_game
from grid_minion.packages import jungle_pathing, level_one, recall_openings


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}" if seconds is not None else "-"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    series_id = sys.argv[1]
    game_number = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    path = f"{series_id}_g{game_number}.capture.json.gz"

    if os.path.exists(path):
        capture = GameCapture.load(path)
        print(f"Captura cargada de {path}")
    else:
        load_dotenv(find_dotenv(usecwd=True))
        api_key = os.getenv("GRID_API_KEY")
        if not api_key:
            print("Configura GRID_API_KEY en el entorno o en un .env")
            return 2
        capture = capture_game(GridRestClient(api_key=api_key), series_id, game_number)
        if capture is None:
            print("GRID no tiene LiveStats de esa partida.")
            return 1
        capture.save(path)
        print(f"Captura guardada en {path} ({os.path.getsize(path) // 1024} KB)")

    print(f"\nPartida de {clock(capture.game['duration_s'])}, parche {capture.game['game_version']}")

    # --- Nivel 1 ---
    package = level_one(capture)
    print("\nNIVEL 1 (posición en 1:00)")
    for player in package["players"].values():
        mark = player["marks"].get("60")
        print(f"  {player['side']:4} {player['role']:8} {player['champion']:12} "
              f"{mark['zone'] if mark else '-'}")
    for invade in package["invades"]:
        print(f"  invade: {invade['champion']} en {invade['zone']} "
              f"{clock(invade['t_enter'])}-{clock(invade['t_exit'])}")

    # --- Pathing de jungla ---
    print("\nPATHING DE JUNGLA (hasta el primer recall)")
    for jungler in jungle_pathing(capture)["junglers"]:
        route = " > ".join(f"{c['monster']} {clock(c['t'])}" for c in jungler["camps"])
        print(f"  {jungler['side']} {jungler['champion']}: {route}")
        print(f"    fin de ventana {clock(jungler['window']['t1'])} ({jungler['window']['reason']})")
        for gank in jungler["ganks"]:
            print(f"    gank {gank['lane']} {clock(gank['t_enter'])} -> {gank['result']}")
        for invade in jungler["invades"]:
            print(f"    invade {invade['zone']} {clock(invade['t_enter'])} "
                  f"campamentos={invade['camps_taken']} -> {invade['result']}")

    # --- Openings tras backear ---
    print("\nPRIMEROS RECALLS (hasta 6:00)")
    for recall in recall_openings(capture, until_s=360)["recalls"]:
        destination = recall["destination"]["zone"] if recall["destination"] else "-"
        print(f"  {clock(recall['t_end'])} {recall['side']:4} {recall['champion']:12} "
              f"compra {recall['items_bought']} -> {destination}"
              f"{' (TP)' if recall['teleport'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

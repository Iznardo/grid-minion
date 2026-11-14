"""Contrasta, con datos reales de GRID, el draft de respaldo (`draftActions` del
end-state) contra el draft que reconstruye `DraftObserver` desde los eventos.

Para cada partida compara el equipo First Pick y los picks de cada lado, e
indica también qué FP habría dado la regla antigua (equipo de la primera
acción), para ver en qué partidas cambia el resultado.

Uso:
    python examples/verify_draft_fallback.py 2922522 2922523 ...

Necesita GRID_API_KEY en el entorno o en un .env. Sale con código 1 si alguna
partida no coincide.

Ojo con las ERL: cuando el equipo que elige First Pick quiere jugar en rojo,
se hace un primer draft para fijar el FP y, tras invalidarlo, se rehace en los
lados elegidos. `DraftObserver` se queda (a propósito) con el primer draft; el
end-state y el champ_select de Riot solo ven el segundo. En esos casos una
discrepancia de FP es esperada y el correcto es el de los eventos.
"""
import os
import sys

from dotenv import load_dotenv

from grid_minion import GridError, GridRestClient, split_grid_series
from grid_minion.champions import normalize_champion
from grid_minion.observers import DraftObserver, GameEventProcessor
from grid_minion.sources import extract_grid_game_state, normalize_grid_game_state


def _names(champs):
    return sorted(normalize_champion(c)[0] for c in champs if c)


def _old_rule_fp(actions):
    ordered = sorted(actions, key=lambda a: int(a.get("sequenceNumber", 0) or 0))
    for a in ordered:
        team = (a.get("drafter") or {}).get("id")
        if team is not None:
            return str(team)
    return None


def verify_series(client, series_id):
    events = client.get_grid_events(series_id)
    endstate = client.get_grid_endstate(series_id)
    if not events or not endstate:
        print(f"{series_id}: sin eventos o sin end-state, se omite.")
        return 0, 0

    checked = mismatches = 0
    for number, game_events in enumerate(split_grid_series(events), start=1):
        game_state = extract_grid_game_state(endstate, number)
        actions = (game_state or {}).get("draftActions") or []
        fallback = normalize_grid_game_state(game_state).get("draft")
        if not fallback:
            print(f"{series_id} G{number}: sin draftActions, se omite.")
            continue

        proc = GameEventProcessor()
        obs = DraftObserver()
        proc.attach(obs)
        proc.process_events(game_events)
        live = obs.get_draft()
        if not live["is_complete"]:
            print(f"{series_id} G{number}: draft de eventos incompleto, se omite.")
            continue

        checked += 1
        same_fp = str(live["fp"]["team_id"]) == fallback["fp"]["team_id"]
        same_picks = (
            _names(p["name"] for p in live["fp"]["picks"] if p) == _names(fallback["fp"]["picks"])
            and _names(p["name"] for p in live["sp"]["picks"] if p) == _names(fallback["sp"]["picks"])
        )
        old_fp = _old_rule_fp(actions)
        ok = same_fp and same_picks
        mismatches += not ok
        print(f"{series_id} G{number}: {'OK ' if ok else 'MAL'}  "
              f"FP eventos={live['fp']['team_id']}  FP respaldo={fallback['fp']['team_id']}  "
              f"FP regla antigua={old_fp}{'  <- la regla antigua fallaba' if old_fp != fallback['fp']['team_id'] else ''}"
              f"{'' if same_picks else '  picks distintos'}")
    return checked, mismatches


def main():
    load_dotenv()
    api_key = os.getenv("GRID_API_KEY")
    if not api_key:
        print("Configura GRID_API_KEY en el entorno o en un .env")
        return 2
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    client = GridRestClient(api_key=api_key)
    total = bad = 0
    for series_id in sys.argv[1:]:
        try:
            checked, mismatches = verify_series(client, series_id)
        except GridError as e:
            print(f"{series_id}: error de GRID: {e}")
            continue
        total += checked
        bad += mismatches
    print(f"\nPartidas comparadas: {total}  discrepancias: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

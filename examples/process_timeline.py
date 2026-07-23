"""
Ejemplo end-to-end de la **capa de timeline granular** de grid-minion.

Muestra el patrón correcto de uso: **UNA descarga por partida** (`get_riot_livestats`)
y luego TODOS los observers parsean esa misma lista en memoria — los observers NO hacen
ninguna petición de red. Reconstruye posiciones, economía/stats por jugador, combate,
visión, estructuras y spawns de objetivos (tipo de grieta y de Nashor).

Requisitos: `.env` en la raíz con `GRID_API_KEY`.

Ejecutar:
    python examples/process_timeline.py
"""
import os
from dotenv import load_dotenv

from grid_minion import GridRestClient, GridError
from grid_minion.observers import (
    GameEventProcessor,
    TeamsObserver,
    PlayerTimelineObserver,
    CombatObserver,
    WardEventsObserver,
    BuildingObserver,
    ObjectiveSpawnObserver,
)


def main():
    load_dotenv()
    api_key = os.getenv("GRID_API_KEY")
    if not api_key:
        print("Error: configura GRID_API_KEY en tu archivo .env")
        return

    client = GridRestClient(api_key=api_key)
    series_id = "2930129"  # Final MSI (BLG vs HLE), 5 partidas

    # Recorremos las partidas de la serie. Aquí probamos 1..5; en producción
    # puedes obtener el número de partidas con split_grid_series() sobre get_grid_events().
    for game_num in range(1, 6):
        try:
            # --- 1 SOLA descarga del timeline de esta partida (queda en memoria) ---
            riot_events = client.get_riot_livestats(series_id, game_number=game_num)
            if not riot_events:
                continue
            riot_summary = client.get_riot_summary(series_id, game_number=game_num)
        except GridError as e:
            print(f"Game {game_num}: error descargando ({e})")
            continue

        # --- Instanciar observers. El orden de attach() importa: los que dependen
        #     de TeamsObserver van después. ---
        processor = GameEventProcessor()
        teams = TeamsObserver()
        timeline = PlayerTimelineObserver()
        combat = CombatObserver(teams_observer=teams)
        wards = WardEventsObserver(teams_observer=teams)
        buildings = BuildingObserver(teams_observer=teams)
        spawns = ObjectiveSpawnObserver()

        processor.attach(teams)       # sin dependencias
        processor.attach(timeline)    # sin dependencias
        processor.attach(combat)      # depende de teams
        processor.attach(wards)       # depende de teams
        processor.attach(buildings)   # teams opcional
        processor.attach(spawns)      # sin dependencias

        # --- Una única pasada reparte la MISMA lista en memoria a todos los observers ---
        processor.process_bundle(riot_summary=riot_summary, riot_livestats=riot_events)

        # ------------------------------------------------------------------ salida
        print("\n" + "=" * 66)
        print(f"GAME {game_num}  —  {len(riot_events)} eventos")
        print("=" * 66)

        # Estado por jugador a máxima frecuencia
        for pid in timeline.get_players():
            name = teams.get_player_name(pid)
            positions = timeline.get_positions(pid)          # [{t, x, y}]
            economy = timeline.get_economy(pid)              # [{t, gold_total, ...}]
            stats = timeline.get_champion_stats(pid)         # [{t, ad, ap, ...}]
            last = economy[-1] if economy else {}
            print(f"[{pid:>2}] {name:<18} {len(positions):>4} posiciones | "
                  f"oro={last.get('gold_total')} lvl={last.get('level')} cs={last.get('cs')}")
            if stats:
                s = stats[-1]
                print(f"       stats finales: AD={s['ad']} AP={s['ap']} "
                      f"armor={s['armor']} MR={s['mr']} HP={s['hp_max']}")

        # Disponibilidad DIRECTA de ultimate (cooldownRemaining == 0), sin estimar
        if timeline.get_players():
            p = timeline.get_players()[0]
            eco = timeline.get_economy(p)
            marks = [t for t in (300, 600, 900, 1200) if eco and t < eco[-1]["t"]]
            avail = [f"{t // 60}min={'UP' if timeline.is_ultimate_up(p, t) else 'cd'}" for t in marks]
            print(f"\nUltimate de {teams.get_player_name(p)}: {', '.join(avail)}")

        # Diferencia de oro por equipo en el tiempo
        team_gold = timeline.get_team_gold_series()
        if team_gold.get(100) and team_gold.get(200):
            print(f"Oro final por equipo: BLUE={team_gold[100][-1]['gold']} "
                  f"RED={team_gold[200][-1]['gold']}")

        # Combate
        kills = combat.get_kills()
        print(f"\nCombate: {len(kills)} muertes, {len(combat.get_special_events())} especiales")
        for k in kills[:3]:
            m, s = divmod(int(k["time"]), 60)
            print(f"   [{m:02d}:{s:02d}] {k['killer']} ({k['killer_side']}) -> {k['victim']} "
                  f"| asistentes={len(k['assistants'])} fuentes_daño={len(k['damage_breakdown'])}")

        # Visión (colocación + destrucción)
        print(f"\nVisión: {len(wards.get_placements())} wards colocadas, "
              f"{len(wards.get_kills())} destruidas")

        # Estructuras
        print(f"Estructuras: {len(buildings.get_turrets())} torres, "
              f"{len(buildings.get_inhibitors())} inhibidores, "
              f"{len(buildings.get_plates())} placas")

        # Spawns de objetivos → tipo de grieta y de Nashor
        dragons = [d["dragon_type"] for d in spawns.get_dragon_spawns()]
        print(f"\nDragones (orden de spawn): {dragons}")
        print(f"Tipo de grieta (3.er dragón): {spawns.get_rift_type()}")
        print(f"Tipo de Nashor: {spawns.get_nashor_type()}")


if __name__ == "__main__":
    main()

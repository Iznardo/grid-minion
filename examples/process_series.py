import os
import logging
from dotenv import load_dotenv

# Importaciones de la libreria
from grid_minion import GridRestClient, split_grid_series, GridError
from grid_minion.observers import (
    GameEventProcessor,
    TeamsObserver,
    DraftObserver,
    PostGameObserver,
    ObjectiveKilledObserver,
    WardsObserver,
    BuildObserver
)

# Configuracion de logging para ver que ocurre internamente (opcional)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def main():
    # 1. Configuracion inicial
    load_dotenv()
    api_key = os.getenv("GRID_API_KEY")
    series_id = "2922522"  # ID de ejemplo (GX vs UCAM)

    if not api_key:
        print("Error: Configura GRID_API_KEY en tu archivo .env")
        return

    # 2. Inicializar el cliente REST
    client = GridRestClient(api_key=api_key)

    try:
        print(f"\nIniciando analisis de la Serie: {series_id}")
        
        # 3. Descarga de eventos de GRID
        print("Descargando eventos principales de GRID...")
        grid_events_full = client.get_grid_events(series_id)
        
        if not grid_events_full:
            print("Error: No se pudieron obtener eventos para esta serie.")
            return

        # 4. Dividir la serie en partidas individuales
        grid_games = split_grid_series(grid_events_full)
        print(f"Se han detectado {len(grid_games)} partidas.")

        # 5. Procesamiento de cada partida
        for i, grid_game_events in enumerate(grid_games):
            game_num = i + 1
            print(f"\n" + "="*60)
            print(f"PROCESANDO PARTIDA {game_num}")
            print("="*60)

            # A. Preparar procesador y observadores
            processor = GameEventProcessor()
            
            teams_obs = TeamsObserver()
            draft_obs = DraftObserver()
            stats_obs = PostGameObserver()
            objectives_obs = ObjectiveKilledObserver()
            wards_obs = WardsObserver(teams_observer=teams_obs)
            build_obs = BuildObserver()

            processor.attach(teams_obs)
            processor.attach(draft_obs)
            processor.attach(stats_obs)
            processor.attach(objectives_obs)
            processor.attach(wards_obs)
            processor.attach(build_obs)

            # B. Descargar datos adicionales de Riot
            print(f"Obteniendo datos de Riot...")
            riot_summary = client.get_riot_summary(series_id, game_number=game_num)
            riot_events = client.get_riot_livestats(series_id, game_number=game_num)

            # C. Ingesta masiva (Bundle)
            # El procesador se encarga de entregar los datos en el orden correcto
            processor.process_bundle(
                grid_livestats=grid_game_events,
                riot_summary=riot_summary,
                riot_livestats=riot_events
            )

            # 6. Mostrar resultados extraidos
            
            # --- Equipos y Mapeo de IDs ---
            print(f"\nEquipos:")
            for pid in range(1, 11):
                p = teams_obs.get_player_by_id(pid)
                if p:
                    grid_id = p.grid_player_id or "N/A"
                    print(f"   [{pid:02d}] {p.summoner_name:<20} | GRID ID: {grid_id:<8} | Lado: {p.team_side}")

            # --- Fase de Draft ---
            # Picks normalizados a clave de Riot vía Data Dragon: {"name", "id"}.
            if draft_obs.draft_found:
                draft = draft_obs.get_draft()
                fp_picks = [p['name'] for p in draft['fp']['picks']]
                sp_picks = [p['name'] for p in draft['sp']['picks']]
                print(f"\nDraft (Completo: {draft['is_complete']}):")
                print(f"   First Pick (Team {draft['fp']['team_id']}): {fp_picks}")
                print(f"   Second Pick (Team {draft['sp']['team_id']}): {sp_picks}")

            # --- Objetivos ---
            objs = objectives_obs.get_all_objectives()
            total_objs = sum(len(v) for v in objs.values())
            print(f"\nObjetivos neutrales capturados: {total_objs}")
            if objs['dragons']:
                print(f"   Ultimo dragon: {objs['dragons'][-1]['type']} ({objs['dragons'][-1]['team']})")

            # --- Estadisticas Finales ---
            final_stats = stats_obs.get_game_stats(teams_obs)
            meta = final_stats['meta']
            print(f"\nResultado:")
            print(f"   Ganador: {meta['winner']} | Version: {meta['version']}")

            # --- Builds por jugador (runas, items, skill order, build path) ---
            builds = build_obs.get_builds()
            print(f"\nBuilds (jugador 1):")
            p1 = final_stats['players'].get(1, {})
            b1 = builds.get(1, {})
            print(f"   Campeon:     {p1.get('champion')} (id {p1.get('champion_id')})")
            print(f"   Final items: {p1.get('final_items')}")
            print(f"   Runas:       {p1.get('runes')}")
            print(f"   Skill order: {b1.get('skill_order')}")
            print(f"   Build path:  {len(b1.get('build_path', []))} compras/ventas")

    except GridError as e:
        print(f"Error de la libreria: {e}")
    except Exception as e:
        print(f"Error inesperado: {e}")

if __name__ == "__main__":
    main()

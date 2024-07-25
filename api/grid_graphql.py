import json
import requests

def grid_graphql(headers, body):
    url = 'https://api.grid.gg/central-data/graphql'
    response = requests.post(url=url, headers=headers, json={"query": body})
    return response

#crear otra query con la que con un seriesId preguntar cuantos games hay dentro para poder sacar datos bien en officials que sean bo3/bo5 (igual sirve la misma)

def get_series(headers,start_time, end_time, game_type, title_id = 3, page_games = 25):
    
    all_ids = []
    
    #para poder hacer una query formateable hay que usar el doble de llaves {} para que funcione correctamente
    #se pueden poner parametros vacíos por lo cual podríamos usar una misma función para todas las querys e incluso pasar las páginas
    #vuelve el first porque sigue habiendo páginas incluso cuando marcas el tamaño de estas, añadirlo a la función, standard 25 games
    def fetch_data(cursor=""):
        body = """
        query GetGames {{
            allSeries(
                after: "{cursor}"
                first: {page_games}
                filter: {{
                    startTimeScheduled: {{
                        gte: "{start_time}"
                        lte: "{end_time}"
                    }}
                    titleIds: {{ in: {title_id} }}
                    types: {game_type}
                }}
                orderBy: StartTimeScheduled
            ) {{
                totalCount
                pageInfo{{
                    hasNextPage
                    endCursor
                }}
                edges {{
                    node {{
                        id    
                    }}
                }}
            }}
        }}
        """.format(start_time = start_time, end_time = end_time, game_type = game_type, title_id = title_id, page_games = page_games, cursor = cursor)
    
        response = grid_graphql(headers = headers, body = body)
        json_data = json.loads(response.text)
        nodes = json_data['data']['allSeries']['edges']

        ids = [n['node']['id'] for n in nodes]
        all_ids.extend(ids)

        page_info = json_data['data']['allSeries']['pageInfo']
        has_next_page = page_info['hasNextPage']
        end_cursor = page_info['endCursor']

        return has_next_page, end_cursor
    
    has_next_page, end_cursor = fetch_data()

    while has_next_page:
        has_next_page, end_cursor = fetch_data(cursor=end_cursor)

    return all_ids


#EJEMPLO DE USO
if __name__ == "__main__":

    headers = {
        "x-api-key": ""
    }

    start_time = "2024-01-01T00:00:00+01:00"
    end_time = "2024-02-12T00:00:00+01:00"
    game_type = "SCRIM"
    ids = get_series(headers, start_time, end_time, game_type)
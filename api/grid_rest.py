import json
import requests
from io import BytesIO
from zipfile import ZipFile

def download_grid_livestats(headers, seriesId):
    url = f"https://api.grid.gg/file-download/events/grid/series/{seriesId}"
    response = requests.get(url=url, headers=headers)
    # GESTIONAR CUANDO NO EXISTE EL ARCHIVO, PROBABLEMENTE LO MEJOR SEA DESDE EL GET_SERIES
    if(response.status_code != 200):
        return False
    extracted_file = ZipFile(BytesIO(response.content))
    jsonl_file = extracted_file.open(extracted_file.namelist()[0]).readlines()

    return jsonl_file

def load_jsonl(file):
    data = []
    for line in file:
        data.append(json.loads(line))

    return data

def get_summary(seriesId, headers):
    url = f"https://api.grid.gg/file-download/end-state/riot/series/{seriesId}/games/1/summary" #gestionar el que haya varios games (bo3/bo5)

    response = requests.get(url = url, headers = headers)
    # GESTIONAR CUANDO NO EXISTE EL ARCHIVO, PROBABLEMENTE LO MEJOR SEA DESDE EL GET_SERIES
    if(response.status_code != 200):
        return False
        #raise Exception('Bad response code:' + str(response)+ ' in ' + url, response.status_code)
    post_game_data = response.json()
    return post_game_data
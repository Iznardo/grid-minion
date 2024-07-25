import requests

def get_json(url, show_call = True):
    if show_call:
        print('Making Call: ' + url)

    resp = requests.get(url)
    if(resp.status_code != 200):
        raise Exception('Bad response code:' + str(resp)+ ' in ' + url, resp.status_code)
    return resp.json()

def get_champ_dict():
    url = 'https://ddragon.leagueoflegends.com/realms/na.json'
    
    realms_json = get_json(url)
    version = realms_json['n']['champion']

    url = 'http://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json' \
          .format(version = version)
    
    champs_json = get_json(url)
    
    champ_dict = dict()
    key_dict = dict()
    for champ in champs_json['data']:
        champ_dict[champs_json['data'][champ]['name']] = int(champs_json['data'][champ]['key'])
        key_dict[int(champs_json['data'][champ]['key'])] = champs_json['data'][champ]['id']
    return champ_dict, key_dict
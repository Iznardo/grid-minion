"""Construye el fixture real recortado y anonimizado de `tests/samples/real/`.

No forma parte de la suite: se ejecuta a mano cuando haga falta regenerar el
fixture. Necesita GRID_API_KEY en el entorno.

    python tests/tools/build_real_fixture.py [series_id] [game_number]

Por defecto usa la serie 2922522 (LES), partida 1, elegida porque incluye un
redraft de ERL: un primer draft que fija el First Pick, una invalidación y el
draft rehecho en los lados correctos.

Qué se recorta (el livestats crudo pesa ~106 MB):
  - Eventos de GRID: solo los que leen `split_grid_series`, `TeamsObserver` y
    `DraftObserver`, reducidos a los campos que usan.
  - Riot LiveStats: se quitan `champ_select`, los campos de envoltorio y
    `high_frequency_data`. De `stats_update` se conservan todos los ticks de
    los primeros 5 minutos, los previos a las marcas de 7:00 y 14:00, uno de
    cada 60 del resto y los últimos; de cada jugador se quitan `goldStats`,
    `stackingBuffs` y los contadores de `stats` que ningún observer lee. Los
    demás eventos se conservan todos.

Qué se anonimiza: PUUIDs, nombres de jugador, ids de cuenta y el hash de IP
(`hashedIP` de `game_info`), de forma
consistente entre las tres fuentes (el cruce GRID <-> Riot por PUUID sigue
funcionando).
"""
import gzip
import json
import os
import sys
from pathlib import Path

from grid_minion import GridRestClient, split_grid_series
from grid_minion.champions import ChampionResolver

OUT_DIR = Path(__file__).resolve().parents[1] / "samples" / "real"

GRID_DRAFT_TYPES = {
    "team-banned-character", "team-picked-character",
    "team-!banned-character", "team-!picked-character",
}
GRID_TEAM_TYPES = {"tournament-started-series", "series-started-game", "grid-started-feed"}
GRID_MARKER_TYPES = {"grid-invalidated-series", "game-aborted", "game-started"}

ENVELOPE_KEYS = {
    "rfc001Scope", "rfc190Scope", "rfc460Hostname", "rfc460Timestamp",
    "repeater_timestamp", "path", "platformID", "playbackID", "generationID",
    "source_type", "stageID", "gameName", "rootGameID", "parentGameID", "gameID",
}
NAME_KEYS = {"playerName", "riotId", "summonerName", "riotIdGameName",
             "displayName", "gameName", "summonerInternalName"}
ID_KEYS = {"accountID", "summonerID", "summonerId", "accountId"}

FULL_UNTIL_MS = 300_000
MARKS_MS = (420_000, 840_000)
MARK_WINDOW_MS = 5_000
KEEP_EVERY = 60
KEEP_LAST = 3

# Campos pesados del participante que ningún observer lee.
PARTICIPANT_DROP = {"goldStats", "stackingBuffs"}
# De los ~70 contadores de `stats` se conservan los que leen los observers
# actuales y los que leerá el observer de stats (contadores de combate).
STATS_KEEP = {
    "MINIONS_KILLED", "NEUTRAL_MINIONS_KILLED", "CHAMPIONS_KILLED", "NUM_DEATHS",
    "ASSISTS", "VISION_SCORE", "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS",
    "MAGIC_DAMAGE_DEALT_TO_CHAMPIONS", "PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS",
    "TRUE_DAMAGE_DEALT_TO_CHAMPIONS", "TOTAL_DAMAGE_TAKEN",
    "TOTAL_DAMAGE_TAKEN_FROM_CHAMPIONS", "MAGIC_DAMAGE_TAKEN",
    "PHYSICAL_DAMAGE_TAKEN", "TRUE_DAMAGE_TAKEN", "TOTAL_DAMAGE_SELF_MITIGATED",
    "TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES", "TOTAL_HEAL_ON_TEAMMATES",
    "TOTAL_DAMAGE_DEALT_TO_TURRETS", "TOTAL_DAMAGE_DEALT_TO_BUILDINGS",
    "TOTAL_DAMAGE_DEALT_TO_OBJECTIVES", "TOTAL_DAMAGE_DEALT_TO_EPIC_MONSTERS",
    "TOTAL_DAMAGE_TAKEN_FROM_BUILDINGS", "TIME_CCING_OTHERS",
    "TOTAL_TIME_CROWD_CONTROL_DEALT_TO_CHAMPIONS",
}


def _slim_teams(state):
    teams = []
    for team in (state or {}).get("teams", []) or []:
        players = []
        for player in team.get("players", []) or []:
            links = [
                {"dataProvider": {"name": "RIOT_PUUID"},
                 "externalEntity": {"id": (link.get("externalEntity") or {}).get("id")}}
                for link in player.get("externalLinks", []) or []
                if (link.get("dataProvider") or {}).get("name") == "RIOT_PUUID"
            ]
            players.append({"id": player.get("id"), "externalLinks": links})
        teams.append({"id": team.get("id"), "players": players})
    return {"teams": teams}


def slim_grid_events(wrappers):
    out = []
    for wrapper in wrappers:
        events = []
        for ev in wrapper.get("events", []):
            ev_type = ev.get("type")
            if ev_type in GRID_DRAFT_TYPES:
                name = ((ev.get("target") or {}).get("state") or {}).get("name")
                events.append({"type": ev_type,
                               "actor": {"id": (ev.get("actor") or {}).get("id")},
                               "target": {"state": {"name": name}}})
            elif ev_type in GRID_TEAM_TYPES:
                slim = {"type": ev_type}
                if (ev.get("state") or {}).get("teams"):
                    slim["state"] = _slim_teams(ev["state"])
                else:
                    slim["target"] = {"state": _slim_teams((ev.get("target") or {}).get("state"))}
                events.append(slim)
            elif ev_type in GRID_MARKER_TYPES:
                events.append({"type": ev_type})
        if events:
            out.append({"occurredAt": wrapper.get("occurredAt"),
                        "sequenceNumber": wrapper.get("sequenceNumber"),
                        "events": events})
    return out


class Anonymizer:
    """Sustituye PUUIDs, nombres e ids de cuenta de forma consistente."""

    def __init__(self, summary):
        self.puuids = {}
        for p in summary.get("participants", []):
            puuid = (p.get("puuid") or "").lower()
            if puuid:
                self.puuids[puuid] = f"puuid-player-{p.get('participantId')}"

    def scrub(self, node, pid=None):
        if isinstance(node, dict):
            local_pid = node.get("participantId", node.get("participantID", pid))
            for key, value in list(node.items()):
                if key == "puuid" and isinstance(value, str):
                    node[key] = self.puuids.get(value.lower(), "puuid-unknown")
                elif key == "externalEntity" and isinstance(value, dict):
                    ext = (value.get("id") or "")
                    if ext.lower() in self.puuids:
                        value["id"] = self.puuids[ext.lower()]
                elif key in NAME_KEYS and isinstance(value, str) and value:
                    node[key] = f"Player {local_pid}" if local_pid is not None else "Player"
                elif key == "hashedIP":
                    node[key] = ""
                elif key in ("riotIdTagline", "tagLine"):
                    node[key] = "TAG"
                elif key in ID_KEYS:
                    node[key] = local_pid if local_pid is not None else 0
                else:
                    self.scrub(value, local_pid)
        elif isinstance(node, list):
            for item in node:
                self.scrub(item, pid)
        return node


def _slim_participant(p):
    slim = {k: v for k, v in p.items() if k not in PARTICIPANT_DROP}
    if isinstance(slim.get("stats"), list):
        slim["stats"] = [st for st in slim["stats"] if st.get("name") in STATS_KEEP]
    return slim


def slim_livestats(events):
    stats_times = [e.get("gameTime", 0) for e in events if e.get("rfc461Schema") == "stats_update"]
    last_times = set(stats_times[-KEEP_LAST:])
    out = []
    stats_index = 0
    for ev in events:
        schema = ev.get("rfc461Schema")
        if schema == "champ_select":
            continue
        if schema == "stats_update":
            stats_index += 1
            t = ev.get("gameTime", 0)
            keep = (
                t <= FULL_UNTIL_MS
                or any(mark - MARK_WINDOW_MS <= t <= mark for mark in MARKS_MS)
                or stats_index % KEEP_EVERY == 0
                or t in last_times
            )
            if not keep:
                continue
        slim = {k: v for k, v in ev.items() if k not in ENVELOPE_KEYS}
        slim.pop("high_frequency_data", None)
        if schema == "stats_update":
            slim["participants"] = [_slim_participant(p) for p in slim.get("participants", [])]
        out.append(slim)
    return out


def champions_mapping(grid_slim, summary):
    """Mapeo mínimo de Data Dragon para los campeones de la partida.

    Permite que los tests normalicen campeones sin red
    (`ChampionResolver.from_mapping`). Forma: `{clave Riot: {name, key}}`.
    """
    resolver = ChampionResolver()
    names = {p.get("championName") for p in summary.get("participants", [])}
    for wrapper in grid_slim:
        for ev in wrapper["events"]:
            name = ((ev.get("target") or {}).get("state") or {}).get("name")
            if name:
                names.add(name)
    mapping = {}
    for name in sorted(n for n in names if n):
        riot_id, key = resolver.normalize(name)
        display = name if name != riot_id else mapping.get(riot_id, {}).get("name", name)
        if riot_id not in mapping or name != riot_id:
            mapping[riot_id] = {"name": display, "key": key}
    return mapping


def _write_gz(path, text):
    # mtime=0: el fichero es reproducible byte a byte.
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(text.encode("utf-8"))
    return os.path.getsize(path)


def main():
    series_id = sys.argv[1] if len(sys.argv) > 1 else "2922522"
    game_number = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    client = GridRestClient(api_key=os.environ["GRID_API_KEY"])

    grid_game = split_grid_series(client.get_grid_events(series_id))[game_number - 1]
    summary = client.get_riot_summary(series_id, game_number)
    livestats = client.get_riot_livestats(series_id, game_number)

    anon = Anonymizer(summary)
    summary.pop("tournamentCode", None)
    grid_slim = anon.scrub(slim_grid_events(grid_game))
    summary = anon.scrub(summary)
    live_slim = anon.scrub(slim_livestats(livestats))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    champions = champions_mapping(grid_slim, summary)
    (OUT_DIR / "champions.json").write_text(
        json.dumps(champions, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"champions.json: {len(champions)} campeones")
    sizes = {
        "grid_events.json.gz": _write_gz(
            OUT_DIR / "grid_events.json.gz", json.dumps(grid_slim, separators=(",", ":"))),
        "riot_summary.json.gz": _write_gz(
            OUT_DIR / "riot_summary.json.gz", json.dumps(summary, separators=(",", ":"))),
        "riot_livestats.jsonl.gz": _write_gz(
            OUT_DIR / "riot_livestats.jsonl.gz",
            "\n".join(json.dumps(e, separators=(",", ":")) for e in live_slim)),
    }
    for name, size in sizes.items():
        print(f"{name}: {size / 1024:.0f} KB")
    print(f"livestats: {len(live_slim)} eventos "
          f"({sum(e.get('rfc461Schema') == 'stats_update' for e in live_slim)} stats_update)")


if __name__ == "__main__":
    main()

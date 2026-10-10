#!/usr/bin/env python3
"""Pull a Steam profile's achievement data -> data.json + index.html.

Usage:
    STEAM_API_KEY=xxxx python3 fetch_steam.py --steamid 76561198417306884 [--refresh]

Stdlib only. Responses are cached in .cache/ so reruns are instant and you
don't hammer Steam's rate limit; pass --refresh to re-download.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.steampowered.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
UA = "steam-profile-builder/1.0"

# The client caches one asset folder per library game (owned + family-shared). It is the
# only offline source that includes family titles, so we use it as a candidate set and let
# GetPlayerAchievements decide which ones this account actually has progress in.
LIBRARY_CACHE_DIRS = [
    "~/Library/Application Support/Steam/appcache/librarycache",   # macOS
    "~/.steam/steam/appcache/librarycache",                        # Linux
    "~/.local/share/Steam/appcache/librarycache",
    "~/snap/steam/common/.local/share/Steam/appcache/librarycache",
    "%ProgramFiles(x86)%/Steam/appcache/librarycache",             # Windows
]


def local_library_appids():
    ids = set()
    for d in LIBRARY_CACHE_DIRS:
        p = os.path.expanduser(os.path.expandvars(d))
        if os.path.isdir(p):
            ids |= {int(n) for n in os.listdir(p) if n.isdigit()}
    return ids


def store_name(appid):
    """Last-resort name lookup for apps whose schema carries no gameName."""
    try:
        u = "https://store.steampowered.com/api/appdetails?" + urllib.parse.urlencode(
            {"appids": appid, "cc": "us", "l": "en"})
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20) as r:
            d = json.load(r)
        return ((d.get(str(appid)) or {}).get("data") or {}).get("name")
    except Exception:
        return None


USERDATA_DIRS = [
    "~/Library/Application Support/Steam/userdata",   # macOS
    "~/.steam/steam/userdata",                         # Linux
    "~/.local/share/Steam/userdata",
    "~/snap/steam/common/.local/share/Steam/userdata",
    "%ProgramFiles(x86)%/Steam/userdata",              # Windows
]


def local_play_stats(steamid):
    """Per-app {last played unix, playtime minutes} from the client's localconfig.vdf.

    The Web API exposes neither, and this also covers family games played here.
    """
    accountid = int(steamid) - 76561197960265728
    out = {}
    for d in USERDATA_DIRS:
        p = os.path.expanduser(os.path.expandvars(
            os.path.join(d, str(accountid), "config", "localconfig.vdf")))
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for m in re.finditer(r'"(\d{2,})"\s*\{\s*"LastPlayed"\s*"(\d+)"\s*"Playtime"\s*"(\d+)"', txt):
            out[int(m.group(1))] = {"last": int(m.group(2)), "playtime": int(m.group(3))}
    return out


def get(path, params, cache_key=None, refresh=False):
    """GET a Steam Web API endpoint as JSON, with disk cache + retry."""
    if cache_key:
        cp = os.path.join(CACHE, cache_key + ".json")
        if not refresh and os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    url = API + path + "?" + urllib.parse.urlencode(params)
    data = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            data = {"__http_error": e.code}   # cache the miss so reruns don't re-probe
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 * (attempt + 1))
    if cache_key and data is not None:
        os.makedirs(CACHE, exist_ok=True)
        cp = os.path.join(CACHE, cache_key + ".json")
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


def build_game(g, key, steamid, refresh):
    """Return a game dict with merged achievements, or None if the game has none."""
    appid = g["appid"]
    base = {"key": key, "steamid": steamid, "appid": appid, "format": "json"}

    pa = get("/ISteamUserStats/GetPlayerAchievements/v1/", base,
             cache_key="pa_%s" % appid, refresh=refresh)
    ps = (pa or {}).get("playerstats", {}) if isinstance(pa, dict) else {}
    # success is bool or, for some apps, the string "true"
    ok = ps.get("success") in (True, "true", 1)
    if not ok or not ps.get("achievements"):
        return None

    sc = get("/ISteamUserStats/GetSchemaForGame/v2/",
             {"key": key, "appid": appid, "format": "json"},
             cache_key="sc_%s" % appid, refresh=refresh)
    schema = {}
    for a in (((sc or {}).get("game") or {}).get("availableGameStats") or {}).get("achievements") or []:
        schema[a.get("name", "")] = a

    gl = get("/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
             {"gameid": appid, "format": "json"},
             cache_key="gl_%s" % appid, refresh=refresh)
    glob = {}
    for a in ((gl or {}).get("achievementpercentages") or {}).get("achievements") or []:
        try:                                   # Steam sends percent as a string, e.g. "46.8"
            glob[a.get("name", "")] = float(a.get("percent"))
        except (TypeError, ValueError):
            pass

    def ihash(u):          # keep only the icon hash; the URL prefix is rebuilt in JS
        return u.rsplit("/", 1)[-1].rsplit(".", 1)[0] if u else ""

    merged = []
    for a in ps["achievements"]:
        name = a.get("apiname", "")
        s = schema.get(name, {})
        desc = s.get("description", "") or ""
        merged.append({
            "title": s.get("displayName") or name,
            "desc": desc,
            "hidden": int(bool(s.get("hidden", 0))),
            "achieved": int(a.get("achieved") in (1, True)),
            "unlocktime": a.get("unlocktime") or 0,
            "icon": ihash(s.get("icon", "")),      # locked state is CSS grayscale
            "rarity": (round(glob[name], 2) if name in glob else None),
        })

    total = len(merged)
    unlocked = sum(1 for a in merged if a["achieved"])
    return {
        "appid": appid,
        "name": g.get("name") or ((sc or {}).get("game") or {}).get("gameName") or store_name(appid) or str(appid),
        "playtime": g.get("playtime_forever", 0),
        "family": bool(g.get("family")),
        "images": [
            "https://cdn.cloudflare.steamstatic.com/steam/apps/%s/capsule_616x353.jpg" % appid,
            "https://cdn.cloudflare.steamstatic.com/steam/apps/%s/header.jpg" % appid,
            "https://cdn.cloudflare.steamstatic.com/steam/apps/%s/library_hero.jpg" % appid,
        ],
        "total": total,
        "unlocked": unlocked,
        "percent": round(unlocked / total * 100, 1) if total else 0.0,
        "perfect": total > 0 and unlocked == total,
        "achievements": merged,
    }


def compute_stats(games):
    tot = sum(g["total"] for g in games)
    unl = sum(g["unlocked"] for g in games)
    return {
        "gamesWithAchievements": len(games),
        "perfectGames": sum(1 for g in games if g["perfect"]),
        "totalAchievements": tot,
        "unlockedAchievements": unl,
        "completion": round(unl / tot * 100, 1) if tot else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steamid", default=os.environ.get("STEAMID"))
    ap.add_argument("--key", default=os.environ.get("STEAM_API_KEY"))
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-download")
    ap.add_argument("--limit", type=int, default=0, help="only the first N games (debug)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if not args.steamid or not args.key:
        sys.exit("need --steamid and --key (or STEAMID / STEAM_API_KEY env)")

    summ = get("/ISteamUser/GetPlayerSummaries/v2/",
               {"key": args.key, "steamids": args.steamid, "format": "json"},
               cache_key="profile_%s" % args.steamid, refresh=args.refresh)
    players = ((summ or {}).get("response") or {}).get("players") or []
    if not players:
        sys.exit("profile not found / not visible to the API")
    p = players[0]

    owned = get("/IPlayerService/GetOwnedGames/v1/",
                {"key": args.key, "steamid": args.steamid, "include_appinfo": 1,
                 "include_played_free_games": 1, "format": "json"},
                cache_key="owned_%s" % args.steamid, refresh=args.refresh)
    owned_games = ((owned or {}).get("response") or {}).get("games") or []
    owned_ids = {g["appid"] for g in owned_games}

    # GetOwnedGames omits family-shared titles; GetRecentlyPlayedGames exposes the ones
    # played inside its ~2-week window, so union them and remember them across runs.
    recent = get("/IPlayerService/GetRecentlyPlayedGames/v1/",
                 {"key": args.key, "steamid": args.steamid, "count": 0, "format": "json"},
                 cache_key="recent_%s" % args.steamid, refresh=args.refresh)
    recent_games = ((recent or {}).get("response") or {}).get("games") or []

    extra_path = os.path.join(HERE, "family_appids.json")
    prev = {}
    if os.path.exists(extra_path):
        try:
            with open(extra_path) as f:
                prev = json.load(f)
        except Exception:
            prev = {}
    # a shared game may since have been bought outright
    extra = {k: v for k, v in prev.items() if int(k) not in owned_ids}

    for rg in recent_games:                    # family games played in the last ~2 weeks
        if rg.get("appid") and rg["appid"] not in owned_ids:
            extra[str(rg["appid"])] = {"appid": rg["appid"], "name": rg.get("name"),
                                       "playtime_forever": rg.get("playtime_forever", 0),
                                       "family": True}
    cache_ids = local_library_appids()          # full library incl. family, from the client
    for a in cache_ids:
        if a not in owned_ids and str(a) not in extra:
            extra[str(a)] = {"appid": a, "name": None, "playtime_forever": 0, "family": True}

    games = list(owned_games) + list(extra.values())
    if args.limit:
        games = games[:args.limit]
    print("owned: %d | family candidates: %d | total: %d" % (len(owned_games), len(extra), len(games)))

    out = []
    done = [0]

    def work(g):
        try:
            r = build_game(g, args.key, args.steamid, args.refresh)
        except Exception as e:
            r = None
            print("  ! %s (%s): %s" % (g.get("name"), g.get("appid"), e))
        done[0] += 1
        if done[0] % 10 == 0:
            print("  ... %d/%d" % (done[0], len(games)))
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(work, games):
            if r:
                out.append(r)

    play = local_play_stats(args.steamid)      # real playtime + last-played for every app
    for g in out:
        lp = play.get(g["appid"])
        g["last_played"] = lp["last"] if lp else 0
        if lp:
            g["playtime"] = max(g["playtime"], lp["playtime"])

    # keep only family games this account actually has data in; GetPlayerAchievements already
    # dropped everything without stats, so this just removes shared-but-never-played titles.
    out = [g for g in out if (not g.get("family")) or g["unlocked"] > 0 or g["playtime"] > 0]
    fam = {str(g["appid"]): {"appid": g["appid"], "name": g["name"],
                             "playtime_forever": g["playtime"], "family": True}
           for g in out if g.get("family")}
    with open(extra_path, "w") as f:
        json.dump(fam, f, ensure_ascii=False, indent=0)

    out.sort(key=lambda g: (not g["perfect"], -g["percent"], g["name"].lower()))
    stats = compute_stats(out)
    stats["familyGames"] = sum(1 for g in out if g.get("family"))
    stats["totalGames"] = len(games) if args.limit else (
        ((owned or {}).get("response") or {}).get("game_count", len(owned_games)) + stats["familyGames"])

    showcase = []
    sc_path = os.path.join(HERE, "showcase.json")
    if os.path.exists(sc_path):
        try:
            with open(sc_path) as f:
                raw = json.load(f)
            raw = raw.get("games", []) if isinstance(raw, dict) else raw
            known = {g["appid"] for g in out}
            showcase = [int(a) for a in raw if int(a) in known]
        except Exception as e:
            print("  ! showcase.json:", e)

    payload = {
        "profile": {
            "steamid": args.steamid,
            "personaname": p.get("personaname"),
            "avatarfull": p.get("avatarfull"),
            "profileurl": p.get("profileurl"),
        },
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "showcase": showcase,
        "games": out,
    }

    with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    # Data lives in its own file so index.html stays tiny. A <script src> (not fetch)
    # is used because it also works when the page is opened straight off disk.
    with open(os.path.join(HERE, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.STEAM_DATA=")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";")
    shutil.copyfile(os.path.join(HERE, "template.html"), os.path.join(HERE, "index.html"))

    print("games with achievements: %d | perfect: %d | %d/%d (%.1f%%)" % (
        stats["gamesWithAchievements"], stats["perfectGames"],
        stats["unlockedAchievements"], stats["totalAchievements"], stats["completion"]))
    print("wrote data.js + data.json + index.html")


def _selftest():
    games = [
        {"name": "A", "total": 10, "unlocked": 10, "percent": 100.0, "perfect": True},
        {"name": "B", "total": 10, "unlocked": 5, "percent": 50.0, "perfect": False},
    ]
    s = compute_stats(games)
    assert s["perfectGames"] == 1, s
    assert s["unlockedAchievements"] == 15, s
    assert s["completion"] == 75.0, s
    assert s["gamesWithAchievements"] == 2, s
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()

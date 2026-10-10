# Steam Profile — achievement tracker

A static, single-file Steam profile site: your perfect (100%) games, per-game
progress, and a full psnprofiles-style achievement page for every game.

**[View the live site →](index.html)** — dark, PS5-inspired UI.

## Features

- **Completionist Showcase** — a hand-picked grid of games, baked in at build time.
- **Library** — 5 games per page with cover art, total played, last played, and achievement progress.
- **Game pages** — every achievement with icon, description (how to unlock), global rarity % and unlock date.
- **Family-shared games** — Steam's API hides family library titles; this finds them.

## How it works

Steam's Web API doesn't expose everything, so the generator pulls from several sources:

| Data | Source |
|---|---|
| Owned games | `IPlayerService/GetOwnedGames` |
| Achievements | `ISteamUserStats/GetPlayerAchievements` |
| Descriptions | `ISteamUserStats/GetSchemaForGame` |
| Rarity | `ISteamUserStats/GetGlobalAchievementPercentagesForApp` |
| Family-shared games | local Steam client `appcache/librarycache` |
| Last played / playtime | local Steam client `localconfig.vdf` |

## Usage

```bash
cp .env.example .env        # add STEAM_API_KEY + STEAMID
python3 fetch_steam.py      # writes data.js + index.html
```

Get an API key at <https://steamcommunity.com/dev/apikey>. Needs Python 3 stdlib only
(no dependencies). Responses are cached in `.cache/`, so re-runs are fast;
pass `--refresh` to re-download.

## Editing the showcase

Open `index.html`, press **⌘E**, click games to add/remove, then **Save showcase.json**
and drop the file here. Re-run `fetch_steam.py` to bake it in. Visitors can't change it —
the section is driven entirely by `showcase.json` in this repo.

## Notes

- Family titles are only discoverable on a machine with Steam installed.
- `index.html` is tiny; the data lives in `data.js`, loaded via a `<script src>` so the
  page still works when opened directly from disk (a `fetch()` would be blocked there).

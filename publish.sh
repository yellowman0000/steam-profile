#!/usr/bin/env bash
# Refresh the data and publish to GitHub Pages.
set -e
cd "$(dirname "$0")"
set -a; . ./.env; set +a

python3 fetch_steam.py --steamid "$STEAMID"
git add -A
git commit -m "Update Steam profile data" || echo "nothing changed"
git push
echo "done — GitHub Pages redeploys in ~1 minute"

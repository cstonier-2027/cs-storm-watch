#!/bin/bash
# Deploys the CS's Storm Watch map (index.html + data/) to Cloudflare (Workers static assets).
# Run after wxmap/build.py finishes. Uses the token in ~/.config/stormwatch/cloudflare.env.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(dirname "$HERE")/wxmap"
SITE="$HERE/site"

export PATH="$HOME/.local/node/bin:$PATH"
WR="$HERE/node_modules/.bin/wrangler"
# API token saved by the user (CLOUDFLARE_API_TOKEN=...), used instead of `wrangler login`.
CF_ENV="$HOME/.config/stormwatch/cloudflare.env"
if [ -f "$CF_ENV" ]; then set -a; . "$CF_ENV"; set +a; fi

rm -rf "$SITE"; mkdir -p "$SITE"
# The page is written without a doctype (claude.ai adds one); add it here so browsers use standards mode.
{ echo "<!doctype html>"; cat "$SRC/index.html"; } > "$SITE/index.html"
cp "$SRC/apple-touch-icon.png" "$SRC/icon-512.png" "$SITE/"
rsync -a --exclude 'summary_*' "$SRC/data/" "$SITE/data/"
# Data changes every 30 min; the basemap never does.
cat > "$SITE/_headers" <<'H'
/data/*
  Cache-Control: no-cache
/data/base/*
  Cache-Control: public, max-age=86400
/index.html
  Cache-Control: no-cache
H

cd "$HERE" && "$WR" deploy --config wrangler.jsonc

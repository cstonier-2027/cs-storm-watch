#!/bin/bash
# One-shot refresh: rebuild the weather data, then deploy it to Cloudflare.
# Prints "OK built=<time>" on success, or the failing log tail.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
W="$ROOT/wxmap"
echo "[$(date "+%Y-%m-%d %H:%M")] refresh start"
cd "$W" || { echo "Map folder is gone"; exit 1; }

# Right after the Mac wakes, Wi-Fi may not be back yet: wait up to 3 minutes for it.
for _ in $(seq 1 18); do curl -s -m 5 -o /dev/null https://api.weather.gov/ && break; sleep 10; done

if ! .venv/bin/python -c "import numpy, PIL, eccodes" 2>/dev/null; then
  python3 -m venv --clear .venv && .venv/bin/pip install -q --upgrade pip \
    && .venv/bin/pip install -q numpy pillow eccodes eccodeslib || { echo "venv setup failed"; exit 1; }
fi

.venv/bin/python -W ignore build.py > build.log 2>&1
if ! tail -1 build.log | grep -q '^done'; then
  echo "BUILD FAILED"; tail -15 build.log; exit 1
fi

if ! "$ROOT/deploy/deploy.sh" > "$ROOT/deploy/deploy.log" 2>&1; then
  echo "DEPLOY FAILED"; tail -15 "$ROOT/deploy/deploy.log"; exit 1
fi
echo "OK built=$(tail -1 build.log | cut -d' ' -f2)"

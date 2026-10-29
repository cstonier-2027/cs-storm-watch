#!/bin/bash
# Builds "CS's Storm Watch.app" into the Storm Watch folder (next to wxmap/).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
APP="$ROOT/CS's Storm Watch.app"
PY="$ROOT/wxmap/.venv/bin/python"
WORK="$(mktemp -d)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -o "$APP/Contents/MacOS/CSStormWatch" "$HERE/main.swift" \
  -framework Cocoa -framework WebKit -framework UniformTypeIdentifiers

"$PY" "$HERE/make_icon.py" "$WORK/icon.png"
mkdir -p "$WORK/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$WORK/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>CS's Storm Watch</string>
  <key>CFBundleDisplayName</key><string>CS's Storm Watch</string>
  <key>CFBundleIdentifier</key><string>local.csstormwatch.map</string>
  <key>CFBundleExecutable</key><string>CSStormWatch</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.weather</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Data: NOAA/NWS, SPC, NCEP, IEM, Natural Earth</string>
</dict>
</plist>
PLIST

codesign --force --deep -s - "$APP"
rm -rf "$WORK"
echo "Built $APP"

#!/usr/bin/env bash
# Packs extension/ into dist/v-z-ble-extension.crx using Chrome.
#
# The first run creates keys/v-z-ble-extension.pem. Later runs sign with it, which keeps the
# extension's ID the same. Keep that file private and backed up: it's git-ignored and never deployed.
set -euo pipefail
cd "$(dirname "$0")"

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || { echo "Chrome not found at $CHROME. Set CHROME to its path."; exit 1; }
KEY="keys/v-z-ble-extension.pem"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/v-z-ble-extension" dist keys
cp -R extension/*.js extension/manifest.json extension/icons "$WORK/v-z-ble-extension/"

KEY_FLAG=()
[ -f "$KEY" ] && KEY_FLAG=(--pack-extension-key="$KEY")
"$CHROME" --headless=new --user-data-dir="$WORK/profile" --no-first-run --no-message-box \
  --pack-extension="$WORK/v-z-ble-extension" ${KEY_FLAG[@]+"${KEY_FLAG[@]}"} >/dev/null 2>&1 || true

[ -f "$WORK/v-z-ble-extension.crx" ] || { echo "Chrome didn't create the CRX."; exit 1; }
mv "$WORK/v-z-ble-extension.crx" dist/
chmod 644 dist/v-z-ble-extension.crx
if [ -f "$WORK/v-z-ble-extension.pem" ]; then
  mv "$WORK/v-z-ble-extension.pem" "$KEY"
  chmod 600 "$KEY"
fi
echo "Created dist/v-z-ble-extension.crx"

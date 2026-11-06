#!/usr/bin/env bash
# Builds the zip to upload to the Chrome Web Store: dist/v-z-ble-extension-<version>-store.zip.
# manifest.json sits at the top of the zip, as the store requires. This build leaves out the
# localhost address used for development, so it can only send to the live site.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist

python3 - <<'PY'
import json, re, zipfile
from pathlib import Path

src = Path("extension")
manifest = json.loads((src / "manifest.json").read_text())
keep = lambda items: [i for i in items if "localhost" not in i]
for script in manifest["content_scripts"]:
    script["matches"] = keep(script["matches"])
manifest["host_permissions"] = keep(manifest["host_permissions"])

background = re.sub(r'"http://localhost:\d+",\s*', "", (src / "background.js").read_text(encoding="utf-8"))
out = Path(f"dist/v-z-ble-extension-{manifest['version']}-store.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    z.writestr("background.js", background)
    for path in sorted(src.rglob("*")):
        if path.suffix in (".js", ".png") and path.name != "background.js":
            z.write(path, path.relative_to(src))
print(f"Created {out}")
PY

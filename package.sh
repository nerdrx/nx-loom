#!/usr/bin/env bash
# Package NX Loom into an installable addon zip (SPEC §9).
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import re, zipfile
from pathlib import Path

src = Path("nx_loom/__init__.py").read_text()
version = ".".join(re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', src).groups())
out = Path(f"nx-loom-{version}.zip")
out.unlink(missing_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(Path("nx_loom").rglob("*.py")):
        if "__pycache__" not in p.parts:
            z.write(p)
print(f"Wrote {out}")
PY

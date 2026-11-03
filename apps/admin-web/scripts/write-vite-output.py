"""Write a Vite in-memory build safely on Windows."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path


if len(sys.argv) != 2:
    raise SystemExit("usage: write-vite-output.py <admin-web-dist>")

output_root = Path(sys.argv[1]).resolve()
if output_root.name != "dist" or not (output_root.parent / "package.json").is_file():
    raise SystemExit(f"refusing unexpected build output path: {output_root}")

output_root.mkdir(parents=True, exist_ok=True)
written = 0
for line in sys.stdin:
    record = json.loads(line)
    destination = (output_root / record["fileName"]).resolve()
    destination.relative_to(output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(base64.b64decode(record["data"]))
    written += 1

if not (output_root / "index.html").read_bytes().lstrip().lower().startswith(b"<!doctype html"):
    raise SystemExit("Vite build did not produce a valid HTML entry point")
print(f"wrote {written} build assets safely")

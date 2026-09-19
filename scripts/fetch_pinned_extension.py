#!/usr/bin/env python3
"""Download the reviewed bundled extension; never follow a moving latest tag."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / "build-lock.json").read_text())
destination = ROOT / ".build/titanium-pinned.crx"
destination.parent.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(lock["extension_url"], timeout=60) as response:
    data = response.read(32 * 1024 * 1024 + 1)
if len(data) > 32 * 1024 * 1024:
    raise SystemExit("Extension exceeds the expected size limit")
if hashlib.sha256(data).hexdigest() != lock["extension_sha256"]:
    raise SystemExit("Bundled extension SHA-256 mismatch")
destination.write_bytes(data)
subprocess.run([sys.executable, str(ROOT / "extensions/bundle.py"),
                str(ROOT / "extensions/dist"), "titanium",
                destination.as_uri()], check=True)

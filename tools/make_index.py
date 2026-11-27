"""Regenerate profiles/index.json: the list of data files with their SHA-256, read by the app's data updater.

Usage (from the repository root):   python tools/make_index.py [profiles_dir]
Run it after every change to a profile, then commit profiles/index.json together with the change.
Files whose name starts with an underscore (templates) are skipped.
"""
import hashlib
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")
files = {}
for kind in ("airports", "aircraft"):
    d = os.path.join(root, kind)
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.startswith("_"):
            with open(os.path.join(d, name), "rb") as fh:
                files[f"{kind}/{name}"] = hashlib.sha256(fh.read()).hexdigest()
out = os.path.join(root, "index.json")
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump({"format": 1, "files": files}, fh, indent=1, sort_keys=True)
    fh.write("\n")
print(f"{len(files)} files indexed -> {out}")

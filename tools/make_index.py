"""Maintainers: rebuild profiles/index.json (the list of data files with their SHA-256) that the app's updater downloads first.

    python tools/make_index.py            # rewrite profiles/index.json
    python tools/make_index.py --check    # exit 1 if it is out of date (CI)

Run it after any change to profiles/airports, profiles/aircraft or profiles/verified.json. The app refuses every file whose hash
is not in the index, so an out-of-date index simply means the new data is not used yet."""
import hashlib
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "profiles")
if "--root" in sys.argv:                      # CI: judge the contribution's profiles with the trusted copy of this script
    ROOT = sys.argv[sys.argv.index("--root") + 1]


def build():
    files = {}
    for kind in ("airports", "aircraft"):
        d = os.path.join(ROOT, kind)
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json") and not fn.startswith("_"):
                files[f"{kind}/{fn}"] = hashlib.sha256(open(os.path.join(d, fn), "rb").read()).hexdigest()
    vp = os.path.join(ROOT, "verified.json")
    if os.path.isfile(vp):
        files["verified.json"] = hashlib.sha256(open(vp, "rb").read()).hexdigest()
    return json.dumps({"format": 1, "files": files}, indent=1, sort_keys=True) + "\n"


def main():
    new = build()
    path = os.path.join(ROOT, "index.json")
    old = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    if "--check" in sys.argv:
        if old != new:
            print("profiles/index.json is out of date: run python tools/make_index.py and commit the result")
            return 1
        print("index.json is current")
        return 0
    open(path, "w", encoding="utf-8", newline="\n").write(new)
    print(f"wrote {path} ({new.count(chr(10)) - 4} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

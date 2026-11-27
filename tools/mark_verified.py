"""Maintainers only: record that a profile has been checked against its source.

    python tools/mark_verified.py profiles/airports/EHLE.json --by "your-github-name"

Refuses unless the profile passes every check AND names at least one source. Writes the hash of the profile's content into
profiles/verified.json; ANY later edit of the profile changes the hash and the 'Verified' badge disappears until re-signed.
Pull requests that touch verified.json need maintainer review (see CODEOWNERS)."""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import validator as vd  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--by", required=True, help="who checked it")
    ap.add_argument("--data", help="OurAirports folder for runway cross-checks")
    a = ap.parse_args(argv)
    path = os.path.abspath(a.profile)
    kind = os.path.basename(os.path.dirname(path))
    stem = os.path.basename(path)[:-5]
    root = os.path.dirname(os.path.dirname(path))
    obj, issues = vd.load_file(path)
    if obj is None:
        print(issues[0].msg)
        return 1
    if kind == "airports":
        issues = vd.check_airport(obj, stem, mode="submit", dataset=vd.load_dataset(a.data) if a.data else None)
    elif kind == "aircraft":
        issues = vd.check_aircraft(obj, stem, mode="submit")
    else:
        print("profile must be in profiles/airports or profiles/aircraft")
        return 1
    bad = [i for i in issues if i.level == "error"]
    if bad:
        for i in bad:
            print(f"ERROR {i.path}: {i.msg}")
        return 1
    if not (obj.get("meta") or {}).get("sources") and not obj.get("_sources"):
        print("ERROR: add at least one source to meta.sources first")
        return 1
    mpath = os.path.join(root, "verified.json")
    manifest = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
    manifest[f"{kind}/{stem}.json"] = vd.content_hash(obj)
    manifest["_note"] = manifest.get("_note", "Written only by tools/mark_verified.py (maintainers).")
    manifest[f"_by_{kind}/{stem}.json"] = f"{a.by} {datetime.date.today().isoformat()}"
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    print(f"verified {kind}/{stem}.json (signed off by {a.by})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

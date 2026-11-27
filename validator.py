"""Downwind Call data validator: the single gatekeeper for airfield and aircraft profiles.

Used in three places, always with the same rules:
  * the app, when a user saves a profile from Settings          (mode "local")
  * the app, when it loads a profile from disk                  (sanitize_airport / sanitize_aircraft)
  * the GitHub check on every pull request                       (mode "submit", via the command line)

Standard library only. Command line:
    python validator.py profiles                          check every profile in a folder
    python validator.py profiles --base ../base/profiles  compare with the trusted copy (needs sources for safety changes)
    python validator.py profiles --data data              also cross-check ICAO codes and runways against OurAirports

Design rules:
  1. Whitelist, never blacklist: unknown keys are errors.
  2. Hard numeric limits on everything that moves a circuit or a speed band.
  3. Text is plain text only: no markup, no control or direction-changing characters, bounded length.
  4. Changes to safety fields (circuit side, height, geometry, speeds) need a source when submitted to the shared repo.
  5. Trust is never taken from the file itself: it comes from profiles/verified.json (hashes set by a maintainer).
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import namedtuple

Issue = namedtuple("Issue", "level path msg")        # level: "error" | "warn"

MAX_FILE_BYTES = 32 * 1024
MAX_DEPTH = 6

ICAO_RE = re.compile(r"^[A-Z0-9]{3,4}$")
AIRCRAFT_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
RWY_RE = re.compile(r"^(0[1-9]|[12][0-9]|3[0-6])[LRC]?$")
FREQ_RE = re.compile(r"^1[0-3][0-9]\.[0-9]{2,3}$")
SIMVAR_RE = re.compile(r"^[A-Z0-9 :_]{3,60}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}(/[^\s<>\"'`]*)?$", re.I)

# characters that can hide or reorder text: zero-width, bidi controls, BOM, soft hyphen, tags
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁩﻿­\U000e0000-\U000e007f]")
_BAD_MARKUP = re.compile(r"[<>`]|&#|&[a-z]+;|javascript:|data:text|\bon[a-z]+\s*=", re.I)

# ---- limits (the heart of "no funny business") ------------------------------------------------------------
AIRPORT_TOP = {"icao", "name", "tower_name", "frequencies", "notes", "circuit_agl_ft", "downwind_call_at", "default_side",
               "runway_sides", "circuit", "papi", "glide_deg", "glide_by_rwy", "papi_cfg", "calls", "meta",
               "turn_crosswind_agl_ft", "base_turn_agl_ft", "final_agl_ft", "check_lights"}
CIRCUIT_LIMITS = {                      # key: (min, max)  in NM
    "offset_nm": (0.3, 1.5), "base_along_nm": (0.1, 1.5), "final_side_nm": (0.15, 1.0), "short_final_nm": (0.2, 1.5),
    "crosswind_along_nm": (0.1, 1.5), "lead_nm": (0.0, 0.2), "final_trim_nm": (0.5, 3.0), "lead_s": (0.0, 8.0)}
PAPI_LIMITS = {"aim_ft": (300, 2500), "window_nm": (1.0, 10.0), "min_agl_ft": (0, 300)}
CALL_KEYS = {"ready", "downwind", "base", "final", "goaround", "vacated"}
# fields that change where or how high an aircraft flies in the pattern: these need a source when shared
AIRPORT_SAFETY = ("default_side", "runway_sides", "circuit_agl_ft", "circuit", "downwind_call_at", "turn_crosswind_agl_ft",
                  "base_turn_agl_ft", "final_agl_ft", "glide_deg", "glide_by_rwy")

AIRCRAFT_TOP = {"id", "name", "match", "carb_heat", "carb_simvar", "fuel_pump", "flap_detents", "flap_labels", "flap_words",
                "flap_calls", "targets", "rotate_kt", "limits", "meta"}
PHASES = ("TAKEOFF", "CLIMB", "CROSSWIND", "DOWNWIND", "DOWNWIND_LATE", "BASE", "FINAL", "FINAL_SHORT", "GOAROUND")
TARGET_KEYS = {"ias", "flaps", "carb", "track", "agl", "agl_min", "agl_max", "agl_lo_ft", "agl_hi_ft"}
AGL_WORDS = {"circuit", "turn"}
LIMIT_KEYS = ("vs0", "vs1", "vfe", "vno", "vne")


# ---- helpers -----------------------------------------------------------------------------------------------
def _p(*parts):
    return ".".join(str(x) for x in parts)


class _Ctx:
    def __init__(self):
        self.issues = []

    def err(self, path, msg):
        self.issues.append(Issue("error", path, msg))

    def warn(self, path, msg):
        self.issues.append(Issue("warn", path, msg))

    def text(self, path, v, maxlen, allow_empty=False):
        if not isinstance(v, str):
            self.err(path, "must be text")
            return False
        if not v and not allow_empty:
            self.err(path, "must not be empty")
            return False
        if len(v) > maxlen:
            self.err(path, f"too long ({len(v)} > {maxlen} characters)")
            return False
        if _HIDDEN.search(v) or any(unicodedata.category(c) in ("Cc", "Cf") for c in v):
            self.err(path, "contains control or hidden characters")
            return False
        if _BAD_MARKUP.search(v):
            self.err(path, "markup or script-like text is not allowed (plain text only)")
            return False
        return True

    def num(self, path, v, lo, hi, allow_none=False):
        if v is None and allow_none:
            return True
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.err(path, "must be a number")
            return False
        if not lo <= v <= hi:
            self.err(path, f"{v} is outside the allowed range {lo} to {hi}")
            return False
        return True

    def enum(self, path, v, allowed):
        if v not in allowed:
            self.err(path, f"must be one of {sorted(allowed)}")
            return False
        return True

    def unknown_keys(self, path, d, allowed):
        for k in d:
            if isinstance(k, str) and k.startswith("_"):
                self._comment(_p(path, k) if path else k, d[k])
            elif k not in allowed:
                self.err(_p(path, k) if path else k, "unknown key")

    def _comment(self, path, v):
        if isinstance(v, str):
            self.text(path, v, 600, allow_empty=True)
        elif isinstance(v, list) and all(isinstance(x, str) for x in v) and len(v) <= 12:
            for i, x in enumerate(v):
                self.text(_p(path, i), x, 400, allow_empty=True)
        elif not isinstance(v, (bool, int, float)):
            self.err(path, "comment keys (starting with _) may hold text, a list of text, or a number only")


def _depth(o, d=0):
    if d > MAX_DEPTH:
        return d
    if isinstance(o, dict):
        return max([_depth(v, d + 1) for v in o.values()] + [d])
    if isinstance(o, list):
        return max([_depth(v, d + 1) for v in o] + [d])
    return d


# ---- strict loading ----------------------------------------------------------------------------------------
def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key: {k}")
        out[k] = v
    return out


def _bad_const(name):
    raise ValueError(f"{name} is not allowed")


def parse_strict(raw, max_bytes=MAX_FILE_BYTES):
    """bytes/str -> (obj, issues). Rejects oversize files, duplicate keys, NaN/Infinity, deep nesting."""
    c = _Ctx()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > max_bytes:
        c.err("", f"file is too large ({len(raw)} bytes, limit {max_bytes})")
        return None, c.issues
    try:
        text = raw.decode("utf-8")
        obj = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_bad_const)
    except (UnicodeDecodeError, ValueError) as exc:
        c.err("", f"not valid JSON: {exc}")
        return None, c.issues
    if not isinstance(obj, dict):
        c.err("", "top level must be an object")
        return None, c.issues
    if _depth(obj) > MAX_DEPTH:
        c.err("", "nested too deeply")
        return None, c.issues
    return obj, c.issues


def load_file(path, max_bytes=MAX_FILE_BYTES):
    with open(path, "rb") as fh:
        return parse_strict(fh.read(), max_bytes)


# ---- trust: never taken from the file itself ---------------------------------------------------------------
def content_hash(profile):
    """Hash of everything except meta.status, so a status label can never fake verification."""
    p = json.loads(json.dumps(profile))
    if isinstance(p.get("meta"), dict):
        p["meta"].pop("status", None)
    blob = json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def trust_level(profile, key, manifest):
    """'verified' only if a maintainer recorded this exact content; 'community' = shared and unreviewed; else 'local'."""
    if not isinstance(profile, dict):
        return "local"
    if manifest.get(key) == content_hash(profile):
        return "verified"
    st = (profile.get("meta") or {}).get("status")
    return "community" if st == "community" else "local"


# ---- airport -----------------------------------------------------------------------------------------------
def _check_meta(c, meta, mode, safety_changed):
    if meta is None:
        return
    if not isinstance(meta, dict):
        c.err("meta", "must be an object")
        return
    c.unknown_keys("meta", meta, {"status", "sources", "change_note", "updated"})
    if "status" in meta:
        c.enum("meta.status", meta["status"], {"community", "local"})   # 'verified' is only ever set through verified.json
    if "change_note" in meta:
        c.text("meta.change_note", meta["change_note"], 300)
    if "updated" in meta and not (isinstance(meta["updated"], str) and DATE_RE.match(meta["updated"])):
        c.err("meta.updated", "must be a date like 2026-11-27")
    srcs = meta.get("sources", [])
    if not isinstance(srcs, list) or len(srcs) > 8:
        c.err("meta.sources", "must be a list of at most 8 sources")
        srcs = []
    for i, s in enumerate(srcs):
        sp = _p("meta.sources", i)
        if not isinstance(s, dict):
            c.err(sp, "must be an object with title, url and date")
            continue
        c.unknown_keys(sp, s, {"title", "url", "date"})
        c.text(_p(sp, "title"), s.get("title", ""), 120)
        u = s.get("url", "")
        if not (isinstance(u, str) and len(u) <= 300 and URL_RE.match(u)):
            c.err(_p(sp, "url"), "must be an https:// web address")
        elif re.match(r"^https://(localhost|\d{1,3}(\.\d{1,3}){3}|\[)", u, re.I):
            c.err(_p(sp, "url"), "must be a public website, not a local or numeric address")
        if not (isinstance(s.get("date"), str) and DATE_RE.match(s.get("date", ""))):
            c.err(_p(sp, "date"), "must be the date you read it, like 2026-11-27")
    if mode == "submit" and safety_changed and not srcs:
        c.err("meta.sources", "this change affects the circuit or speeds: add at least one source (AIP/AIC/VAC page or POH section) with its date")
    if mode == "submit" and safety_changed and not meta.get("change_note"):
        c.err("meta.change_note", "explain in one sentence why this changes")


def _changed(a, b):
    return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def check_airport(profile, filename_icao=None, baseline=None, mode="local", dataset=None):
    """mode: 'local' (user's own machine), 'submit' (shared repo), 'load'. dataset: {ICAO: set(runway idents)} or None."""
    c = _Ctx()
    if not isinstance(profile, dict):
        return [Issue("error", "", "profile must be an object")]
    c.unknown_keys("", profile, AIRPORT_TOP)
    icao = profile.get("icao")
    if not (isinstance(icao, str) and ICAO_RE.match(icao)):
        c.err("icao", "must be the 3-4 letter/digit airport code in capitals")
    elif filename_icao and icao != filename_icao:
        c.err("icao", f"must match the file name ({filename_icao}.json)")
    if "name" in profile:
        c.text("name", profile["name"], 60)
    if "tower_name" in profile:
        c.text("tower_name", profile["tower_name"], 60)

    fr = profile.get("frequencies")
    if fr is not None:
        if not isinstance(fr, dict) or len(fr) > 12:
            c.err("frequencies", "must be an object with at most 12 entries")
        else:
            for k, v in fr.items():
                c.text(_p("frequencies", k, "(name)"), k, 24)
                if not (isinstance(v, str) and FREQ_RE.match(v)):
                    c.err(_p("frequencies", k), "must look like 135.180 (118.000 to 136.975 MHz)")
    notes = profile.get("notes")
    if notes is not None:
        if not isinstance(notes, list) or len(notes) > 12:
            c.err("notes", "must be a list of at most 12 lines")
        else:
            for i, n in enumerate(notes):
                c.text(_p("notes", i), n, 300)

    if "circuit_agl_ft" in profile:
        c.num("circuit_agl_ft", profile["circuit_agl_ft"], 400, 1500)
    if "downwind_call_at" in profile:
        c.enum("downwind_call_at", profile["downwind_call_at"], {"midfield", "abeam"})
    if "default_side" in profile:
        c.enum("default_side", profile["default_side"], {"left", "right"})
    rs = profile.get("runway_sides")
    if rs is not None:
        if not isinstance(rs, dict) or len(rs) > 12:
            c.err("runway_sides", "must be an object with at most 12 runways")
        else:
            for k, v in rs.items():
                if not RWY_RE.match(str(k)):
                    c.err(_p("runway_sides", k), "runway name must look like 05, 23, 09L")
                c.enum(_p("runway_sides", k), v, {"left", "right"})
    for key, lo, hi in (("turn_crosswind_agl_ft", 200, 1000), ("base_turn_agl_ft", 300, 1000), ("final_agl_ft", 200, 800)):
        if key in profile:
            c.num(key, profile[key], lo, hi)
    if "check_lights" in profile and not isinstance(profile["check_lights"], bool):
        c.err("check_lights", "must be true or false")

    circ = profile.get("circuit")
    if circ is not None:
        if not isinstance(circ, dict):
            c.err("circuit", "must be an object")
        else:
            c.unknown_keys("circuit", circ, set(CIRCUIT_LIMITS))
            for k, (lo, hi) in CIRCUIT_LIMITS.items():
                if k in circ:
                    c.num(_p("circuit", k), circ[k], lo, hi, allow_none=True)
            off, base = circ.get("offset_nm"), circ.get("base_along_nm")
            if isinstance(off, (int, float)) and isinstance(base, (int, float)) and off > 0 and base / off > 4:
                c.warn("circuit", "base turn is very far out compared with the downwind offset: check the numbers")

    if "papi" in profile and not isinstance(profile["papi"], bool):
        c.err("papi", "must be true or false")
    if "glide_deg" in profile:
        c.num("glide_deg", profile["glide_deg"], 2.0, 5.5)
    gb = profile.get("glide_by_rwy")
    if gb is not None:
        if not isinstance(gb, dict) or len(gb) > 12:
            c.err("glide_by_rwy", "must be an object with at most 12 runways")
        else:
            for k, v in gb.items():
                if not RWY_RE.match(str(k)):
                    c.err(_p("glide_by_rwy", k), "runway name must look like 05, 23, 09L")
                c.num(_p("glide_by_rwy", k), v, 2.0, 5.5)
    pc = profile.get("papi_cfg")
    if pc is not None:
        if not isinstance(pc, dict):
            c.err("papi_cfg", "must be an object")
        else:
            c.unknown_keys("papi_cfg", pc, set(PAPI_LIMITS))
            for k, (lo, hi) in PAPI_LIMITS.items():
                if k in pc:
                    c.num(_p("papi_cfg", k), pc[k], lo, hi)
    calls = profile.get("calls")
    if calls is not None:
        if not isinstance(calls, dict):
            c.err("calls", "must be an object")
        else:
            for k, v in calls.items():
                if k == "_":
                    continue
                if k not in CALL_KEYS:
                    c.err(_p("calls", k), f"unknown call (allowed: {sorted(CALL_KEYS)})")
                else:
                    c.text(_p("calls", k), v, 300)

    # cross-check with real airport data
    if dataset is not None and isinstance(icao, str) and ICAO_RE.match(icao):
        if icao not in dataset:
            (c.err if mode == "submit" else c.warn)("icao", f"{icao} is not in the OurAirports data")
        else:
            idents = dataset[icao]
            for key in ("runway_sides", "glide_by_rwy"):
                for r in (profile.get(key) or {}):
                    if r not in idents:
                        c.err(_p(key, r), f"runway {r} does not exist at {icao} (known: {', '.join(sorted(idents)) or 'none'})")

    # safety fields changed against the trusted copy?
    safety_changed = False
    if baseline is None:
        safety_changed = any(k in profile for k in AIRPORT_SAFETY)
    else:
        safety_changed = any(_changed(profile.get(k), baseline.get(k)) for k in AIRPORT_SAFETY)
        _delta_checks(c, profile, baseline)
    _check_meta(c, profile.get("meta"), mode, safety_changed)
    if mode == "submit" and safety_changed and "meta" not in profile:
        c.err("meta", "safety fields changed: add a meta block with sources and change_note")
    return c.issues


def _delta_checks(c, new, old):
    """Compare with the trusted copy. Big jumps are flagged for the maintainer (warn) or blocked (error)."""
    ors, nrs = old.get("runway_sides") or {}, new.get("runway_sides") or {}
    for r in set(ors) & set(nrs):
        if ors[r] != nrs[r]:
            c.warn(_p("runway_sides", r), f"CIRCUIT SIDE CHANGE {ors[r]} -> {nrs[r]}: maintainer must check the source")
    if old.get("default_side") and new.get("default_side") and old["default_side"] != new["default_side"]:
        c.warn("default_side", f"CIRCUIT SIDE CHANGE {old['default_side']} -> {new['default_side']}: maintainer must check the source")
    oc, nc = old.get("circuit") or {}, new.get("circuit") or {}
    for k in CIRCUIT_LIMITS:
        a, b = oc.get(k), nc.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0:
            ch = abs(b - a) / a
            if ch > 0.6:
                c.err(_p("circuit", k), f"changes by {ch:.0%} in one step ({a} -> {b}); split into smaller, sourced steps")
            elif ch > 0.25:
                c.warn(_p("circuit", k), f"changes by {ch:.0%} ({a} -> {b}): maintainer must check the source")
    a, b = old.get("circuit_agl_ft"), new.get("circuit_agl_ft")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0 and abs(b - a) / a > 0.3:
        c.warn("circuit_agl_ft", f"circuit height changes {a} -> {b} ft: maintainer must check the source")


def sanitize_airport(profile, filename_icao=None):
    """For loading: drop every key that fails the rules instead of using it. Returns (clean, issues)."""
    issues = check_airport(profile, filename_icao=filename_icao, mode="load")
    return _drop_bad(profile, issues), [i for i in issues if i.level == "error"]


def _drop_bad(profile, issues):
    clean = json.loads(json.dumps(profile))
    for i in issues:
        if i.level != "error" or not i.path:
            continue
        parts = i.path.replace("(name)", "").strip(".").split(".")
        cur = clean
        for part in parts[:-1]:
            cur = cur.get(part) if isinstance(cur, dict) else (cur[int(part)] if isinstance(cur, list) and part.isdigit() and int(part) < len(cur) else None)
            if cur is None:
                break
        else:
            last = parts[-1]
            if isinstance(cur, dict):
                cur.pop(last, None)
            elif isinstance(cur, list) and last.isdigit() and int(last) < len(cur):
                cur[int(last)] = ""
    if isinstance(clean.get("notes"), list):
        clean["notes"] = [n for n in clean["notes"] if isinstance(n, str) and n]
    return clean


# ---- aircraft ----------------------------------------------------------------------------------------------
def check_aircraft(profile, filename_id=None, baseline=None, mode="local"):
    c = _Ctx()
    if not isinstance(profile, dict):
        return [Issue("error", "", "profile must be an object")]
    c.unknown_keys("", profile, AIRCRAFT_TOP)
    pid = profile.get("id")
    if not (isinstance(pid, str) and AIRCRAFT_ID_RE.match(pid)):
        c.err("id", "must be 2-32 characters: lowercase letters, digits, underscore")
    elif filename_id and pid != filename_id:
        c.err("id", f"must match the file name ({filename_id}.json)")
    c.text("name", profile.get("name", ""), 80)
    m = profile.get("match", [])
    if not isinstance(m, list) or len(m) > 10:
        c.err("match", "must be a list of at most 10 words")
    else:
        for i, w in enumerate(m):
            c.text(_p("match", i), w, 40)
    for key in ("carb_heat", "fuel_pump"):
        if key in profile and not isinstance(profile[key], bool):
            c.err(key, "must be true or false")
    if "carb_simvar" in profile and not (isinstance(profile["carb_simvar"], str) and SIMVAR_RE.match(profile["carb_simvar"])):
        c.err("carb_simvar", "must be a simulator variable name in capitals, like GENERAL ENG ANTI ICE POSITION:1")
    if "rotate_kt" in profile:
        c.num("rotate_kt", profile["rotate_kt"], 30, 200)

    det = profile.get("flap_detents", [0])
    if not (isinstance(det, list) and 1 <= len(det) <= 8 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100 for v in det)
            and det == sorted(det) and len(set(det)) == len(det)):
        c.err("flap_detents", "must be 1-8 increasing flap positions between 0 and 100")
        det = [0]
    for key in ("flap_labels", "flap_words"):
        if key in profile:
            v = profile[key]
            if not (isinstance(v, list) and len(v) == len(det)):
                c.err(key, f"needs exactly {len(det)} entries (one per flap position)")
            else:
                for i, s in enumerate(v):
                    c.text(_p(key, i), s, 16)
    fc = profile.get("flap_calls")
    if fc is not None:
        if not isinstance(fc, dict):
            c.err("flap_calls", "must be an object")
        else:
            for k, v in fc.items():
                if k not in {"abeam", "base", "tgo", "ga"}:
                    c.err(_p("flap_calls", k), "unknown key")
                elif not (isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < len(det) for x in v)):
                    c.err(_p("flap_calls", k), "must be a list of flap position numbers")

    lim = profile.get("limits")
    limv = {}
    if lim is not None:
        if not isinstance(lim, dict):
            c.err("limits", "must be an object")
        else:
            c.unknown_keys("limits", lim, set(LIMIT_KEYS))
            for k in LIMIT_KEYS:
                if k in lim and c.num(_p("limits", k), lim[k], 20, 400):
                    limv[k] = lim[k]
            order = [limv[k] for k in LIMIT_KEYS if k in limv]
            if order != sorted(order) or len(set(order)) != len(order):
                c.err("limits", "speeds must increase: vs0 < vs1 < vfe < vno < vne")

    tg = profile.get("targets", {})
    if not isinstance(tg, dict):
        c.err("targets", "must be an object")
        tg = {}
    for ph, t in tg.items():
        pp = _p("targets", ph)
        if ph not in PHASES:
            c.err(pp, f"unknown phase (allowed: {', '.join(PHASES)})")
            continue
        if not isinstance(t, dict):
            c.err(pp, "must be an object")
            continue
        c.unknown_keys(pp, t, TARGET_KEYS)
        ias = t.get("ias")
        if ias is not None:
            ok = isinstance(ias, list) and len(ias) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ias)
            if not ok or not 20 <= ias[0] < ias[1] <= 300:
                c.err(_p(pp, "ias"), "must be two speeds, 20 <= low < high <= 300")
            else:
                if ias[1] - ias[0] > 40:
                    c.warn(_p(pp, "ias"), "speed band wider than 40 knots: too loose to coach with")
                if "vs0" in limv and ias[0] < limv["vs0"] * 1.05:
                    c.err(_p(pp, "ias"), f"low end {ias[0]} is too close to the stall speed (vs0 {limv['vs0']})")
                if "vne" in limv and ias[1] > limv["vne"]:
                    c.err(_p(pp, "ias"), f"high end {ias[1]} is above vne {limv['vne']}")
                fl = t.get("flaps")
                if "vfe" in limv and isinstance(fl, list) and fl and all(isinstance(f, (int, float)) and f > 0 for f in fl) and ias[1] > limv["vfe"]:
                    c.err(_p(pp, "ias"), f"high end {ias[1]} is above the flap speed vfe {limv['vfe']}")
        fl = t.get("flaps")
        if fl is not None:
            if not (isinstance(fl, list) and fl and all(isinstance(v, (int, float)) and not isinstance(v, bool) and any(abs(v - d) < 0.5 for d in det) for v in fl)):
                c.err(_p(pp, "flaps"), f"must be flap positions the aircraft has: {det}")
        if "carb" in t and not isinstance(t["carb"], bool):
            c.err(_p(pp, "carb"), "must be true or false")
        if "track" in t:
            c.num(_p(pp, "track"), t["track"], 1, 45)
        for k in ("agl", "agl_min", "agl_max"):
            if k in t and t[k] not in AGL_WORDS:
                c.num(_p(pp, k), t[k], 50, 3000)
        for k in ("agl_lo_ft", "agl_hi_ft"):
            if k in t:
                c.num(_p(pp, k), t[k], 50, 3000)
        if "agl_lo_ft" in t and "agl_hi_ft" in t and isinstance(t["agl_lo_ft"], (int, float)) and isinstance(t["agl_hi_ft"], (int, float)) and t["agl_lo_ft"] >= t["agl_hi_ft"]:
            c.err(pp, "agl_lo_ft must be lower than agl_hi_ft")

    # speeds must make sense through the circuit (each step no faster than the one before, small tolerance)
    hi = {ph: tg[ph]["ias"][1] for ph in tg if isinstance(tg[ph], dict) and isinstance(tg[ph].get("ias"), list) and len(tg[ph]["ias"]) == 2
          and all(isinstance(v, (int, float)) for v in tg[ph]["ias"])}
    for a, b in (("DOWNWIND", "BASE"), ("BASE", "FINAL"), ("FINAL", "FINAL_SHORT")):
        if a in hi and b in hi and hi[b] > hi[a] + 5:
            c.err(_p("targets", b, "ias"), f"{b} is faster than {a} ({hi[b]} > {hi[a]} kt): check the numbers")
    if "rotate_kt" in profile and "CLIMB" in hi and isinstance(profile["rotate_kt"], (int, float)) and profile["rotate_kt"] >= hi["CLIMB"]:
        c.err("rotate_kt", "rotate speed must be below the climb speed band")
    if "vs1" in limv and isinstance(profile.get("rotate_kt"), (int, float)) and profile["rotate_kt"] < limv["vs1"]:
        c.err("rotate_kt", f"below the clean stall speed vs1 ({limv['vs1']})")

    safety_changed = baseline is None or any(_changed(profile.get(k), baseline.get(k)) for k in ("targets", "rotate_kt", "limits", "flap_detents"))
    _check_meta(c, profile.get("meta"), mode, safety_changed)
    if mode == "submit" and safety_changed and "meta" not in profile and not any(k in profile for k in ("_sources",)):
        c.err("meta", "speeds changed: add a meta block with the POH/AFM source and a change_note")
    return c.issues


def sanitize_aircraft(profile, filename_id=None):
    issues = check_aircraft(profile, filename_id=filename_id, mode="load")
    return _drop_bad(profile, issues), [i for i in issues if i.level == "error"]


# ---- language packs ------------------------------------------------------------------------------------------
_ALLOWED_TAG = re.compile(r"</?(fl|cb|tr|ck|rd)>")


def check_lang(obj, name="lang"):
    """Language packs hold text that is spoken and shown: plain text only (plus the topic tags <fl> <cb> <tr> <ck> <rd>)."""
    c = _Ctx()

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(k, str) or len(k) > 80 or _HIDDEN.search(k) or re.search(r"[<>`]", k):
                    c.err(_p(path, k), "odd key name")
                    continue
                walk(v, _p(path, k) if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, _p(path, i))
        elif isinstance(o, str):
            plain = _ALLOWED_TAG.sub("", o)
            if len(o) > 800:
                c.err(path, "text too long")
            elif _HIDDEN.search(o) or any(unicodedata.category(ch) in ("Cc", "Cf") for ch in o if ch not in "\n\t"):
                c.err(path, "contains control or hidden characters")
            elif re.search(r"[<`]|&#|javascript:|data:text|\bon[a-z]+\s*=", plain, re.I):
                c.err(path, "markup or script-like text is not allowed")
        elif not isinstance(o, (bool, int, float)) and o is not None:
            c.err(path, "unexpected value type")
    walk(obj, "")
    return c.issues


# ---- dataset (OurAirports) ----------------------------------------------------------------------------------
def load_dataset(data_dir):
    """{ICAO: {runway idents}} from OurAirports airports.csv + runways.csv; None if the files are missing."""
    ap, rw = os.path.join(data_dir, "airports.csv"), os.path.join(data_dir, "runways.csv")
    if not (os.path.exists(ap) and os.path.exists(rw)):
        return None
    ident_by_id = {}
    with open(ap, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ident_by_id[r["id"]] = (r.get("icao_code") or r.get("gps_code") or r["ident"] or "").upper()
    out = {v: set() for v in ident_by_id.values() if v}
    with open(rw, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            code = ident_by_id.get(r["airport_ref"])
            if code in out:
                for k in ("le_ident", "he_ident"):
                    if r.get(k):
                        out[code].add(r[k])
    return out


# ---- command line --------------------------------------------------------------------------------------------
def _scan(folder, base_folder, dataset, mode):
    report = []                                           # (file, issues)
    for kind in ("airports", "aircraft"):
        d = os.path.join(folder, kind)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            path = os.path.join(d, fn)
            obj, issues = load_file(path)
            stem = fn[:-5]
            if obj is not None:
                base = None
                if base_folder:
                    bp = os.path.join(base_folder, kind, fn)
                    if os.path.exists(bp):
                        base, _ = load_file(bp)
                if kind == "airports":
                    issues += check_airport(obj, filename_icao=stem, baseline=base, mode=mode, dataset=dataset)
                else:
                    issues += check_aircraft(obj, filename_id=stem, baseline=base, mode=mode)
            report.append((f"{kind}/{fn}", issues))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate Downwind Call profiles")
    ap.add_argument("folder", help="profiles folder to check")
    ap.add_argument("--base", help="trusted copy of the profiles folder (from the main branch) to compare with")
    ap.add_argument("--data", help="folder with OurAirports airports.csv and runways.csv")
    ap.add_argument("--mode", default="submit", choices=["submit", "local"])
    ap.add_argument("--json", help="write the result as JSON to this file")
    a = ap.parse_args(argv)
    dataset = load_dataset(a.data) if a.data else None
    if a.data and dataset is None:
        print(f"note: no OurAirports files in {a.data}: skipping runway cross-checks")
    report = _scan(a.folder, a.base, dataset, a.mode)
    errors = warns = 0
    for name, issues in report:
        for i in issues:
            tag = "ERROR" if i.level == "error" else "warn "
            print(f"{tag} {name}: {i.path or '(file)'}: {i.msg}")
            errors += i.level == "error"
            warns += i.level == "warn"
    langdir = os.path.join(os.path.dirname(os.path.abspath(a.folder)), "lang")
    for fn in sorted(os.listdir(langdir)) if os.path.isdir(langdir) else []:
        if fn.endswith(".json"):
            obj, iss = load_file(os.path.join(langdir, fn), 256 * 1024)
            if obj is not None:
                iss += check_lang(obj, fn)
            report.append((f"lang/{fn}", iss))
            for i in iss:
                print(f"ERROR lang/{fn}: {i.path or '(file)'}: {i.msg}")
                errors += 1
    print(f"\n{len(report)} files checked: {errors} errors, {warns} warnings")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([{"file": n, "issues": [i._asdict() for i in iss]} for n, iss in report], fh, indent=1)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

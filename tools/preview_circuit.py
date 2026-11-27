"""Draw the circuit of a profile as an SVG so a reviewer sees the shape at a glance (wrong side, too wide, too long).

    python tools/preview_circuit.py profiles/airports/EHLE.json --out previews
    python tools/preview_circuit.py new/EHLE.json --base old/EHLE.json --out previews     (grey = current, colour = proposed)
Same geometry as the app's map: x along the runway from the threshold, landing towards +x, left-hand circuit drawn above."""
import argparse
import csv
import html
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import validator as vd  # noqa: E402

DEFAULT_LEN_NM = 0.7


def pts(off, b, L):
    r = min(0.12, off / 4)
    out = []

    def corner(cx, cy, a0):
        for a in range(a0, a0 + 91, 15):
            out.append((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
    corner(L + b - r, r, -90)
    corner(L + b - r, off - r, 0)
    corner(-b + r, off - r, 90)
    corner(-b + r, r, 180)
    return out, r


def geo(profile):
    c = {**(profile.get("circuit") or {})}
    off = c.get("offset_nm") or 0.8
    b = c.get("base_along_nm") if c.get("base_along_nm") is not None else 0.5
    return off, b


def runway_lengths(data_dir, icao):
    out = {}
    path = os.path.join(data_dir or "", "runways.csv")
    if not data_dir or not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["airport_ident"] == icao and r.get("length_ft"):
                try:
                    out[r["le_ident"]] = out[r["he_ident"]] = float(r["length_ft"]) / 6076.12
                except ValueError:
                    pass
    return out


def svg_for(profile, rwy, side, L, base=None, base_side=None):
    W, H, TOP, BOT = 800, 420, 56, 50
    shapes = [(profile, "#58a6ff", False, side)] + ([(base, "#8b949e", True, base_side or side)] if base else [])
    xs, ys = [0.0, L], [0.0]
    for p, _c, _g, sd in shapes:
        off, b = geo(p)
        sg = -1 if sd == "right" else 1
        xs += [-b - 0.15, L + b + 0.15]
        ys += [sg * off]
    xmin, xmax = min(xs) - 0.1, max(xs) + 0.1
    ymin, ymax = min(ys) - 0.2, max(ys) + 0.2
    k = min((W - 40) / (xmax - xmin), (H - TOP - BOT) / (ymax - ymin))
    ox = (W - k * (xmax - xmin)) / 2
    px = lambda x: ox + (x - xmin) * k                                    # noqa: E731
    py = lambda y: TOP + (ymax - y) * k                                   # noqa: E731
    clamp = lambda x: max(12, min(W - 12, x))                             # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#0d1117"/>']
    head = f'{html.escape(str(profile.get("icao", "?")))} runway {html.escape(rwy)}: {"RIGHT" if side == "right" else "LEFT"}-hand circuit'
    if base and base_side and base_side != side:
        head += f'   (WAS {base_side.upper()}-HAND)'
    out.append(f'<text x="20" y="34" fill="{"#f0883e" if side == "right" else "#7ee787"}" font-size="22" font-weight="bold">{head}</text>')
    for p, col, ghost, sd in shapes:
        off, b = geo(p)
        sg = -1 if sd == "right" else 1
        pp, _r = pts(off, b, L)
        line = " ".join(f"{px(x):.1f},{py(sg * y):.1f}" for x, y in pp + [pp[0]])
        out.append(f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="{2 if ghost else 3}"' + (' stroke-dasharray="5 6"' if ghost else "") + "/>")
        if not ghost:
            out.append(f'<text x="{clamp(px(L / 2)):.0f}" y="{py(sg * off) + (-10 if sg > 0 else 20):.0f}" fill="{col}" font-size="14" text-anchor="middle">downwind offset {off} NM</text>')
            out.append(f'<text x="{clamp(px(-b)):.0f}" y="{py(0) + (-12 if sg < 0 else 28):.0f}" fill="{col}" font-size="13" text-anchor="{"start" if px(-b) < 160 else "middle"}">base turn {b} NM before threshold</text>')
    out.append(f'<rect x="{px(0):.1f}" y="{py(0.03):.1f}" width="{px(L) - px(0):.1f}" height="{py(-0.03) - py(0.03):.1f}" fill="#8b949e"/>')
    out.append(f'<text x="{px(L / 2):.0f}" y="{py(0) + 5:.0f}" fill="#0d1117" font-size="13" text-anchor="middle" font-weight="bold">RWY {html.escape(rwy)} \u2192 landing</text>')
    h = profile.get("circuit_agl_ft")
    out.append(f'<text x="20" y="{H - 16}" fill="#c9d1d9" font-size="14">circuit height {h} ft AGL \u00b7 runway {L:.2f} NM \u00b7 {"grey dashed = current, blue = proposed" if base else "proposed"}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--base", help="current profile to draw underneath")
    ap.add_argument("--data", help="folder with OurAirports runways.csv for true runway lengths")
    ap.add_argument("--out", default="previews")
    a = ap.parse_args(argv)
    prof, issues = vd.load_file(a.profile)
    if prof is None:
        print(issues[0].msg)
        return 1
    base = vd.load_file(a.base)[0] if a.base and os.path.exists(a.base) else None
    icao = prof.get("icao", "")
    lens = runway_lengths(a.data, icao)
    sides = prof.get("runway_sides") or {"": prof.get("default_side", "left")}
    os.makedirs(a.out, exist_ok=True)
    for rwy, side in sides.items():
        bside = ((base or {}).get("runway_sides") or {}).get(rwy)
        svg = svg_for(prof, rwy or "?", side, lens.get(rwy, DEFAULT_LEN_NM), base, bside)
        name = os.path.join(a.out, f"{icao}_{rwy or 'default'}.svg")
        with open(name, "w", encoding="utf-8") as fh:
            fh.write(svg)
        flag = f"  (was {bside})" if bside and bside != side else ""
        print(f"wrote {name}: runway {rwy} {side}-hand{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

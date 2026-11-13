#!/usr/bin/env python3
"""
The U.S. Economy, Sized by 2025 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of U.S. states built with popgrid: 1,000 squares, each one 0.1% of GDP.
Square count shows GDP, color shows GDP per person.

Setup:
    pip install "popgrid==0.7.1" pandas requests
    Google Chrome, Chromium or Edge must be installed (it renders the PNG).

Run:
    python make_map.py                 # rebuilds us-economy-by-gdp.png from the snapshot
    python make_map.py --refresh       # downloads current figures first
    python make_map.py --out map.png --scale 1

Data, committed so the published map stays reproducible:
    data.csv       BEA SAGDP2 GDP by state, current dollars; Census NST-EST2025 population
    imf_2025.csv   IMF NGDPD, GDP by country, current dollars (checks the comparisons)

Downloads (cached in .cache/ next to this script):
    npm    Playfair Display, Inter and Source Serif 4 (from Fontsource)
    With --refresh, also the BEA, Census and IMF source files.

BEA and the IMF revise their figures, so after --refresh check_claims() stops the
script if a sentence on the graphic stops being true. Pass --skip-checks to render anyway.
"""

import argparse
import html
import io
import json
import math
import os
import shutil
import subprocess
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
DATA_CSV = HERE / "data.csv"
IMF_CSV = HERE / "imf_2025.csv"

# Land area in square miles, from Census "State Area Measurements".
# https://www.census.gov/geographies/reference-files/2010/geo/state-area.html
LAND_ALASKA = 570_641
LAND_TEXAS = 261_232
LAND_US = 3_531_905

URLS = {
    "bea.zip": "https://apps.bea.gov/regional/zip/SAGDP.zip",
    "census.csv": "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/state/totals/NST-EST2025-ALLDATA.csv",
    "imf.json": "https://www.imf.org/external/datamapper/api/v1/NGDPD?periods=2025",
}

# Fontsource package -> weights and styles to extract
FONTS = {
    "playfair-display": ("Playfair Display", "5.3.0", [(700, "normal"), (800, "normal"), (900, "normal")]),
    "inter": ("Inter", "5.3.0", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal")]),
    "source-serif-4": ("Source Serif 4", "5.3.1", [(700, "normal")]),
}

ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
STATE = {v: k for k, v in ABBR.items()}


# DOWNLOADS

def fetch(name):
    """Download a source file once and return its cached path."""
    path = CACHE / name
    if not path.exists():
        print(f"Downloading {name}")
        response = requests.get(URLS[name], timeout=180)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def fetch_fonts():
    """Extract the needed woff2 files from the Fontsource npm tarballs."""
    folder = CACHE / "fonts"
    folder.mkdir(exist_ok=True)
    css = []
    for package, (family, version, faces) in FONTS.items():
        wanted = {f"{package}-latin-{weight}-{style}.woff2": (weight, style) for weight, style in faces}
        if not all((folder / name).exists() for name in wanted):
            print(f"Downloading font {family}")
            url = f"https://registry.npmjs.org/@fontsource/{package}/-/{package}-{version}.tgz"
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
                for member in archive.getmembers():
                    base = Path(member.name).name
                    if base in wanted and member.isfile():
                        (folder / base).write_bytes(archive.extractfile(member).read())
        for name, (weight, style) in wanted.items():
            css.append(
                f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:{style};"
                f"src:url({(folder / name).as_uri()})}}"
            )
    return "".join(css)


# DATA

def load_states(refresh=False):
    """
    GDP (BEA, 2025) and population (Census, 2025) for the 50 states and D.C.

    Reads the snapshot in data.csv. With refresh=True it downloads the current
    figures and overwrites the snapshot.
    """
    if DATA_CSV.exists() and not refresh:
        states = pd.read_csv(DATA_CSV)
        states["gdp"] = states.gdp.astype(float)
        states["share"] = states.gdp / states.gdp.sum()
        states["per_person"] = states.gdp / states["pop"]
        return states.sort_values("gdp", ascending=False).reset_index(drop=True)

    with zipfile.ZipFile(fetch("bea.zip")) as archive:
        name = next(n for n in archive.namelist() if n.startswith("SAGDP2__ALL"))
        bea = pd.read_csv(archive.open(name), encoding="latin1", low_memory=False)
    total = bea[(bea.LineCode == 1) & bea.Unit.str.contains("Millions of current")].copy()
    total["gdp"] = pd.to_numeric(total["2025"]) * 1e6

    census = pd.read_csv(fetch("census.csv"))
    census = census[census.SUMLEV == 40][["NAME", "POPESTIMATE2025"]]
    census.columns = ["state", "pop"]

    # The inner merge drops BEA's regions, and the isin drops Puerto Rico
    states = total.rename(columns={"GeoName": "state"})[["state", "gdp"]].merge(census, on="state")
    states = states[states.state.isin(ABBR)].sort_values("gdp", ascending=False).reset_index(drop=True)
    states.assign(gdp=states.gdp.round().astype("int64"))[["state", "gdp", "pop"]].to_csv(DATA_CSV, index=False)
    states["share"] = states.gdp / states.gdp.sum()
    states["per_person"] = states.gdp / states["pop"]
    return states


def build_grids(states):
    """
    Run popgrid and return the squares as {"gdp": [...], "land": [...]},
    with one [state, column, row] list per landmass.

    popgrid 0.7.1 reads GDP from its bundled table (modelled PPP figures), so the
    official BEA values are swapped in at the point where PopGrid loads its weights.
    """
    import popgrid.core as core
    from popgrid import AreaGrid, PopGrid

    official = dict(zip(states.state, states.gdp.astype(float)))
    bundled = core.load_region_values

    def bea_values(iso3, names, by="population", name_to_parent=None):
        if by == "gdp":
            return {name: official[name] for name in names}
        return bundled(iso3, names, by=by, name_to_parent=name_to_parent)

    core.load_region_values = bea_values
    try:
        gdp_grid = PopGrid("USA", n=1000, by="gdp").build()
        land_grid = AreaGrid("USA", n=1000).build()
    finally:
        core.load_region_values = bundled

    def squares(grid):
        out = []
        for landmass in grid._landmasses:
            cells = landmass["cells"]
            if cells is None or len(cells) == 0:
                continue
            bounds = cells.geometry.bounds
            size = grid._cell_size
            out.append([
                [name, round(x / size), round(y / size)]
                for name, x, y in zip(cells.region_name, bounds.minx, bounds.miny)
            ])
        return out

    return {"gdp": squares(gdp_grid), "land": squares(land_grid)}


def load_imf(refresh=False):
    """2025 GDP by country in USD billions, from the snapshot in imf_2025.csv."""
    if not IMF_CSV.exists() or refresh:
        raw = json.loads(fetch("imf.json").read_text())["values"]["NGDPD"]
        rows = [(code, v["2025"]) for code, v in raw.items() if "2025" in v and len(code) == 3]
        table = pd.DataFrame(rows, columns=["code", "gdp_2025_usd_billions"]).sort_values("code")
        table.to_csv(IMF_CSV, index=False)
    table = pd.read_csv(IMF_CSV, keep_default_na=False)
    return dict(zip(table.code, table.gdp_2025_usd_billions.astype(float)))


def check_claims(states, grids, refresh=False):
    """Recompute every claim printed on the graphic."""
    by_state = states.set_index("state")
    gdp = by_state.gdp
    total = states.gdp.sum()
    us_per_person = total / states["pop"].sum()
    imf_table = load_imf(refresh)
    imf = lambda code: imf_table[code] * 1e9
    squares = Counter(s[0] for landmass in grids["gdp"] for s in landmass)
    smallest_25 = states.iloc[-25:]
    second_highest = sorted(states.per_person)[-2]

    checks = {
        "50 states plus D.C.": len(states) == 51,
        "1,000 squares": sum(squares.values()) == 1000,
        "every state within one square of its share": all(
            abs(squares[r.state] - r.share * 1000) < 1 for r in states.itertuples()
        ),
        "a square is about $31B": 30 < total / 1000 / 1e9 < 31,
        "combined GDP is $30.7T": round(total / 1e12, 1) == 30.7,
        "Alaska has two squares": squares["Alaska"] == 2,
        "Hawaii has four squares": squares["Hawaii"] == 4,
        "eight states are over half": states.share[:8].sum() > 0.5 > states.share[:7].sum(),
        "top four are 37%": round(states.share[:4].sum() * 100) == 37
        and set(states.state[:4]) == {"California", "Texas", "New York", "Florida"},
        "California beats 24 smallest states plus D.C.": smallest_25.gdp.sum() < gdp["California"]
        and "District of Columbia" in set(smallest_25.state),
        "California is under $4.35T, printed as $4.3T": 4.25e12 <= gdp["California"] < 4.35e12,
        "California above U.K. and India": gdp["California"] > max(imf("GBR"), imf("IND")),
        "Texas above Italy and Russia": gdp["Texas"] > max(imf("ITA"), imf("RUS")),
        "New York above Canada and Brazil": gdp["New York"] > max(imf("CAN"), imf("BRA")),
        "Florida above Turkiye": gdp["Florida"] > imf("TUR"),
        "Alaska is 16% of land": round(LAND_ALASKA / LAND_US * 100) == 16,
        "Alaska is the largest state by land": LAND_ALASKA > LAND_TEXAS,
        "Alaska is 0.2% of GDP": round(by_state.share["Alaska"] * 100, 1) == 0.2,
        "D.C. is highest per person": by_state.per_person.idxmax() == "District of Columbia",
        "D.C. is $278K per person": round(by_state.per_person["District of Columbia"] / 1e3) == 278,
        "D.C. is about 3x the U.S.": 2.9 < by_state.per_person["District of Columbia"] / us_per_person < 3.2,
        "U.S. is $90K per person": round(us_per_person / 1e3) == 90,
        "all states fit the $55K to $125K scale": states.per_person.min() >= PC_LOW and second_highest <= PC_HIGH,
    }
    failed = [claim for claim, passed in checks.items() if not passed]
    print(f"Claims checked: {len(checks) - len(failed)} of {len(checks)} pass")
    if failed:
        raise SystemExit("These claims no longer hold:\n  " + "\n  ".join(failed))


# DESIGN

WIDTH, HEIGHT = 1200, 1160
CELL = 19               # size of a square, in px
MAP_X, MAP_Y = 26, 222  # top-left corner of the main map
INK = "#0C1F1A"
MUTED = "#51625C"
RAMP = ["#E9F4E4", "#B9DFB4", "#7CC394", "#3B9C74", "#14705A", "#0A4A40", "#052B26"]
PC_LOW, PC_HIGH = 55_000, 125_000


def X(col):
    return MAP_X + (col + 32) * CELL


def Y(row):
    """Top edge of a row. Rows count upward, as in the projected map."""
    return MAP_Y + (15 - row) * CELL


def stair(col, y):
    """Leader that rises from a square, then turns right into its label."""
    return dict(out=(X(col) + 9.5 + 14, y), up=True)


STAIR_TOP, STAIR_STEP = Y(9) - 104, 20

# Label settings per state.
#   at + out  draw a leader from that square to a label outside the state
#   up        make the leader an elbow instead of a straight line
#   skip      no label here (D.C. has its magnifier)
#   rect      place the label in these cells: (col, row, width, height)
#   dx, dy    nudge; v sets the value's font size
LABELS = {
    "ID": dict(at=(-19, 9), **stair(-19, STAIR_TOP)),
    "WY": dict(at=(-18, 7), **stair(-18, STAIR_TOP + STAIR_STEP)),
    "MT": dict(at=(-16, 8), **stair(-16, STAIR_TOP + 2 * STAIR_STEP)),
    "SD": dict(at=(-15, 7), **stair(-15, STAIR_TOP + 3 * STAIR_STEP)),
    "NE": dict(at=(-14, 5), **stair(-14, STAIR_TOP + 4 * STAIR_STEP)),
    "ND": dict(at=(-13, 7), **stair(-13, STAIR_TOP + 5 * STAIR_STEP)),
    "NM": dict(at=(-18, -2), out=(X(-18) + 9, Y(-6) + 2), anchor="end"),
    "AR": dict(at=(-5, -6), out=(X(-2) - 8, Y(-15) + 2)),
    "MS": dict(at=(-4, -9), out=(X(-2) - 8, Y(-13) + 8)),
    "WV": dict(at=(5, -1), out=(X(5) - 5, Y(9) + 6), up=True, anchor="end"),
    "DE": dict(at=(15, -3), out=(X(18) + 2, Y(-3) + 9)),
    "VT": dict(at=(18, 12), out=(X(14) + 4, Y(15) + 6)),
    "NH": dict(at=(19, 11), out=(X(18) + 6, Y(15) - 12)),
    "ME": dict(at=(20, 13), out=(X(22) + 6, Y(14) - 6)),
    "RI": dict(at=(23, 5), out=(X(24) + 8, Y(6) - 2)),
    "DC": dict(skip=True),
    "AZ": dict(dx=-5, dy=4),
    "LA": dict(dx=-3, dy=8, v=11.5),
    "FL": dict(rect=(6, -18, 6, 6), dx=2),
}

# The four largest economies get a larger label and a country comparison
HERO = {
    "CA": dict(name=24, value=40, note=["Bigger than the", "U.K. or India"]),
    "TX": dict(name=21, value=33, note=["Bigger than Italy or Russia"]),
    "NY": dict(name=21, value=33, note=["Bigger than Canada or Brazil"]),
    "FL": dict(name=18, value=26, note=["Bigger than", "Türkiye"]),
}


# SVG HELPERS

def to_rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(to_rgb(a), to_rgb(b)))


def scale_color(per_person):
    position = max(0, min(1, (per_person - PC_LOW) / (PC_HIGH - PC_LOW))) * (len(RAMP) - 1)
    i = min(int(position), len(RAMP) - 2)
    return mix(RAMP[i], RAMP[i + 1], position - i)


def luminance(color):
    channels = [v / 255 for v in to_rgb(color)]
    channels = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def text_color(background):
    """White or ink, whichever has more contrast against the background."""
    on_white = 1.05 / (luminance(background) + 0.05)
    on_ink = (luminance(background) + 0.05) / (luminance(INK) + 0.05)
    return "#ffffff" if on_white > on_ink else INK


def money(value):
    return f"${value / 1e12:.2f}T" if value >= 1e12 else f"${value / 1e9:.0f}B"


def text(x, y, content, size, weight=700, family="sans", fill=INK, anchor="middle",
         extra="", halo=None, halo_width=3.5):
    """SVG text. A halo in the square's color cuts the grid lines behind the label."""
    face = {"sans": "Inter", "serif": "Playfair Display", "num": "Source Serif 4"}[family]
    if halo:
        extra += f' paint-order="stroke" stroke="{halo}" stroke-width="{halo_width}" stroke-linejoin="round"'
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{face}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}" style="font-feature-settings:\'lnum\' 1" {extra}>'
        f"{html.escape(content)}</text>"
    )


def lines(x, y, rows, size, height, weight=500, family="sans", fill=INK, anchor="start"):
    return "".join(text(x, y + i * height, row, size, weight, family, fill, anchor) for i, row in enumerate(rows))


def leader(x1, y1, path_tail):
    """Thin line with a white casing and a dot on the state."""
    d = f"M{x1:.1f} {y1:.1f}{path_tail}"
    return (
        f'<path d="{d}" fill="none" stroke="#fff" stroke-width="2.6" stroke-opacity=".85"/>'
        f'<path d="{d}" fill="none" stroke="{INK}" stroke-width=".9"/>'
        f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="2.2" fill="#fff" stroke="{INK}" stroke-width=".9"/>'
    )


def draw_squares(cells, size, origin_x, origin_y, fill_of, gap=0.6, radius=1.2, border=1.7, tint=0.30):
    """Draw {(col, row): state} as squares, with an outline around each state."""
    px = lambda col: origin_x + col * size
    py = lambda row: origin_y - (row + 1) * size
    out = []
    for (col, row), state in cells.items():
        fill = fill_of(state)
        out.append(
            f'<rect x="{px(col):.2f}" y="{py(row):.2f}" width="{size}" height="{size}" fill="{mix(fill, "#ffffff", tint)}"/>'
            f'<rect x="{px(col) + gap:.2f}" y="{py(row) + gap:.2f}" width="{size - 2 * gap:.2f}" '
            f'height="{size - 2 * gap:.2f}" rx="{radius}" fill="{fill}"/>'
        )
    edges = []
    for (col, row), state in cells.items():
        x, y = px(col), py(row)
        if cells.get((col - 1, row)) != state:
            edges.append(f"M{x:.2f} {y:.2f}V{y + size:.2f}")
        if cells.get((col + 1, row)) != state:
            edges.append(f"M{x + size:.2f} {y:.2f}V{y + size:.2f}")
        if cells.get((col, row + 1)) != state:
            edges.append(f"M{x:.2f} {y:.2f}H{x + size:.2f}")
        if cells.get((col, row - 1)) != state:
            edges.append(f"M{x:.2f} {y + size:.2f}H{x + size:.2f}")
    out.append(f'<path d="{"".join(edges)}" fill="none" stroke="{INK}" stroke-width="{border}" stroke-linecap="square"/>')
    return "".join(out)


def largest_rectangle(cells):
    """Largest, squarest rectangle inside a state: (col, row, width, height)."""
    best = (0, 0, 0, 0, 0)
    for col, row in sorted(cells):
        for height in range(1, 16):
            if (col, row + height - 1) not in cells:
                break
            width = 1
            while all((col + width, row + k) in cells for k in range(height)):
                width += 1
            score = width * height - abs(width - height) * 0.15
            if score > best[0]:
                best = (score, col, row, width, height)
    return best[1:]


# THE PAGE

def state_labels(main, data, origin_x, origin_y):
    out = []
    px = lambda col: origin_x + col * CELL
    py = lambda row: origin_y - (row + 1) * CELL
    cells_of = defaultdict(set)
    for cell, state in main.items():
        cells_of[state].add(cell)

    for state, cells in cells_of.items():
        abbr = ABBR[state]
        conf = LABELS.get(abbr, {})
        if conf.get("out") or conf.get("skip"):
            continue
        col, row, w, h = conf.get("rect") or largest_rectangle(cells)
        cx = px(col) + w * CELL / 2 + conf.get("dx", 0)
        cy = py(row + h - 1) + h * CELL / 2 + conf.get("dy", 0)
        fill = scale_color(data[state].per_person)
        ink = text_color(fill)
        value = money(data[state].gdp)

        if abbr in HERO:
            hero = HERO[abbr]
            size = hero["value"]
            out.append(text(cx, cy - size * 0.62, state, hero["name"], 800, "serif", ink, halo=fill, halo_width=5))
            out.append(text(cx, cy + size * 0.5, value, size, 900, "serif", ink, halo=fill, halo_width=6))
            for i, note in enumerate(hero["note"]):
                out.append(text(cx, cy + size * 0.5 + 17 + i * 14.5, note, 11.5, 600, "sans", ink, halo=fill, halo_width=4))
            continue

        v_size, n_size = conf.get("v", 12.5), 9.5
        if h * CELL < 34 and w * CELL >= 56:  # one line, for long thin states
            value_w = len(value) * v_size * 0.53
            name_w = len(abbr) * n_size * 0.68
            x0 = cx - (name_w + 4 + value_w) / 2
            out.append(text(x0, cy + n_size * 0.36, abbr, n_size, 600, "sans", ink, "start", halo=fill))
            out.append(text(x0 + name_w + 4, cy + v_size * 0.34, value, v_size, 700, "num", ink, "start", halo=fill))
        else:
            out.append(text(cx, cy - v_size * 0.30, abbr, n_size, 600, "sans", ink, halo=fill))
            out.append(text(cx, cy + v_size * 0.78, value, v_size, 700, "num", ink, halo=fill))

    for abbr, conf in LABELS.items():
        if not conf.get("out"):
            continue
        value = money(data[STATE[abbr]].gdp)
        x, y = conf["out"]
        col, row = conf["at"]
        tail = f"V{y:.1f}H{x:.1f}" if conf.get("up") else f"L{x:.1f} {y:.1f}"
        out.append(leader(px(col) + CELL / 2, py(row) + CELL / 2, tail))
        if conf.get("anchor") == "end":
            width = len(value) * 12.5 * 0.54
            out.append(text(x - 4, y + 4.6, value, 12.5, 700, "num", anchor="end"))
            out.append(text(x - 4 - width - 5, y + 4.2, abbr, 10.5, 700, "sans", anchor="end"))
        else:
            out.append(text(x + 4, y + 4.2, abbr, 10.5, 700, "sans", anchor="start"))
            out.append(text(x + 26, y + 4.6, value, 12.5, 700, "num", anchor="start"))
    return "".join(out)


def header(us_per_person):
    out = [
        text(24, 122, "The U.S. Economy", 136, 900, "serif", INK, "start", 'letter-spacing="-2.4"'),
        text(1174, 180, "Sized by 2025 GDP, Not Land", 44, 700, "serif", INK, "end", 'letter-spacing="-.4"'),
    ]
    deck_x = 486
    out.append(lines(deck_x, 214, [
        "Each square is 0.1% of U.S. GDP, about $31 billion.",
        "Eight states produce over half of it, while Alaska,",
        "the largest state by land, earns just two squares.",
    ], 14, 19.5))

    x, y, width = deck_x + 34, 296, 272
    stops = "".join(f'<stop offset="{i / (len(RAMP) - 1)}" stop-color="{c}"/>' for i, c in enumerate(RAMP))
    out.append(
        f'<defs><linearGradient id="legend">{stops}</linearGradient></defs>'
        f'<rect x="{x}" y="{y}" width="{width}" height="11" rx="2" fill="url(#legend)"/>'
    )
    out.append(text(x, y - 6, "Color: GDP per person, 2025", 11, 600, "sans", INK, "start"))
    out.append(text(x - 5, y + 10, "$55K", 11.5, 700, "num", INK, "end"))
    out.append(text(x + width + 5, y + 10, "$125K", 11.5, 700, "num", INK, "start"))
    tick = (us_per_person - PC_LOW) / (PC_HIGH - PC_LOW) * width
    out.append(f'<path d="M{x + tick:.1f} {y - 2}V{y + 13}" stroke="{INK}" stroke-width="1.4"/>')
    out.append(text(x + tick, y + 25, "U.S. $90K", 10, 600, "sans", MUTED))
    return "".join(out)


def dc_magnifier(main, data, fill_of):
    cx, cy, radius = 1066, 668, 66
    start_x, start_y = X(11) + 9.5, Y(-3) + 9.5
    angle = math.atan2(cy - start_y, cx - start_x)
    end_x, end_y = cx - radius * math.cos(angle), cy - radius * math.sin(angle)
    out = [
        leader(start_x, start_y, f"L{end_x:.1f} {end_y:.1f}"),
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="#fff" stroke="{INK}" stroke-width="1.6"/>',
    ]
    dc = {cell: s for cell, s in main.items() if s == "District of Columbia"}
    cols = [c for c, _ in dc]
    rows = [r for _, r in dc]
    size = 11
    block_w = (max(cols) - min(cols) + 1) * size
    block_h = (max(rows) - min(rows) + 1) * size
    out.append(draw_squares(
        dc, size, cx - block_w / 2 - min(cols) * size, cy - 6 + block_h / 2 + min(rows) * size,
        fill_of, gap=0.5, border=1.2,
    ))
    out.append(text(cx, cy - 40, "D.C.", 12, 700))
    out.append(text(cx, cy + 44, money(data["District of Columbia"].gdp), 21, 900, "serif"))
    out.append(lines(cx, cy + radius + 18, [
        "Highest GDP per person:",
        "$278K, or 3x the U.S. average",
    ], 11.5, 14.5, weight=600, anchor="middle"))
    return "".join(out)


def stats():
    x = 906
    return "".join([
        text(x, 842, "37%", 54, 900, "serif", INK, "start", 'letter-spacing="-1"'),
        lines(x + 114, 811, ["of U.S. GDP comes from", "four states: California,", "Texas, New York, Florida"], 12.5, 17),
        f'<path d="M{x} 866H1174" stroke="{INK}" stroke-width=".8"/>',
        text(x, 920, "$4.3T", 46, 900, "serif", INK, "start", 'letter-spacing="-.6"'),
        lines(x + 132, 889, ["California out-produces", "the 24 smallest states", "plus D.C. combined"], 12.5, 17),
    ])


def alaska_and_hawaii(data, fill_of):
    """Both states at map scale, then the note whose arrow points into the land map."""
    x, y = 44, 682
    alaska = {(0, 0): "Alaska", (0, 1): "Alaska"}
    hawaii = {(0, i): "Hawaii" for i in range(4)}
    out = [
        draw_squares(alaska, CELL, x, y + 2 * CELL, fill_of),
        text(x + CELL + 8, y + 13, "Alaska", 11.5, 700, "sans", INK, "start"),
        text(x + CELL + 8, y + 31, money(data["Alaska"].gdp), 15, 700, "num", INK, "start"),
        draw_squares(hawaii, CELL, x + 108, y + 4 * CELL, fill_of),
        text(x + 108 + CELL + 8, y + 13, "Hawaii", 11.5, 700, "sans", INK, "start"),
        text(x + 108 + CELL + 8, y + 31, money(data["Hawaii"].gdp), 15, 700, "num", INK, "start"),
        lines(44, 812, ["Alaska has 16% of", "U.S. land, but only", "0.2% of its GDP"], 22, 27, weight=800, family="serif"),
        f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M0 0L10 5L0 10" fill="none" stroke="{INK}" stroke-width="1.6"/></marker></defs>'
        f'<path d="M150 884C170 930 240 948 332 934" fill="none" stroke="{INK}" stroke-width="1.4" marker-end="url(#arrow)"/>',
    ]
    return "".join(out)


def land_inset(grids, fill_of):
    """popgrid's AreaGrid in the same colors, with Alaska and Hawaii moved in close."""
    land = {}
    for landmass in grids["land"]:
        for state, col, row in landmass:
            if state == "Alaska":
                land[(col - 15, row - 36)] = state
            elif state == "Hawaii":
                land[(col + 36, round(row / 3) - 16)] = state
            else:
                land[(col, row)] = state
    return draw_squares(land, 5.4, 642, 948, fill_of, gap=0, radius=0, border=0.75, tint=0) + text(
        345, 846, "The Same States, Sized by Land", 18, 800, "serif", INK, "start"
    )


def footer():
    return "".join([
        f'<path d="M26 1066H1174" stroke="{INK}" stroke-width="1"/>',
        lines(26, 1086, [
            "Source: U.S. Bureau of Economic Analysis (2025 GDP by state, current dollars); U.S. Census Bureau (Vintage 2025 population; land area);",
            "IMF World Economic Outlook (2025 GDP by country, current dollars). Squares are whole-number shares (largest remainder method) of the combined",
            "GDP of the 50 states and D.C., $30.7T. D.C. is shown at the top of the color scale.",
        ], 10.5, 14.5, weight=400, fill=MUTED),
        lines(1174, 1086, ["Josep Ferrer", "databites.tech", "Grid built with popgrid"], 11, 14.5, weight=600, anchor="end"),
    ])


def build_page(states, grids, font_css):
    data = {r.state: r for r in states.itertuples()}
    fill_of = lambda state: scale_color(data[state].per_person)
    us_per_person = states.gdp.sum() / states["pop"].sum()

    main = {(col, row): state for state, col, row in grids["gdp"][0]}
    origin_x = MAP_X + 32 * CELL
    origin_y = MAP_Y + 16 * CELL

    svg = "".join([
        header(us_per_person),
        draw_squares(main, CELL, origin_x, origin_y, fill_of),
        state_labels(main, data, origin_x, origin_y),
        dc_magnifier(main, data, fill_of),
        stats(),
        alaska_and_hawaii(data, fill_of),
        land_inset(grids, fill_of),
        footer(),
    ])
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{font_css}'
        "*{margin:0;padding:0}body{background:#fff}text{font-kerning:normal}</style></head><body>"
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">{svg}</svg>'
        "</body></html>"
    )


def find_chrome():
    """Locate an installed Chrome, Chromium or Edge."""
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(path).exists():
            return path
    raise SystemExit("Chrome not found. Install it, or set the CHROME variable to its path.")


def screenshot(page_html, out, scale):
    """Open the page in headless Chrome and save it as a PNG."""
    page_path = CACHE / "map.html"
    page_path.write_text(page_html, encoding="utf-8")
    out = Path(out).resolve()
    command = [
        find_chrome(), "--headless", "--disable-gpu", "--hide-scrollbars",
        f"--window-size={WIDTH},{HEIGHT}", f"--force-device-scale-factor={scale}",
        "--virtual-time-budget=5000", f"--screenshot={out}", page_path.as_uri(),
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # Chrome refuses to run sandboxed as root
        command.insert(1, "--no-sandbox")
    subprocess.run(command, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default=str(HERE / "us-economy-by-gdp.png"))
    parser.add_argument("--scale", type=float, default=2, help="pixel density; 2 gives 2400 x 2320")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="download current BEA, Census and IMF figures")
    args = parser.parse_args()

    CACHE.mkdir(exist_ok=True)
    states = load_states(args.refresh)
    grids = build_grids(states)
    if not args.skip_checks:
        check_claims(states, grids, args.refresh)
    page_html = build_page(states, grids, fetch_fonts())
    screenshot(page_html, args.out, args.scale)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()

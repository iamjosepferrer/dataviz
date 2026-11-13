#!/usr/bin/env python3
"""
Europe's Economy, Sized by 2025 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of 40 European countries built with popgrid: 1,000 squares, each one
0.1% of their combined GDP. Square count shows GDP, color shows GDP per person,
gold below the European average and blue above it. All amounts are in euros, converted
from the IMF's U.S. dollar figures at the ECB's average exchange rate for 2025.

Setup:
    pip install -r requirements.txt
    Google Chrome, Chromium or Edge must be installed (it renders the PNG).

Run:
    python make_map.py                 # rebuilds europe-economy-by-gdp.png from the snapshot
    python make_map.py --refresh       # downloads current figures first
    python make_map.py --out map.png --scale 1

Data, committed so the published map stays reproducible:
    data.csv       IMF GDP in U.S. dollars and population, World Bank land area, by country
    imf_2025.csv   IMF GDP for every country (checks the comparisons)

Downloads (cached in .cache/ next to this script):
    Natural Earth boundaries, through popgrid
    npm    Playfair Display, Inter and Source Serif 4 (from Fontsource)
    With --refresh, also the IMF, World Bank and ECB source files.

The IMF revises its figures, so after --refresh check_claims() stops the script
if a sentence on the graphic stops being true. Pass --skip-checks to render anyway.
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
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
DATA_CSV = HERE / "data.csv"
IMF_CSV = HERE / "imf_2025.csv"

URLS = {
    "imf_gdp.json": "https://www.imf.org/external/datamapper/api/v1/NGDPD?periods=2025",
    "imf_pop.json": "https://www.imf.org/external/datamapper/api/v1/LP?periods=2025",
    "wb_land.json": "https://api.worldbank.org/v2/country/all/indicator/AG.LND.TOTL.K2?format=json&per_page=400&date=2021",
    "ecb_rate.csv": "https://data-api.ecb.europa.eu/service/data/EXR/A.USD.EUR.SP00.A?startPeriod=2025&endPeriod=2025&format=csvdata",
}

# ECB reference rate, 2025 annual average: U.S. dollars per euro.
USD_PER_EUR = 1.129983

FONTS = {
    "playfair-display": ("Playfair Display", "5.3.0", [(700, "normal"), (800, "normal"), (900, "normal")]),
    "inter": ("Inter", "5.3.0", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal")]),
    "source-serif-4": ("Source Serif 4", "5.3.1", [(700, "normal")]),
}

# Natural Earth code -> (IMF code, name on the graphic).
# Editorial scope: Russia and Turkiye are out, because their land area would swamp the
# outline and most of it lies outside Europe. Microstates are out because the IMF does
# not cover them all.
COUNTRIES = {
    "ALB": ("ALB", "Albania"), "AUT": ("AUT", "Austria"), "BEL": ("BEL", "Belgium"),
    "BGR": ("BGR", "Bulgaria"), "BIH": ("BIH", "Bosnia"), "BLR": ("BLR", "Belarus"),
    "CHE": ("CHE", "Switzerland"), "CYP": ("CYP", "Cyprus"), "CZE": ("CZE", "Czechia"),
    "DEU": ("DEU", "Germany"), "DNK": ("DNK", "Denmark"), "ESP": ("ESP", "Spain"),
    "EST": ("EST", "Estonia"), "FIN": ("FIN", "Finland"), "FRA": ("FRA", "France"),
    "GBR": ("GBR", "U.K."), "GRC": ("GRC", "Greece"), "HRV": ("HRV", "Croatia"),
    "HUN": ("HUN", "Hungary"), "IRL": ("IRL", "Ireland"), "ISL": ("ISL", "Iceland"),
    "ITA": ("ITA", "Italy"), "KOS": ("UVK", "Kosovo"), "LTU": ("LTU", "Lithuania"),
    "LUX": ("LUX", "Luxembourg"), "LVA": ("LVA", "Latvia"), "MDA": ("MDA", "Moldova"),
    "MKD": ("MKD", "N. Macedonia"), "MLT": ("MLT", "Malta"), "MNE": ("MNE", "Montenegro"),
    "NLD": ("NLD", "Netherlands"), "NOR": ("NOR", "Norway"), "POL": ("POL", "Poland"),
    "PRT": ("PRT", "Portugal"), "ROU": ("ROU", "Romania"), "SRB": ("SRB", "Serbia"),
    "SVK": ("SVK", "Slovakia"), "SVN": ("SVN", "Slovenia"), "SWE": ("SWE", "Sweden"),
    "UKR": ("UKR", "Ukraine"),
}

# Natural Earth files these under another code, so they are moved to the country
# they belong to. Crimea and Sevastopol are internationally recognized as Ukraine.
REASSIGN_UNITS = {"Crimea": "UKR", "Sevastopol": "UKR"}
REASSIGN_CODES = {"ALD": "FIN", "CYN": "CYP", "WSB": "CYP", "ESB": "CYP"}

# Keeps Europe and its nearby islands, dropping overseas territories and Svalbard
BBOX = (-25, 34, 45, 72)


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

def load_countries(refresh=False):
    """GDP in euros and population (IMF, 2025) and land area (World Bank) by country."""
    if DATA_CSV.exists() and not refresh:
        table = pd.read_csv(DATA_CSV, keep_default_na=False, na_values=[""])
    else:
        gdp = json.loads(fetch("imf_gdp.json").read_text())["values"]["NGDPD"]
        pop = json.loads(fetch("imf_pop.json").read_text())["values"]["LP"]
        land = json.loads(fetch("wb_land.json").read_text())[1]
        land = {row["countryiso3code"]: row["value"] for row in land}
        rows = []
        for code, (imf, name) in COUNTRIES.items():
            rows.append(dict(
                code=code, name=name,
                gdp_usd=round(gdp[imf]["2025"] * 1e9),
                pop=round(pop[imf]["2025"] * 1e6),
                land_km2=land.get(code),  # the World Bank has no figure for Kosovo
            ))
        table = pd.DataFrame(rows)
        table.to_csv(DATA_CSV, index=False)
        ecb = pd.read_csv(fetch("ecb_rate.csv")).OBS_VALUE.iloc[0]
        if abs(ecb - USD_PER_EUR) > 1e-6:
            raise SystemExit(f"The ECB rate is now {ecb}. Update USD_PER_EUR and the footer.")
    table["gdp"] = table.gdp_usd.astype(float) / USD_PER_EUR
    table["share"] = table.gdp / table.gdp.sum()
    table["per_person"] = table.gdp / table["pop"]
    return table.sort_values("gdp", ascending=False).reset_index(drop=True)


def load_imf(refresh=False):
    """2025 GDP by country in USD billions, from the snapshot in imf_2025.csv."""
    if not IMF_CSV.exists() or refresh:
        raw = json.loads(fetch("imf_gdp.json").read_text())["values"]["NGDPD"]
        rows = [(code, v["2025"]) for code, v in raw.items() if "2025" in v and len(code) == 3]
        table = pd.DataFrame(rows, columns=["code", "gdp_2025_usd_billions"]).sort_values("code")
        table.to_csv(IMF_CSV, index=False)
    table = pd.read_csv(IMF_CSV, keep_default_na=False)
    return dict(zip(table.code, table.gdp_2025_usd_billions.astype(float)))


def load_shapes(countries):
    """Dissolve Natural Earth admin-1 units (popgrid's cached file) into countries."""
    from popgrid.data import _download_admin1

    units = gpd.read_file(_download_admin1(), columns=["adm0_a3", "name"])
    for unit, code in REASSIGN_UNITS.items():
        units.loc[units.name == unit, "adm0_a3"] = code
    units["adm0_a3"] = units.adm0_a3.replace(REASSIGN_CODES)
    units = units[units.adm0_a3.isin(countries.code)].clip(BBOX)
    shapes = units.dissolve(by="adm0_a3").reset_index()[["adm0_a3", "geometry"]]
    return shapes.merge(countries[["code", "gdp"]], left_on="adm0_a3", right_on="code")


def repair_quotas(gdp_landmasses, quotas):
    """
    popgrid 0.7.1 can give a very small region no square while its neighbor gets one
    too many (here Bosnia and North Macedonia). Hand the extra square to the region
    that is short, taking the one closest to it in the west of the donor.
    """
    cells = [cell for landmass in gdp_landmasses for cell in landmass]
    counts = Counter(cell[0] for cell in cells)
    short = [code for code, quota in quotas.items() if counts[code] < quota]
    extra = [code for code, quota in quotas.items() if counts[code] > quota]
    if len(short) != len(extra):
        raise SystemExit(f"Cannot repair squares: short {short}, extra {extra}")
    for needy, donor in zip(short, extra):
        square = min((cell for cell in cells if cell[0] == donor), key=lambda cell: cell[1])
        square[0] = needy
        print(f"Moved one square from {donor} to {needy}")


def build_grids(countries):
    """Run popgrid and return the squares, one [code, column, row] list per landmass."""
    from popgrid import AreaGrid, PopGrid

    shapes = load_shapes(countries)
    gdp_grid = PopGrid.from_geodataframe(shapes, region_col="adm0_a3", weight_col="gdp", n=1000).build()
    land_grid = AreaGrid.from_geodataframe(shapes, region_col="adm0_a3", n=1000).build()

    def squares(grid):
        out = []
        for landmass in grid._landmasses:
            cells = landmass["cells"]
            if cells is None or len(cells) == 0:
                continue
            bounds = cells.geometry.bounds
            size = grid._cell_size
            out.append([
                [code, round(x / size), round(y / size)]
                for code, x, y in zip(cells.region_name, bounds.minx, bounds.miny)
            ])
        return out

    grids = {"gdp": squares(gdp_grid), "land": squares(land_grid)}
    repair_quotas(grids["gdp"], gdp_grid.quotas)
    return grids


def check_claims(countries, grids, refresh=False):
    """Recompute every claim printed on the graphic."""
    c = countries.set_index("code")
    total = countries.gdp.sum()
    average = total / countries["pop"].sum()
    imf_table = load_imf(refresh)
    imf = lambda code: imf_table[code] * 1e9 / USD_PER_EUR  # euros, at the same rate
    squares = Counter(s[0] for landmass in grids["gdp"] for s in landmass)
    land = countries.land_km2.sum()
    top5 = countries.iloc[:5]
    rest = countries.iloc[1:]
    smallest = rest.sort_values("gdp").gdp.cumsum()
    n_beaten = int((smallest < c.gdp["DEU"]).sum())

    checks = {
        "40 countries": len(countries) == 40,
        "1,000 squares": sum(squares.values()) == 1000,
        "every country within one square of its share": all(
            abs(squares[r.code] - r.share * 1000) < 1 for r in countries.itertuples()
        ),
        "a square is 24.3 billion euros": round(total / 1000 / 1e9, 1) == 24.3,
        "combined GDP is 24.3 trillion euros": round(total / 1e12, 1) == 24.3,
        "top five are the named ones": set(top5.code) == {"DEU", "GBR", "FRA", "ITA", "ESP"},
        "top five are 62%": round(top5.share.sum() * 100) == 62,
        "Ukraine has eight squares": squares["UKR"] == 8,
        "Ukraine is the largest by land": countries.land_km2.idxmax() == countries.index[countries.code == "UKR"][0],
        "Ukraine is 10% of land": round(c.land_km2["UKR"] / land * 100) == 10,
        "Ukraine is 0.8% of GDP": round(c.share["UKR"] * 100, 1) == 0.8,
        "Germany beats 29 smallest": n_beaten == GERMANY_BEATS,
        "Germany printed as 4.5 trillion euros": 4.45e12 <= c.gdp["DEU"] < 4.55e12,
        "Germany above Japan": c.gdp["DEU"] > imf("JPN"),
        "U.K. above India": c.gdp["GBR"] > imf("IND"),
        "France above Russia and Canada": c.gdp["FRA"] > max(imf("RUS"), imf("CAN")),
        "Italy above Canada and Brazil": c.gdp["ITA"] > max(imf("CAN"), imf("BRA")),
        "Spain above Mexico and Australia": c.gdp["ESP"] > max(imf("MEX"), imf("AUS")),
        "Luxembourg is highest per person": c.per_person.idxmax() == "LUX",
        "Luxembourg is 131K euros per person": round(c.per_person["LUX"] / 1e3) == 131,
        "Luxembourg is about 3x the average": 2.9 < c.per_person["LUX"] / average < 3.3,
        "average is 41K euros": round(average / 1e3) == 41,
        "Kosovo and Montenegro get no square": squares["KOS"] == 0 and squares["MNE"] == 0,
        "Kosovo is 11B euros and Montenegro 8B": round(c.gdp["KOS"] / 1e9) == 11 and round(c.gdp["MNE"] / 1e9) == 8,
        "all others get a square": all(squares[code] > 0 for code in c.index if code not in ("KOS", "MNE")),
        "no country is below the color scale": countries.per_person.min() >= PC_LOW,
        "the scale's centre is the average": abs(PC_MID - average) < 1,
    }
    failed = [claim for claim, passed in checks.items() if not passed]
    print(f"Claims checked: {len(checks) - len(failed)} of {len(checks)} pass")
    if failed:
        raise SystemExit("These claims no longer hold:\n  " + "\n  ".join(failed))


# DESIGN

WIDTH, HEIGHT = 1200, 1300
CELL = 19               # size of a square, in px
MAP_X, MAP_Y = 26, 222  # top-left corner of the main map
COL0, ROW0 = -38, 29    # first column and top row of the grid
INK = "#0A1B45"
MUTED = "#4F5A75"
EU_BLUE = "#003399"
EU_GOLD = "#FFCC00"
# Gold to pale to blue, with the European average at the middle stop
RAMP = ["#B98A00", "#FFCC00", "#FFE78A", "#EFECE1", "#7F9FE0", "#003399", "#001E63"]
PC_LOW, PC_MID, PC_HIGH = 5_000, 40_785, 90_000
GERMANY_BEATS = 29


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row. Rows count upward, as in the projected map."""
    return MAP_Y + (ROW0 - row) * CELL


def side(at, col, row, nudge=9):
    """Leader that runs to a label starting at this column and row."""
    return dict(at=at, out=(X(col) + 4, Y(row) + nudge))


# Label settings per country.
#   at + out  draw a leader from that square to a label outside the country
#   anchor    "end" puts the label to the left of the leader
#   skip      no label here (Luxembourg has its magnifier)
#   rect      place the label in these cells: (col, row, width, height)
#   n         font size of the name
#   dx, dy    nudge
LABELS = {
    "LUX": dict(skip=True),
    "GBR": dict(rect=(-32, 6, 9, 6)),
    "ISL": side((-25, 27), -24, 27),
    "EST": side((11, 10), 13, 11),
    "LVA": side((11, 9), 13, 9, 12),
    "LTU": side((12, 8), 14, 8, 8),
    "BLR": side((14, 4), 16, 5),
    "UKR": side((15, 3), 16, 3),
    "MDA": side((14, 2), 16, 1),
    "BGR": side((10, -5), 12, -5),
    "MKD": side((9, -7), 12, -7),
    "ALB": side((9, -8), 12, -9),
    "CYP": side((13, -16), 15, -16),
    "MLT": dict(at=(-8, -16), out=(X(-9) + 4, Y(-16) + 9), anchor="end"),
    "SVN": dict(at=(5, -4), out=(X(6) - 4, Y(-5) + 9), anchor="end"),
    "HRV": dict(at=(7, -6), out=(X(6) - 4, Y(-6) + 13), anchor="end"),
    "BIH": dict(at=(8, -7), out=(X(7) - 8, Y(-8) + 12), anchor="end"),
    "SVK": side((10, 1), 16, 0),
    "HUN": dict(rect=(8, -3, 2, 2), n=9),
    "SRB": dict(n=9.5),
    "PRT": dict(n=9),
    "GRC": dict(n=9.5, rect=(7, -13, 2, 3)),
    "FIN": dict(n=9.5, rect=(0, 23, 2, 4)),
}

# The five largest economies get a larger label and a country comparison
HERO = {
    "DEU": dict(name=26, value=44, note=["Bigger than Japan"]),
    "GBR": dict(name=24, value=36, note=["Bigger than India"]),
    "FRA": dict(name=24, value=38, note=["Bigger than Russia or Canada"]),
    "ITA": dict(name=21, value=30, note=["Bigger than Canada", "or Brazil"]),
    "ESP": dict(name=21, value=30, note=["Bigger than Mexico", "or Australia"]),
}


# SVG HELPERS

def to_rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(to_rgb(a), to_rgb(b)))


def scale_color(per_person):
    """Diverging scale: gold below the European average, blue above it."""
    if per_person <= PC_MID:
        position = 0.5 * max(0, per_person - PC_LOW) / (PC_MID - PC_LOW)
    else:
        position = 0.5 + 0.5 * min(1, (per_person - PC_MID) / (PC_HIGH - PC_MID))
    position *= len(RAMP) - 1
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
    return f"\u20ac{value / 1e12:.2f}T" if value >= 1e12 else f"\u20ac{value / 1e9:.0f}B"


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

def name_and_value(x, y, name, value, anchor):
    """One line of text, so the gap stays even whatever the length of the name."""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{INK}" style="font-feature-settings:\'lnum\' 1">'
        f'<tspan font-family="Inter" font-size="10.5" font-weight="700">{html.escape(name)}</tspan>'
        f'<tspan dx="6" font-family="Source Serif 4" font-size="12.5" font-weight="700">{html.escape(value)}</tspan></text>'
    )


def labels(main, data, origin_x, origin_y):
    out = []
    px = lambda col: origin_x + col * CELL
    py = lambda row: origin_y - (row + 1) * CELL
    cells_of = defaultdict(set)
    for cell, code in main.items():
        cells_of[code].add(cell)

    for code, cells in cells_of.items():
        conf = LABELS.get(code, {})
        if conf.get("out") or conf.get("skip"):
            continue
        col, row, w, h = conf.get("rect") or largest_rectangle(cells)
        cx = px(col) + w * CELL / 2 + conf.get("dx", 0)
        cy = py(row + h - 1) + h * CELL / 2 + conf.get("dy", 0)
        fill = scale_color(data[code].per_person)
        ink = text_color(fill)
        value = money(data[code].gdp)

        if code in HERO:
            hero = HERO[code]
            size = hero["value"]
            out.append(text(cx, cy - size * 0.62, data[code].name, hero["name"], 800, "serif", ink, halo=fill, halo_width=5))
            out.append(text(cx, cy + size * 0.5, value, size, 900, "serif", ink, halo=fill, halo_width=6))
            for i, note in enumerate(hero["note"]):
                out.append(text(cx, cy + size * 0.5 + 17 + i * 14.5, note, 11.5, 600, "sans", ink, halo=fill, halo_width=4))
            continue

        name = data[code].name
        v_size, n_size = 12.5, conf.get("n", 10)
        out.append(text(cx, cy - v_size * 0.30, name, n_size, 600, "sans", ink, halo=fill))
        out.append(text(cx, cy + v_size * 0.78, value, v_size, 700, "num", ink, halo=fill))

    for code, conf in LABELS.items():
        if not conf.get("out"):
            continue
        value = money(data[code].gdp)
        name = data[code].name
        x, y = conf["out"]
        col, row = conf["at"]
        tail = f"V{y:.1f}H{x:.1f}" if conf.get("up") else f"L{x:.1f} {y:.1f}"
        out.append(leader(px(col) + CELL / 2, py(row) + CELL / 2, tail))
        out.append(name_and_value(x + (-4 if conf.get("anchor") == "end" else 4), y + 4.4, name, value, conf.get("anchor", "start")))
    return "".join(out)


def header():
    out = [
        text(24, 122, "Europe\u2019s Economy", 136, 900, "serif", INK, "start", 'letter-spacing="-2.4"'),
        text(1174, 180, "Sized by 2025 GDP, Not Land", 44, 700, "serif", INK, "end", 'letter-spacing="-.4"'),
    ]
    out.append(lines(396, 218, [
        "Each square is 0.1% of Europe\u2019s GDP, about",
        "\u20ac24.3 billion. Five countries produce over 60%",
        "of it, while Ukraine, the largest country by",
        "land, earns just eight squares.",
    ], 13.5, 18.5))

    x, y, width = 392, 322, 165
    stops = "".join(f'<stop offset="{i / (len(RAMP) - 1)}" stop-color="{c}"/>' for i, c in enumerate(RAMP))
    out.append(
        f'<defs><linearGradient id="legend">{stops}</linearGradient></defs>'
        f'<rect x="{x}" y="{y}" width="{width}" height="11" rx="2" fill="url(#legend)"/>'
    )
    out.append(text(x, y - 6, "Color: GDP per person, 2025", 11, 600, "sans", INK, "start"))
    out.append(text(x - 5, y + 10, "\u20ac5K", 11.5, 700, "num", INK, "end"))
    out.append(text(x + width + 5, y + 10, "\u20ac90K+", 11.5, 700, "num", INK, "start"))
    middle = x + width / 2
    out.append(f'<path d="M{middle} {y - 2}V{y + 13}" stroke="{INK}" stroke-width="1.4"/>')
    out.append(text(middle, y + 25, "Europe average \u20ac41K", 10, 600, "sans", MUTED))
    return "".join(out)


def luxembourg_magnifier(main, data, fill_of):
    cx, cy, radius = 110, 780, 58
    start_x, start_y = X(-12) + 9.5, Y(3) + 9.5
    angle = math.atan2(cy - start_y, cx - start_x)
    end_x, end_y = cx - radius * math.cos(angle), cy - radius * math.sin(angle)
    out = [
        leader(start_x, start_y, f"L{end_x:.1f} {end_y:.1f}"),
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="#fff" stroke="{EU_GOLD}" stroke-width="3.5"/>',
    ]
    lux = {cell: code for cell, code in main.items() if code == "LUX"}
    cols = [c for c, _ in lux]
    rows = [r for _, r in lux]
    size = 13
    block_w = (max(cols) - min(cols) + 1) * size
    block_h = (max(rows) - min(rows) + 1) * size
    out.append(draw_squares(
        lux, size, cx - block_w / 2 - min(cols) * size, cy - 4 + block_h / 2 + min(rows) * size,
        fill_of, gap=0.5, border=1.2,
    ))
    out.append(text(cx, cy - 32, "Luxembourg", 10.5, 700))
    out.append(text(cx, cy + 41, money(data["LUX"].gdp), 20, 900, "serif"))
    out.append(lines(cx, cy + radius + 18, [
        "Highest GDP per person:",
        "\u20ac131K, or 3x the average",
    ], 11.5, 14.5, weight=600, anchor="middle"))
    return "".join(out)


def land_inset(grids, fill_of):
    """popgrid's AreaGrid in the same colors, with Ukraine outlined and named."""
    land = {(col, row): code for landmass in grids["land"] for code, col, row in landmass}
    # Close one-square holes left by the land grid, so they do not read as territory
    for col in range(min(c for c, _ in land), max(c for c, _ in land)):
        for row in range(min(r for _, r in land), max(r for _, r in land)):
            around = [land.get(k) for k in ((col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1))]
            if (col, row) not in land and all(around):
                land[(col, row)] = max(set(around), key=around.count)
    size, left, top = 5, 912, 250
    origin_x = left - min(c for c, _ in land) * size
    origin_y = top + (max(r for _, r in land) + 1) * size
    ukraine = {cell: code for cell, code in land.items() if code == "UKR"}
    cx = origin_x + (sum(c for c, _ in ukraine) / len(ukraine) + 0.5) * size
    cy = origin_y - (sum(r for _, r in ukraine) / len(ukraine) + 0.5) * size
    return "".join([
        text(1174, 236, "The Same Countries, Sized by Land", 17, 800, "serif", INK, "end"),
        draw_squares(land, size, origin_x, origin_y, fill_of, gap=0, radius=0, border=0.7, tint=0),
        draw_squares(ukraine, size, origin_x, origin_y, fill_of, gap=0, radius=0, border=1.8, tint=0),
        text(cx, cy + 3.5, "Ukraine", 10, 700, "sans", INK, halo=fill_of("UKR"), halo_width=2.5),
        lines(800, 300, ["Ukraine has", "10% of Europe\u2019s", "land, but only", "0.8% of its GDP"], 17, 22.5, weight=800, family="serif"),
    ])


def bottom_band(data):
    y = 1190
    return "".join([
        text(44, y, "62%", 54, 900, "serif", EU_BLUE, "start", 'letter-spacing="-1"'),
        lines(158, y - 31, ["of Europe\u2019s GDP comes from", "five countries: Germany, the", "U.K., France, Italy and Spain"], 12.5, 17),
        f'<path d="M386 {y - 44}V{y + 12}" stroke="{INK}" stroke-width=".8"/>',
        text(410, y, "\u20ac4.5T", 50, 900, "serif", EU_BLUE, "start", 'letter-spacing="-.6"'),
        lines(562, y - 31, ["Germany out-produces", "the 29 smallest economies", "here combined"], 12.5, 17),
        f'<path d="M744 {y - 44}V{y + 12}" stroke="{INK}" stroke-width=".8"/>',
        text(768, y - 22, "Too small for a square", 17, 800, "serif", INK, "start"),
        lines(768, y, [f"Kosovo {money(data['KOS'].gdp)} and Montenegro {money(data['MNE'].gdp)}"], 12.5, 17),
    ])


def footer():
    y = 1226
    return "".join([
        f'<path d="M26 {y}H1174" stroke="{INK}" stroke-width="1"/>',
        lines(26, y + 20, [
            "Source: IMF World Economic Outlook (2025 GDP and population, by country), converted from U.S. dollars at the ECB\u2019s 2025 average of $1.13 per euro;",
            "World Bank (land area, no figure for Kosovo). Europe here means 40 countries, excluding Russia, T\u00fcrkiye and microstates. Squares are whole-number",
            "shares (largest remainder method) of their combined GDP, \u20ac24.3T. Ukraine\u2019s outline includes Crimea. \u201cBigger than\u201d compares nominal 2025 GDP.",
        ], 10.5, 14.5, weight=400, fill=MUTED),
        lines(1174, y + 20, ["Josep Ferrer", "databites.tech", "Grid built with popgrid"], 11, 14.5, weight=600, anchor="end"),
    ])


def build_page(countries, grids, font_css):
    data = {r.code: r for r in countries.itertuples()}
    fill_of = lambda code: scale_color(data[code].per_person)
    main = {(col, row): code for landmass in grids["gdp"] for code, col, row in landmass}
    origin_x = MAP_X - COL0 * CELL
    origin_y = MAP_Y + (ROW0 + 1) * CELL
    svg = "".join([
        header(),
        draw_squares(main, CELL, origin_x, origin_y, fill_of),
        labels(main, data, origin_x, origin_y),
        luxembourg_magnifier(main, data, fill_of),
        land_inset(grids, fill_of),
        bottom_band(data),
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
    parser.add_argument("--out", default=str(HERE / "europe-economy-by-gdp.png"))
    parser.add_argument("--scale", type=float, default=2, help="pixel density; 2 gives 2400 px wide")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="download current IMF and World Bank figures")
    args = parser.parse_args()

    CACHE.mkdir(exist_ok=True)
    countries = load_countries(args.refresh)
    grids = build_grids(countries)
    if not args.skip_checks:
        check_claims(countries, grids, args.refresh)
    page_html = build_page(countries, grids, fetch_fonts())
    screenshot(page_html, args.out, args.scale)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()

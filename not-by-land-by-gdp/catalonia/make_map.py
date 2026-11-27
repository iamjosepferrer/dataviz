#!/usr/bin/env python3
"""
Catalonia's Economy, Sized by 2023 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of Catalonia's 42 comarques and Aran, built with popgrid: 1,000 squares,
each one 0.1% of their combined GDP. Square count shows GDP, color shows GDP per person.

Run:
    python make_map.py               # rebuilds catalonia-economy-by-gdp.png from the snapshot
    python make_map.py --refresh     # downloads current Idescat and IMF figures first

Data, committed so the published map stays reproducible:
    data.csv       Idescat comarca GDP and GDP per inhabitant (2024 benchmark revision), land area
    imf_2023.csv   IMF GDP by country in U.S. dollars (checks the comparisons)

Boundaries: municipalities (ICGC, via github.com/gmarull/cat-topojson) grouped into
comarques with Idescat's current municipality codes, which include El Lluçanès (2023).
"""

import io
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common  # noqa: E402

YEAR = 2023
USD_PER_EUR = 1.081268627451   # ECB reference rate, 2023 annual average
DATA_CSV = HERE / "data.csv"
IMF_CSV = HERE / f"imf_{YEAR}.csv"
URLS = {
    "idescat_pibc.json": "https://api.idescat.cat/taules/v2/pibc/21925/26069/com/data?lang=en",
    "idescat_municipis.csv": "https://www.idescat.cat/codis/?id=50&n=9&f=ssv",
    "cat-municipis.json": "https://raw.githubusercontent.com/sirisacademic/catalonia-cartography/master/cat-municipis.json",
}


# DATA

def municipalities():
    """Municipal polygons with their current comarca code."""
    import geopandas as gpd
    shapes = gpd.read_file(common.fetch("cat-municipis.json", URLS["cat-municipis.json"]))
    shapes["id"] = shapes["id"].astype(str).str.zfill(6)
    codes = pd.read_csv(io.StringIO(common.fetch("idescat_municipis.csv", URLS["idescat_municipis.csv"]).read_bytes().decode("utf-8-sig")),
                        sep=";", skiprows=3, dtype=str)
    shapes = shapes.merge(codes[["Codi", "Codi comarca"]], left_on="id", right_on="Codi", how="left")
    missing = shapes[shapes["Codi comarca"].isna()]
    if len(missing):
        raise SystemExit(f"Municipalities without a comarca: {missing.nom.tolist()}")
    return shapes


def load_regions(refresh=False):
    """GDP, population (GDP / GDP per inhabitant) and land area for the 43 comarques."""
    if not DATA_CSV.exists() or refresh:
        d = json.loads(common.fetch("idescat_pibc.json", URLS["idescat_pibc.json"]).read_text())
        dims, size = d["dimension"], d["size"]
        years = dims["YEAR"]["category"]["index"]
        coms = dims["COM"]["category"]["index"]
        labels = dims["COM"]["category"]["label"]
        yi = years.index(str(YEAR))
        land = municipalities().groupby("Codi comarca").sup.sum()
        rows = []
        for ci, code in enumerate(coms):
            if code == "TOTAL":
                continue
            gdp_me, per_inh = (d["value"][(yi * size[1] + ci) * size[2] + k] for k in range(2))
            rows.append(dict(code=code, name=labels[code], gdp=round(gdp_me * 1e6),
                             pop=round(gdp_me * 1e6 / per_inh), land_km2=round(land[code], 2)))
        pd.DataFrame(rows).to_csv(DATA_CSV, index=False)
    table = pd.read_csv(DATA_CSV, dtype={"code": str})
    table["label"] = table.name
    table["gdp"] = table.gdp.astype(float)
    table["share"] = table.gdp / table.gdp.sum()
    table["per_person"] = table.gdp / table["pop"]
    return table.sort_values("gdp", ascending=False).reset_index(drop=True)


def build_grids(regions):
    shapes = municipalities()
    shapes["geometry"] = shapes.geometry.make_valid()   # the TopoJSON has a few self-touching rings
    shapes = shapes.dissolve(by="Codi comarca").reset_index()
    shapes = shapes.rename(columns={"Codi comarca": "region_name"})[["region_name", "geometry"]]
    return common.build_grids(shapes, dict(zip(regions.code, regions.gdp)))


def load_imf(refresh=False):
    usd = common.load_imf(IMF_CSV, YEAR, refresh)
    return {code: v * 1e9 / USD_PER_EUR for code, v in usd.items()}


# FACTS

def facts(regions, grids, refresh=False):
    r = regions.set_index("code")
    total = regions.gdp.sum()
    squares = Counter(cell[0] for landmass in grids["gdp"] for cell in landmass)
    imf = load_imf(refresh)
    average = total / regions["pop"].sum()
    land = regions.land_km2.sum()
    leader = regions.iloc[0]
    rest = regions.iloc[1:].sort_values("gdp")
    beaten = int((rest.gdp.cumsum() < leader.gdp).sum())
    top4 = regions.iloc[:4]
    richest = regions.loc[regions.per_person.idxmax()]
    nosquare = regions[~regions.code.isin(squares)]
    heroes = {}
    used = set()
    for row in regions.iloc[:3].itertuples():
        codes = common.bigger_than(row.gdp, imf, {"ESP"}, k=3, pool=common.EUROPE)
        codes = [c for c in codes if c not in used][:1] or codes[:1]   # one country each, no repeats
        used.update(codes)
        heroes[row.code] = codes
    return dict(
        total=total, square=total / 1000, squares=squares, imf=imf, average=average,
        leader=leader, leader_land=leader.land_km2 / land, beaten=beaten, top4=top4, top4_share=top4.share.sum(),
        top4_land=top4.land_km2.sum() / land, richest=richest, richest_ratio=richest.per_person / average,
        nosquare=nosquare, heroes=heroes, r=r, land=land,
    )


def check_claims(regions, grids, F):
    r, imf = F["r"], F["imf"]
    checks = {
        "42 comarques and Aran": len(regions) == 43,
        "1,000 squares": sum(F["squares"].values()) == 1000,
        "every comarca within one square of its share": all(
            abs(F["squares"].get(x.code, 0) - x.share * 1000) < 1 for x in regions.itertuples()),
        "comparisons hold": all(r.gdp[code] > imf[c] for code, cs in F["heroes"].items() for c in cs),
        "Barcelonès is the largest economy": F["leader"].code == "13",
        "only Ribera d'Ebre is above the colour scale": (regions.per_person > PC_HIGH).sum() == 1
        and F["richest"].code == "30",
        "no comarca is below the colour scale": regions.per_person.min() >= PC_LOW,
        "the comarques with no square are listed": set(F["nosquare"].code) == {"05"},
    }
    common.check_claims(checks)


# DESIGN

INK = "#3D0A0A"
MUTED = "#6E4A44"
PC_LOW, PC_HIGH = 20_000, 50_000
S = common.Style(INK, MUTED, "catalonia", PC_LOW, PC_HIGH, currency="€")


def text_for(regions, F):
    money1 = lambda v: f"€{v / 1e12:.2f}T" if v >= 1e12 else f"€{v / 1e9:.0f}B"
    lead, rich = F["leader"], F["richest"]
    ns = F["nosquare"]
    return dict(
        title="Catalonia’s Economy",
        subtitle=f"Sized by {YEAR} GDP, Not Land",
        dek=[
            f"Each square is 0.1% of Catalonia’s GDP, about",
            f"€{F['square'] / 1e6:.0f} million. The Barcelonès alone out-produces",
            f"the {F['beaten']} smallest comarques combined.",
        ],
        legend_title=f"Color: GDP per person, {YEAR}",
        legend_low=f"€{PC_LOW / 1e3:.0f}K", legend_high=f"€{PC_HIGH / 1e3:.0f}K+",
        legend_mean=f"Catalonia €{F['average'] / 1e3:.0f}K", mean_value=F["average"],
        hero_notes={code: common.wrap_note("Bigger than " + " or ".join(common.COMPARE_NAMES[c] for c in cs), 22)
                    for code, cs in F["heroes"].items()},
        magnifier=["Highest GDP per person:", f"€{rich.per_person / 1e3:.0f}K, or {rich.per_person / F['average']:.1f}x the average"],
        inset_title="The Same Comarques, Sized by Land",
        note=[
            "The Barcelonès has",
            f"{F['leader_land'] * 100:.1f}% of Catalonia’s",
            f"land, but {lead.share * 100:.0f}% of its GDP",
        ],
        stats=[
            dict(big=f"{F['top4_share'] * 100:.0f}%", note=["of Catalonia’s GDP comes from", "four comarques on just", f"{F['top4_land'] * 100:.0f}% of its land"], note_width=200),
        ],
        footer=[
            f"Source: Idescat (comarca GDP and GDP per inhabitant, {YEAR} provisional, 2024 benchmark revision); ICGC municipal boundaries grouped into",
            f"comarques with Idescat codes; IMF World Economic Outlook ({YEAR} GDP by country, converted at the ECB’s {YEAR} average of ${USD_PER_EUR:.2f} per euro).",
            f"Squares are whole-number shares (largest remainder method) of the GDP of the 42 comarques and Aran, {money1(F['total'])}. Too small for a square:",
            " and ".join(f"{n} (€{v / 1e6:.0f}M)" for n, v in zip(ns.name, ns.gdp))
            + f". Highest GDP per person: Ribera d’Ebre, €{rich.per_person / 1e3:.0f}K, {F['richest_ratio']:.1f}x the average, above the color scale.",
        ],
    )


CELL, MAP_X, MAP_Y, COL0, ROW0 = 19, 252, 372, -19, 17


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row."""
    return MAP_Y + (ROW0 - row) * CELL


# Small comarques get a leader. West: a column of labels in the left margin, in map order.
# North: a staircase of labels above the map. Each entry is (code, column, row) of the square
# the leader starts from.
WEST = [("39", -8, 14), ("26", -8, 13), ("04", -8, 12), ("25", -9, 12), ("23", -9, 11), ("35", -9, 10),
        ("32", -10, 9), ("27", -13, 7), ("38", -12, 5), ("18", -13, 4), ("16", -13, 1), ("01", -12, 0),
        ("29", -17, -7), ("30", -16, -7), ("37", -18, -9), ("22", -19, -16)]
NORTH = [("15", -4, 15), ("14", -1, 15), ("43", 1, 15), ("42", 3, 14), ("31", 5, 17), ("28", 15, 16)]


def leader_layout():
    labels = {}
    y_prev = -1e9
    for code, col, row in WEST:
        y = max(Y(row) + CELL / 2, y_prev + 17)
        y_prev = y
        labels[code] = dict(at=(col, row), out=(MAP_X - 12, y), anchor="end",
                            tail=f"H{MAP_X - 6:.1f}V{y:.1f}H{MAP_X - 12:.1f}" if abs(y - Y(row) - CELL / 2) > 0.5
                            else f"H{MAP_X - 12:.1f}")
    top = Y(17) - 14
    for i, (code, col, row) in enumerate(NORTH):
        y = top - (len(NORTH) - 1 - i) * 17
        x = X(col) + CELL / 2 + 12
        labels[code] = dict(at=(col, row), out=(x, y), tail=f"V{y:.1f}H{x:.1f}")
    return labels


P = dict(
    width=1200, height=1268,
    header=dict(title_size=120, subtitle_y=170, dek=(40, 216), legend=(74, 300, 200)),
    map=dict(cell=CELL, x=MAP_X, y=MAP_Y),
    labels=leader_layout(),
    hero={"13": dict(name=24, value=40), "11": dict(name=16, value=22), "40": dict(name=16, value=22)},
    inset=dict(x=992, y=992, cell=4, title=(1174, 978, "end"), note=(560, 1100), highlight="13", highlight_label=False,
               arrow_from=(760, 1098), arrow_bend=-0.3),
    band=dict(y=1150),
    footer_y=1186,
)


def build_page(regions, grids, font_css, F=None):
    F = F or facts(regions, grids)
    data = {x.code: x for x in regions.itertuples()}
    return common.GridPoster(S, data, grids, P, text_for(regions, F)).build(font_css)


def run(args):
    regions = load_regions(args.refresh)
    grids = build_grids(regions)
    F = facts(regions, grids, args.refresh)
    if not args.skip_checks:
        check_claims(regions, grids, F)
    return build_page(regions, grids, common.fetch_fonts(), F), P["width"], P["height"]


if __name__ == "__main__":
    common.cli(__doc__.split("\n")[1], HERE / "catalonia-economy-by-gdp.png", run)

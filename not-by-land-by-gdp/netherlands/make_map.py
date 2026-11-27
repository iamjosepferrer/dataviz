#!/usr/bin/env python3
"""
The Netherlands' Economy, Sized by 2024 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of the 12 provinces of the Netherlands (the European part of the country),
built with popgrid: 1,000 squares, each one 0.1% of their combined GDP. Square count
shows GDP, color shows GDP per person.

Run:
    python make_map.py               # rebuilds netherlands-economy-by-gdp.png from the snapshot
    python make_map.py --refresh     # downloads current Eurostat and IMF figures first

Data, committed so the published map stays reproducible:
    data.csv       Eurostat GDP (nama_10r_2gdp), population (nama_10r_3popgdp), land area (reg_area3)
    imf_2024.csv   IMF GDP by country in U.S. dollars (checks the comparisons)
"""

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common  # noqa: E402

YEAR = 2024
USD_PER_EUR = 1.08238046875   # ECB reference rate, 2024 annual average
DATA_CSV = HERE / "data.csv"
IMF_CSV = HERE / f"imf_{YEAR}.csv"

# NUTS 2 code -> (Natural Earth region in popgrid, name on the graphic, short label)
REGIONS = {
    "NL11": ("Groningen", "Groningen", "Groningen"),
    "NL12": ("Friesland", "Friesland", "Friesland"),
    "NL13": ("Drenthe", "Drenthe", "Drenthe"),
    "NL21": ("Overijssel", "Overijssel", "Overijssel"),
    "NL22": ("Gelderland", "Gelderland", "Gelderland"),
    "NL23": ("Flevoland", "Flevoland", "Flevoland"),
    "NL32": ("Noord-Holland", "North Holland", "North Holland"),
    "NL34": ("Zeeland", "Zeeland", "Zeeland"),
    "NL35": ("Utrecht", "Utrecht", "Utrecht"),
    "NL36": ("Zuid-Holland", "South Holland", "South Holland"),
    "NL41": ("Noord-Brabant", "North Brabant", "North Brabant"),
    "NL42": ("Limburg", "Limburg", "Limburg"),
}
NORTH = ["NL11", "NL12", "NL13"]   # the three northern provinces


# DATA

def load_regions(refresh=False):
    """GDP in euros, population and land area for the 12 provinces."""
    if not DATA_CSV.exists() or refresh:
        gdp, _ = common.eurostat("nama_10r_2gdp", unit="MIO_EUR", time=YEAR)
        pop, _ = common.eurostat("nama_10r_3popgdp", unit="THS", time=YEAR)
        area, _ = common.eurostat("reg_area3", unit="KM2", landuse="L0008")
        rows = []
        for code, (shape, name, label) in REGIONS.items():
            land = [v for v in area[code].values() if v is not None][-1]
            rows.append(dict(code=code, shape=shape, name=name, label=label,
                             gdp=round(gdp[code][str(YEAR)] * 1e6), pop=round(pop[code][str(YEAR)] * 1e3), land_km2=land))
        pd.DataFrame(rows).to_csv(DATA_CSV, index=False)
    table = pd.read_csv(DATA_CSV)
    table["gdp"] = table.gdp.astype(float)
    table["share"] = table.gdp / table.gdp.sum()
    table["per_person"] = table.gdp / table["pop"]
    return table.sort_values("gdp", ascending=False).reset_index(drop=True)


def build_grids(regions):
    shapes = common.admin1("NLD")
    shapes["region_name"] = shapes.region_name.map({v[0]: k for k, v in REGIONS.items()})
    return common.build_grids(shapes, dict(zip(regions.code, regions.gdp)))


def load_imf(refresh=False):
    """IMF GDP by country, converted to euros at the same rate."""
    usd = common.load_imf(IMF_CSV, YEAR, refresh)
    return {code: v * 1e9 / USD_PER_EUR for code, v in usd.items()}


# FACTS: every number on the graphic, computed here and checked in check_claims()

def facts(regions, grids, refresh=False):
    r = regions.set_index("code")
    total = regions.gdp.sum()
    squares = Counter(cell[0] for landmass in grids["gdp"] for cell in landmass)
    imf = load_imf(refresh)
    average = total / regions["pop"].sum()
    top = regions.iloc[:3]
    leader = regions.iloc[0]
    rest = regions.iloc[1:].sort_values("gdp")
    beaten = int((rest.gdp.cumsum() < leader.gdp).sum())
    land = regions.land_km2.sum()
    north = regions[regions.code.isin(NORTH)]
    richest = regions.loc[regions.per_person.idxmax()]
    heroes = {}
    for row in regions.iloc[:4].itertuples():
        codes = common.bigger_than(row.gdp, imf, {"NLD"})
        if len(" or ".join(common.COMPARE_NAMES[c] for c in codes)) > 22:
            codes = codes[:1]
        heroes[row.code] = codes
    return dict(
        total=total, square=total / 1000, squares=squares, imf=imf, average=average,
        top=top, top_share=top.share.sum(), top_land=top.land_km2.sum() / land, leader=leader, beaten=beaten,
        north_land=north.land_km2.sum() / land, north_gdp=north.share.sum(),
        north_squares=sum(squares[c] for c in NORTH),
        richest=richest, richest_ratio=richest.per_person / average, heroes=heroes, r=r,
    )


def check_claims(regions, grids, F):
    r, imf = F["r"], F["imf"]
    checks = {
        "12 provinces": len(regions) == 12,
        "1,000 squares": sum(F["squares"].values()) == 1000,
        "every province within one square of its share": all(
            abs(F["squares"][x.code] - x.share * 1000) < 1 for x in regions.itertuples()),
        "comparisons hold": all(r.gdp[code] > imf[c] for code, cs in F["heroes"].items() for c in cs),
        "colour scale covers every province": regions.per_person.min() >= PC_LOW and regions.per_person.max() <= PC_HIGH,
        "top three are North Holland, South Holland and North Brabant": list(F["top"].code) == ["NL32", "NL36", "NL41"],
        "top three produce over half": F["top_share"] > 0.5,
        "North Holland is highest per person": F["richest"].code == "NL32",
    }
    common.check_claims(checks)


# DESIGN

INK = "#3A1A05"
MUTED = "#6E5443"
PC_LOW, PC_HIGH = 40_000, 85_000
S = common.Style(INK, MUTED, "netherlands", PC_LOW, PC_HIGH, currency="\u20ac")


def text_for(regions, F):
    money1 = lambda v: f"\u20ac{v / 1e12:.2f}T" if v >= 1e12 else f"\u20ac{v / 1e9:.0f}B"
    lead = F["leader"]
    return dict(
        title="The Netherlands\u2019 Economy",
        subtitle=f"Sized by {YEAR} GDP, Not Land",
        dek=[
            f"Each square is 0.1% of Dutch GDP,",
            f"about \u20ac{F['square'] / 1e9:.1f} billion. North Holland,",
            f"South Holland and North Brabant",
            f"produce {F['top_share'] * 100:.0f}% of it, while the three",
            f"northern provinces earn just",
            f"{F['north_squares']} squares.",
        ],
        legend_title=f"Color: GDP per person, {YEAR}",
        legend_low=f"\u20ac{PC_LOW / 1e3:.0f}K", legend_high=f"\u20ac{PC_HIGH / 1e3:.0f}K",
        legend_mean=f"Netherlands \u20ac{F['average'] / 1e3:.0f}K", mean_value=F["average"],
        hero_notes={code: common.wrap_note("Bigger than " + " or ".join(common.COMPARE_NAMES[c] for c in cs), 20)
                    for code, cs in F["heroes"].items()},
        magnifier=[],
        inset_title="The Same Provinces, Sized by Land",
        note=[
            "Groningen, Friesland and",
            f"Drenthe have {F['north_land'] * 100:.0f}% of the",
            f"land, but only {F['north_gdp'] * 100:.1f}%",
            "of the GDP",
        ],
        stats=[
            dict(big=f"{F['top_share'] * 100:.0f}%", note=["of Dutch GDP comes from", "three provinces on", f"{F['top_land'] * 100:.0f}% of the land"]),
            dict(big=money1(lead.gdp), note=[f"{lead['name']} out-produces", f"the {F['beaten']} smallest provinces", "combined"], gap=200),
        ],
        footer=[
            f"Source: Eurostat (GDP at current market prices, nama_10r_2gdp; average population, nama_10r_3popgdp; land area, reg_area3), {YEAR};",
            f"IMF World Economic Outlook ({YEAR} GDP by country, converted at the ECB\u2019s {YEAR} average of ${USD_PER_EUR:.2f} per euro). Squares are whole-number shares",
            f"(largest remainder method) of the GDP of the 12 provinces, {money1(F['total'])}. Extra-regional GDP and the Caribbean Netherlands are left out.",
        ],
    )


CELL, MAP_X, MAP_Y, COL0, ROW0 = 19, 44, 236, -35, 18


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row."""
    return MAP_Y + (ROW0 - row) * CELL


P = dict(
    width=1200, height=1290,
    header=dict(title_size=96, subtitle_y=172, dek=(870, 230), legend=(904, 380, 200)),
    map=dict(cell=CELL, x=MAP_X, y=MAP_Y),
    labels={},
    hero={"NL32": dict(name=21, value=32), "NL36": dict(name=21, value=32),
          "NL41": dict(name=19, value=28), "NL22": dict(name=17, value=24)},
    inset=dict(x=880, y=470, cell=6, title=(1174, 452, "end"), note=(880, 790), outline=NORTH),
    band=dict(y=1196),
    footer_y=1222,
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
    common.cli(__doc__.split("\n")[1], HERE / "netherlands-economy-by-gdp.png", run)

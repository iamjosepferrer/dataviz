#!/usr/bin/env python3
"""
Mainland China's Economy, Sized by 2024 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of the 31 provinces of mainland China, built with popgrid: 1,000 squares,
each one 0.1% of their combined GDP. Square count shows GDP, color shows GDP per person.

Run:
    python make_map.py               # rebuilds china-economy-by-gdp.png from the snapshot
    python make_map.py --refresh     # downloads current OECD and IMF figures first

Data, committed so the published map stays reproducible:
    data.csv       OECD Regional Database (large regions, TL2): GDP in current U.S. dollars and
                   population; land area measured on Natural Earth boundaries
    imf_2024.csv   IMF GDP by country in U.S. dollars (checks the comparisons)
All amounts are converted to euros at the ECB's 2024 average rate.
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
ISO = "CHN"

# OECD code -> (Natural Earth region in popgrid, name on the graphic, short label)
REGIONS = {
    "CN01": ("Beijing", "Beijing", "Beijing"), "CN02": ("Tianjin", "Tianjin", "Tianjin"),
    "CN03": ("Hebei", "Hebei", "Hebei"), "CN04": ("Shanxi", "Shanxi", "Shanxi"),
    "CN05": ("Inner Mongol", "Inner Mongolia", "Inner Mongolia"), "CN06": ("Liaoning", "Liaoning", "Liaoning"),
    "CN07": ("Jilin", "Jilin", "Jilin"), "CN08": ("Heilongjiang", "Heilongjiang", "Heilongjiang"),
    "CN09": ("Shanghai", "Shanghai", "Shanghai"), "CN10": ("Jiangsu", "Jiangsu", "Jiangsu"),
    "CN11": ("Zhejiang", "Zhejiang", "Zhejiang"), "CN12": ("Anhui", "Anhui", "Anhui"),
    "CN13": ("Fujian", "Fujian", "Fujian"), "CN14": ("Jiangxi", "Jiangxi", "Jiangxi"),
    "CN15": ("Shandong", "Shandong", "Shandong"), "CN16": ("Henan", "Henan", "Henan"),
    "CN17": ("Hubei", "Hubei", "Hubei"), "CN18": ("Hunan", "Hunan", "Hunan"),
    "CN19": ("Guangdong", "Guangdong", "Guangdong"), "CN20": ("Guangxi", "Guangxi", "Guangxi"),
    "CN21": ("Hainan", "Hainan", "Hainan"), "CN22": ("Chongqing", "Chongqing", "Chongqing"),
    "CN23": ("Sichuan", "Sichuan", "Sichuan"), "CN24": ("Guizhou", "Guizhou", "Guizhou"),
    "CN25": ("Yunnan", "Yunnan", "Yunnan"), "CN26": ("Xizang", "Tibet", "Tibet"),
    "CN27": ("Shaanxi", "Shaanxi", "Shaanxi"), "CN28": ("Gansu", "Gansu", "Gansu"),
    "CN29": ("Qinghai", "Qinghai", "Qinghai"), "CN30": ("Ningxia", "Ningxia", "Ningxia"),
    "CN31": ("Xinjiang", "Xinjiang", "Xinjiang"),
}
WEST = ["CN31", "CN26", "CN29"]   # Xinjiang, Tibet and Qinghai


# DATA

def shapes():
    s = common.admin1(ISO)
    s = s[s.region_name.isin({v[0] for v in REGIONS.values()})].copy()
    s["region_name"] = s.region_name.map({v[0]: k for k, v in REGIONS.items()})
    return s


def load_regions(refresh=False):
    """GDP and GDP per person in euros, population and land area by region."""
    if not DATA_CSV.exists() or refresh:
        oecd = common.oecd_regions(ISO, YEAR)
        land = common.equal_area_km2(shapes())
        rows = []
        for code, (_, name, label) in REGIONS.items():
            rows.append(dict(code=code, name=name, label=label, gdp_usd=round(oecd.gdp_usd[code]),
                             pop=round(oecd["pop"][code]), land_km2=round(land[code])))
        pd.DataFrame(rows).to_csv(DATA_CSV, index=False)
    table = pd.read_csv(DATA_CSV)
    table["gdp"] = table.gdp_usd.astype(float) / USD_PER_EUR
    table["share"] = table.gdp / table.gdp.sum()
    table["per_person"] = table.gdp / table["pop"]
    return table.sort_values("gdp", ascending=False).reset_index(drop=True)


def build_grids(regions):
    return common.build_grids(shapes(), dict(zip(regions.code, regions.gdp)))


def load_imf(refresh=False):
    usd = common.load_imf(IMF_CSV, YEAR, refresh)
    return {code: v * 1e9 / USD_PER_EUR for code, v in usd.items()}


# FACTS: every number on the graphic, computed here and checked in check_claims()

def facts(regions, grids, refresh=False):
    r = regions.set_index("code")
    total = regions.gdp.sum()
    squares = Counter(cell[0] for landmass in grids["gdp"] for cell in landmass)
    imf = load_imf(refresh)
    average = total / regions["pop"].sum()
    half = int((regions.share.cumsum() <= 0.5).sum() + 1)
    leader = regions.iloc[0]
    rest = regions.iloc[1:].sort_values("gdp")
    beaten = int((rest.gdp.cumsum() < leader.gdp).sum())
    land = regions.land_km2.sum()
    west = regions[regions.code.isin(WEST)]
    richest = regions.loc[regions.per_person.idxmax()]
    heroes, used = {}, set()
    for row in regions.iloc[:4].itertuples():
        codes = common.bigger_than(row.gdp, imf, {"CHN"}, k=4)
        codes = [c for c in codes if c not in used][:1]
        used.update(codes)
        heroes[row.code] = codes
    return dict(
        total=total, square=total / 1000, squares=squares, imf=imf, average=average, half=half,
        leader=leader, beaten=beaten, west_land=west.land_km2.sum() / land, west_gdp=west.share.sum(),
        west_squares=sum(squares[c] for c in WEST), richest=richest, richest_ratio=richest.per_person / average,
        heroes=heroes, r=r,
    )


def check_claims(regions, grids, F):
    r, imf = F["r"], F["imf"]
    checks = {
        "31 provinces": len(regions) == 31,
        "1,000 squares": sum(F["squares"].values()) == 1000,
        "every province within one square of its share": all(
            abs(F["squares"][x.code] - x.share * 1000) < 1 for x in regions.itertuples()),
        "comparisons hold": all(r.gdp[code] > imf[c] for code, cs in F["heroes"].items() for c in cs),
        "colour scale covers every province": regions.per_person.min() >= PC_LOW and regions.per_person.max() <= PC_HIGH,
        "eight provinces make over half": F["half"] == 8,
        "Guangdong is the largest economy": F["leader"].code == "CN19",
        "Beijing is highest per person": F["richest"].code == "CN01",
        "Xinjiang, Tibet and Qinghai are the three largest by land after Inner Mongolia": set(
            regions.nlargest(4, "land_km2").code) == {"CN31", "CN26", "CN29", "CN05"},
    }
    common.check_claims(checks)


# DESIGN

INK = "#3A0D0A"
MUTED = "#6E4C47"
PC_LOW, PC_HIGH = 6_000, 30_000
S = common.Style(INK, MUTED, "china", PC_LOW, PC_HIGH, currency="€")


def text_for(regions, F):
    money1 = lambda v: f"€{v / 1e12:.2f}T" if v >= 1e12 else f"€{v / 1e9:.0f}B"
    lead, rich = F["leader"], F["richest"]
    return dict(
        title="Mainland China’s Economy",
        subtitle=f"Sized by {YEAR} GDP, Not Land",
        dek=[
            "Each square is 0.1% of the GDP of mainland",
            f"China’s 31 provinces, about €{F['square'] / 1e9:.0f} billion. {common.word(F['half']).capitalize()}",
            "coastal and central provinces produce over half",
            f"of it, while Xinjiang, Tibet and Qinghai earn",
            f"just {F['west_squares']} squares.",
        ],
        legend_title=f"Color: GDP per person, {YEAR}",
        legend_low=f"€{PC_LOW / 1e3:.0f}K", legend_high=f"€{PC_HIGH / 1e3:.0f}K",
        legend_mean=f"China €{F['average'] / 1e3:.0f}K", mean_value=F["average"],
        hero_notes={code: ["Bigger than " + common.COMPARE_NAMES[cs[0]]] for code, cs in F["heroes"].items()},
        magnifier=[],
        inset_title="The Same Provinces, Sized by Land",
        note=[
            "Xinjiang, Tibet and Qinghai",
            f"have {F['west_land'] * 100:.0f}% of the land, but",
            f"only {F['west_gdp'] * 100:.1f}% of the GDP",
        ],
        stats=[
            dict(big=money1(lead.gdp), note=[f"{lead['name']} out-produces", f"the {F['beaten']} smallest provinces", "combined"], gap=196, note_width=180),
            dict(title="Highest GDP per person", note=[f"{rich['name']}, €{rich.per_person / 1e3:.0f}K, or", f"{F['richest_ratio']:.1f}x the average"], width=200),
        ],
        footer=[
            f"Source: OECD Regional Database (GDP in current U.S. dollars and population by province, {YEAR}); IMF World Economic Outlook ({YEAR} GDP by country).",
            f"Converted to euros at the ECB’s {YEAR} average of ${USD_PER_EUR:.2f} per euro. Land area measured on Natural Earth boundaries. Squares are whole-number",
            f"shares (largest remainder method) of the GDP of the 31 provinces, {money1(F['total'])}. Hong Kong, Macau and Taiwan are not included.",
        ],
    )


CELL, MAP_X, MAP_Y, COL0, ROW0 = 21, 140, 214, -27, 23


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row."""
    return MAP_Y + (ROW0 - row) * CELL


def hang(col, row, label_row, anchor="start", dx=-48):
    """Leader that drops from a square to a label hanging below it."""
    x = X(col) + CELL / 2
    y = Y(label_row) + CELL / 2
    return dict(at=(col, row), out=(x + dx + (4 if anchor == "start" else -4) * -1, y), anchor=anchor,
                tail=f"V{y - 13:.1f}")


P = dict(
    width=1200, height=1320,
    header=dict(title_size=92, subtitle_y=166, dek=(60, 244), legend=(94, 368, 200)),
    map=dict(cell=CELL, x=MAP_X, y=MAP_Y),
    labels={
        "CN26": hang(-25, 7, 4.3, dx=-48),
        "CN29": hang(-23, 8, 2.3, anchor="end", dx=12),
        "CN30": dict(at=(-17, 9), out=(X(-16) + 6, Y(12.4) + 10), tail=f"V{Y(12.4) + 10:.1f}H{X(-16) + 6:.1f}"),
        "CN28": dict(at=(-19, 9), out=(X(-18) + 6, Y(13.6) + 10), tail=f"V{Y(13.6) + 10:.1f}H{X(-18) + 6:.1f}"),
        "CN21": dict(at=(-2, -19), out=(X(1), Y(-19) + 10)),
    },
    hero={"CN19": dict(name=20, value=30), "CN10": dict(name=20, value=30),
          "CN15": dict(name=18, value=26), "CN11": dict(name=17, value=24)},
    inset=dict(x=64, y=960, cell=5, title=(60, 946, "start"), note=(60, 1186), outline=WEST),
    band=dict(y=1212, x=470),
    footer_y=1236,
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
    common.cli(__doc__.split("\n")[1], HERE / "china-economy-by-gdp.png", run)

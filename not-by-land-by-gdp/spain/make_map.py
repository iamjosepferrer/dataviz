#!/usr/bin/env python3
"""
Spain's Economy, Sized by 2025 GDP, Not Land
Josep Ferrer, databites.tech

Tiled map of Spain's 17 autonomous communities and the cities of Ceuta and Melilla,
built with popgrid: 1,000 squares, each one 0.1% of their combined GDP. Square count
shows GDP, color shows GDP per person.

Run:
    python make_map.py               # rebuilds spain-economy-by-gdp.png from the snapshot
    python make_map.py --refresh     # downloads current INE, Eurostat and IMF figures first

Data, committed so the published map stays reproducible:
    data.csv       INE Regional Accounts: GDP at current prices and population (2025 provisional,
                   published 30 September 2026, to be revised in December 2026);
                   Eurostat land area (reg_area3)
    imf_2025.csv   IMF GDP by country in U.S. dollars (checks the comparisons)
"""

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common  # noqa: E402

YEAR = 2025
USD_PER_EUR = 1.129983   # ECB reference rate, 2025 annual average
INE_TABLES = {   # INE Regional Accounts (CRE) tables in the Tempus API
    "gdp": "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/82284?nult=3",   # GDP, current prices, thousand euros
    "pop": "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/80113?nult=3",   # population, thousands
}
DATA_CSV = HERE / "data.csv"
IMF_CSV = HERE / f"imf_{YEAR}.csv"

# NUTS 2 code -> (Natural Earth region in popgrid, name on the graphic, short label, name at INE)
REGIONS = {
    "ES11": ("Galicia", "Galicia", "Galicia", "Galicia"),
    "ES12": ("Asturias", "Asturias", "Asturias", "Asturias, Principado de"),
    "ES13": ("Cantabria", "Cantabria", "Cantabria", "Cantabria"),
    "ES21": ("País Vasco", "Basque Country", "Basque C.", "País Vasco"),
    "ES22": ("Foral de Navarra", "Navarre", "Navarre", "Navarra, Comunidad Foral de"),
    "ES23": ("La Rioja", "La Rioja", "La Rioja", "Rioja, La"),
    "ES24": ("Aragón", "Aragon", "Aragon", "Aragón"),
    "ES30": ("Madrid", "Madrid", "Madrid", "Madrid, Comunidad de"),
    "ES41": ("Castilla y León", "Castile and León", "Castile and León", "Castilla y León"),
    "ES42": ("Castilla-La Mancha", "Castile-La Mancha", "Castile-La Mancha", "Castilla - La Mancha"),
    "ES43": ("Extremadura", "Extremadura", "Extremadura", "Extremadura"),
    "ES51": ("Cataluña", "Catalonia", "Catalonia", "Cataluña"),
    "ES52": ("Valenciana", "Valencia", "Valencia", "Comunitat Valenciana"),
    "ES53": ("Islas Baleares", "Balearic Islands", "Balearics", "Balears, Illes"),
    "ES61": ("Andalucía", "Andalusia", "Andalusia", "Andalucía"),
    "ES62": ("Murcia", "Murcia", "Murcia", "Murcia, Región de"),
    "ES63": ("Ceuta", "Ceuta", "Ceuta", "Ceuta"),
    "ES64": ("Melilla", "Melilla", "Melilla", "Melilla"),
    "ES70": ("Canary Is.", "Canary Islands", "Canaries", "Canarias"),
}


# DATA

def load_regions(refresh=False):
    """GDP in euros, population and land area for the 19 regions."""
    if not DATA_CSV.exists() or refresh:
        import json

        def ine(kind):
            """{INE region name: value for YEAR} from the 'Dato base' series of an INE table."""
            series = json.loads(common.fetch(f"ine_{kind}_{YEAR}.json", INE_TABLES[kind]).read_text())
            out = {}
            for item in series:
                name, _, rest = item["Nombre"].partition(". Dato base")
                if rest:
                    out[name] = next((x["Valor"] for x in item["Data"] if x["Anyo"] == YEAR), None)
            return out

        gdp, pop = ine("gdp"), ine("pop")
        area, _ = common.eurostat("reg_area3", unit="KM2", landuse="L0008")
        rows = []
        for code, (shape, name, label, ine_name) in REGIONS.items():
            land = [v for v in area[code].values() if v is not None][-1]
            rows.append(dict(code=code, shape=shape, name=name, label=label,
                             gdp=round(gdp[ine_name] * 1e3), pop=round(pop[ine_name] * 1e3), land_km2=land))
        pd.DataFrame(rows).to_csv(DATA_CSV, index=False)
    table = pd.read_csv(DATA_CSV)
    table["gdp"] = table.gdp.astype(float)
    table["share"] = table.gdp / table.gdp.sum()
    table["per_person"] = table.gdp / table["pop"]
    return table.sort_values("gdp", ascending=False).reset_index(drop=True)


def build_grids(regions):
    shapes = common.admin1("ESP", dissolve_by="region")
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
    cum = regions.share.cumsum()
    half = int((cum <= 0.5).sum() + 1)
    top = regions.iloc[:2]
    leader = regions.iloc[0]
    rest = regions.iloc[1:].sort_values("gdp")
    beaten = int((rest.gdp.cumsum() < leader.gdp).sum())
    land = regions.land_km2.sum()
    biggest = regions.loc[regions.land_km2.idxmax()]
    richest = regions.loc[regions.per_person.idxmax()]
    exclude = {"ESP"}
    heroes, used = {}, set()
    for row in regions.iloc[:4].itertuples():   # the closest European economy below, no repeats
        codes = common.bigger_than(row.gdp, imf, exclude, k=4, pool=common.EUROPE)
        codes = [c for c in codes if c not in used][:1]
        used.update(codes)
        heroes[row.code] = codes
    return dict(
        total=total, square=total / 1000, squares=squares, imf=imf, average=average, half=half,
        top=top, top_share=top.share.sum(), leader=leader, beaten=beaten, beaten_regions=list(rest.code[:beaten]),
        biggest=biggest, biggest_land=biggest.land_km2 / land, biggest_gdp=biggest.share,
        biggest_squares=squares[biggest.code], richest=richest, richest_ratio=richest.per_person / average,
        heroes=heroes, r=r,
    )


def check_claims(regions, grids, F):
    r = F["r"]
    imf = F["imf"]
    checks = {
        "19 regions": len(regions) == 19,
        "1,000 squares": sum(F["squares"].values()) == 1000,
        "every region within one square of its share": all(
            abs(F["squares"][x.code] - x.share * 1000) < 1 for x in regions.itertuples()),
        "every region gets a square": all(F["squares"][code] > 0 for code in regions.code),
        "comparisons hold": all(r.gdp[code] > imf[c] for code, cs in F["heroes"].items() for c in cs),
        "every hero has a comparison": all(F["heroes"].values()),
        "colour scale covers every region": regions.per_person.min() >= PC_LOW and regions.per_person.max() <= PC_HIGH,
        "Madrid and Catalonia are the top two": set(F["top"].code) == {"ES30", "ES51"},
        "Castile and León is largest by land": F["biggest"].code == "ES41",
        "Madrid is highest per person": F["richest"].code == "ES30",
    }
    common.check_claims(checks)


# DESIGN

INK = "#3B0A12"
MUTED = "#6B4A4F"
PC_LOW, PC_HIGH = 20_000, 50_000
S = common.Style(INK, MUTED, "spain", PC_LOW, PC_HIGH, currency="€")


def text_for(regions, F):
    S_ = S
    money1 = lambda v: f"€{v / 1e12:.2f}T" if v >= 1e12 else f"€{v / 1e9:.0f}B"
    lead = F["leader"]
    return dict(
        title="Spain’s Economy",
        subtitle=f"Sized by {YEAR} GDP, Not Land",
        dek=[
            f"Each square is 0.1% of Spain’s GDP, about €{F['square'] / 1e9:.1f} billion.",
            f"Madrid and Catalonia alone produce {F['top_share'] * 100:.0f}% of it, while",
            f"Castile and León, the largest region by land, earns",
            f"just {F['biggest_squares']} squares.",
        ],
        legend_title=f"Color: GDP per person, {YEAR}",
        legend_low=f"€{PC_LOW / 1e3:.0f}K", legend_high=f"€{PC_HIGH / 1e3:.0f}K",
        legend_mean=f"Spain €{F['average'] / 1e3:.0f}K", mean_value=F["average"],
        hero_notes={code: common.wrap_note("Bigger than " + " or ".join(common.COMPARE_NAMES[c] for c in cs))
                    for code, cs in F["heroes"].items()},
        magnifier=[],
        inset_title="The Same Regions, Sized by Land",
        note=[
            "Castile and León has",
            f"{F['biggest_land'] * 100:.0f}% of Spain’s land,",
            f"but only {F['biggest_gdp'] * 100:.1f}% of its GDP",
        ],
        stats=[
            dict(big=f"{F['top_share'] * 100:.0f}%", note=["of Spain’s GDP comes from", "two regions: Madrid", "and Catalonia"]),
            dict(big=money1(lead.gdp), note=[f"{lead['name']} out-produces", f"the {F['beaten']} smallest regions", "combined"], gap=200),
        ],
        footer=[
            f"Source: INE, Spanish Regional Accounts (GDP at current prices and population, {YEAR} provisional, published September 2026); Eurostat (land area,",
            f"reg_area3); IMF World Economic Outlook ({YEAR} GDP by country, converted at the ECB\u2019s {YEAR} average of ${USD_PER_EUR:.2f} per euro). Squares are whole-number",
            f"shares (largest remainder method) of the GDP of the 19 regions, {money1(F['total'])}. Extra-regional GDP is left out. \u201cBigger than\u201d compares nominal {YEAR} GDP.",
        ],
    )


CELL, MAP_X, MAP_Y, COL0, ROW0 = 17, 60, 338, -24, 15


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row."""
    return MAP_Y + (ROW0 - row) * CELL


P = dict(
    width=1200, height=1265,
    header=dict(dek=(600, 214), legend=(634, 300, 200)),
    map=dict(cell=CELL, x=MAP_X, y=MAP_Y, pack={"ES70": (-24, -25, 1)}),
    labels={
        "ES42": dict(at=(-13, -11), out=(X(-15) - 8, Y(-11) + 8.5), anchor="end"),
        "ES63": dict(at=(-9, -24), out=(X(-6), Y(-24) + 8.5)),
        "ES64": dict(at=(2, -27), out=(X(5), Y(-27) + 8.5)),
    },
    hero={"ES30": dict(name=21, value=32), "ES51": dict(name=21, value=32),
          "ES61": dict(name=18, value=26), "ES52": dict(name=17, value=24, dx=22)},
    inset=dict(x=930, y=880, cell=5, title=(1174, 858, "end"), note=(700, 905),
               highlight="ES41", pack={"ES70": (-15, -28, 1)}),
    band=dict(y=1180),
    footer_y=1206,
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
    common.cli(__doc__.split("\n")[1], HERE / "spain-economy-by-gdp.png", run)

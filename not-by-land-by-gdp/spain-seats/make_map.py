#!/usr/bin/env python3
"""
Spain's Congress, Sized by Seats, Not Land
Josep Ferrer, databites.tech

Tiled map of the 52 constituencies of Spain's Congress of Deputies for the general
election of 29 November 2026, built with popgrid: 350 squares, one per seat. Color
shows how many residents each seat represents, against the national average.

Run:
    python make_map.py               # rebuilds spain-seats-2026.png from the snapshot
    python make_map.py --refresh     # re-reads the two decrees from the BOE first

Data, committed so the published map stays reproducible:
    data.csv   Seats per province (Real Decreto 806/2026, BOE 6 October 2026, and
               Real Decreto 400/2023 for the 2023 election), official population on
               1 January 2025 (Real Decreto 1117/2025), land area (Eurostat reg_area3)
The script also recomputes the seats from the population with article 162 of the
electoral law (LOREG) and stops if the result differs from the decree.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common  # noqa: E402

ELECTION = "29 November 2026"
DATA_CSV = HERE / "data.csv"
URLS = {
    "rd806_2026.pdf": "https://www.boe.es/boe/dias/2026/10/06/pdfs/BOE-A-2026-20742.pdf",
    "rd1117_2025.pdf": "https://boe.es/boe/dias/2025/12/11/pdfs/BOE-A-2025-25362.pdf",
    "rd400_2023.html": "https://www.boe.es/eli/es/rd/2023/05/29/400",
}

# Name in the 2026 decree -> (name in the population decree, Natural Earth region in popgrid,
#                              name on the graphic, short label, NUTS 3 codes for land area)
PROVINCES = {
    "Albacete": ("Albacete", "Albacete", "Albacete", "Albacete", ["ES421"]),
    "Alicante/Alacant": ("Alicante/Alacant", "Alicante", "Alicante", "Alicante", ["ES521"]),
    "Almería": ("Almería", "Almería", "Almería", "Almería", ["ES611"]),
    "Araba/Álava": ("Araba/Álava", "Álava", "Álava", "Álava", ["ES211"]),
    "Asturias": ("Asturias", "Asturias", "Asturias", "Asturias", ["ES120"]),
    "Ávila": ("Ávila", "Ávila", "Ávila", "Ávila", ["ES411"]),
    "Badajoz": ("Badajoz", "Badajoz", "Badajoz", "Badajoz", ["ES431"]),
    "Balears (Illes)": ("Balears, Illes", "Baleares", "Balearic Islands", "Balearics", ["ES531", "ES532", "ES533"]),
    "Barcelona": ("Barcelona", "Barcelona", "Barcelona", "Barcelona", ["ES511"]),
    "Bizkaia": ("Bizkaia", "Bizkaia", "Bizkaia", "Bizkaia", ["ES213"]),
    "Burgos": ("Burgos", "Burgos", "Burgos", "Burgos", ["ES412"]),
    "Cáceres": ("Cáceres", "Cáceres", "Cáceres", "Cáceres", ["ES432"]),
    "Cádiz": ("Cádiz", "Cádiz", "Cádiz", "Cádiz", ["ES612"]),
    "Cantabria": ("Cantabria", "Cantabria", "Cantabria", "Cantabria", ["ES130"]),
    "Castellón/Castelló": ("Castellón/Castelló", "Castellón", "Castellón", "Castellón", ["ES522"]),
    "Ciudad Real": ("Ciudad Real", "Ciudad Real", "Ciudad Real", "C. Real", ["ES422"]),
    "Córdoba": ("Córdoba", "Córdoba", "Córdoba", "Córdoba", ["ES613"]),
    "Coruña (A)": ("Coruña, A", "La Coruña", "A Coruña", "A Coruña", ["ES111"]),
    "Cuenca": ("Cuenca", "Cuenca", "Cuenca", "Cuenca", ["ES423"]),
    "Gipuzkoa": ("Gipuzkoa", "Gipuzkoa", "Gipuzkoa", "Gipuzkoa", ["ES212"]),
    "Girona": ("Girona", "Gerona", "Girona", "Girona", ["ES512"]),
    "Granada": ("Granada", "Granada", "Granada", "Granada", ["ES614"]),
    "Guadalajara": ("Guadalajara", "Guadalajara", "Guadalajara", "Guadalaj.", ["ES424"]),
    "Huelva": ("Huelva", "Huelva", "Huelva", "Huelva", ["ES615"]),
    "Huesca": ("Huesca", "Huesca", "Huesca", "Huesca", ["ES241"]),
    "Jaén": ("Jaén", "Jaén", "Jaén", "Jaén", ["ES616"]),
    "León": ("León", "León", "León", "León", ["ES413"]),
    "Lleida": ("Lleida", "Lérida", "Lleida", "Lleida", ["ES513"]),
    "Lugo": ("Lugo", "Lugo", "Lugo", "Lugo", ["ES112"]),
    "Madrid": ("Madrid", "Madrid", "Madrid", "Madrid", ["ES300"]),
    "Málaga": ("Málaga", "Málaga", "Málaga", "Málaga", ["ES617"]),
    "Murcia": ("Murcia", "Murcia", "Murcia", "Murcia", ["ES620"]),
    "Navarra": ("Navarra", "Navarra", "Navarre", "Navarre", ["ES220"]),
    "Ourense": ("Ourense", "Orense", "Ourense", "Ourense", ["ES113"]),
    "Palencia": ("Palencia", "Palencia", "Palencia", "Palencia", ["ES414"]),
    "Palmas (Las)": ("Palmas, Las", "Las Palmas", "Las Palmas", "Las Palmas", ["ES704", "ES705", "ES708"]),
    "Pontevedra": ("Pontevedra", "Pontevedra", "Pontevedra", "Pontevedra", ["ES114"]),
    "Rioja (La)": ("Rioja, La", "La Rioja", "La Rioja", "La Rioja", ["ES230"]),
    "Salamanca": ("Salamanca", "Salamanca", "Salamanca", "Salamanca", ["ES415"]),
    "Santa Cruz de Tenerife": ("Santa Cruz de Tenerife", "Santa Cruz de Tenerife", "Santa Cruz de Tenerife", "S.C. Tenerife",
                               ["ES703", "ES706", "ES707", "ES709"]),
    "Segovia": ("Segovia", "Segovia", "Segovia", "Segovia", ["ES416"]),
    "Sevilla": ("Sevilla", "Sevilla", "Seville", "Seville", ["ES618"]),
    "Soria": ("Soria", "Soria", "Soria", "Soria", ["ES417"]),
    "Tarragona": ("Tarragona", "Tarragona", "Tarragona", "Tarragona", ["ES514"]),
    "Teruel": ("Teruel", "Teruel", "Teruel", "Teruel", ["ES242"]),
    "Toledo": ("Toledo", "Toledo", "Toledo", "Toledo", ["ES425"]),
    "Valencia/València": ("Valencia/València", "Valencia", "Valencia", "Valencia", ["ES523"]),
    "Valladolid": ("Valladolid", "Valladolid", "Valladolid", "Valladolid", ["ES418"]),
    "Zamora": ("Zamora", "Zamora", "Zamora", "Zamora", ["ES419"]),
    "Zaragoza": ("Zaragoza", "Zaragoza", "Zaragoza", "Zaragoza", ["ES243"]),
    "Ceuta": ("Ceuta", "Ceuta", "Ceuta", "Ceuta", ["ES630"]),
    "Melilla": ("Melilla", "Melilla", "Melilla", "Melilla", ["ES640"]),
}

NUMBERS = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
           "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
           "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20}


def spanish_number(words):
    """'Treinta y ocho' -> 38 (the decree spells seat counts out)."""
    words = words.lower().strip(" .")
    tens = {"veinti": 20, "treinta": 30, "cuarenta": 40}
    if words in NUMBERS:
        return NUMBERS[words]
    for prefix, value in tens.items():
        if words.startswith(prefix):
            rest = words[len(prefix):].replace(" y ", "").strip()
            return value + (NUMBERS[rest] if rest else 0)
    raise ValueError(words)


def annex_lines(pdf_name):
    import pypdf
    text = "\n".join(page.extract_text() for page in pypdf.PdfReader(common.fetch(pdf_name, URLS[pdf_name])).pages)
    return text[text.index("ANEXO"):].splitlines()


# DATA

def load_provinces(refresh=False):
    if not DATA_CSV.exists() or refresh:
        import html as htmlmod
        seats, pop = {}, {}
        for line in annex_lines("rd806_2026.pdf"):
            for name in PROVINCES:
                if line.startswith(name + "."):
                    seats[name] = spanish_number(line[len(name) + 1:])
        pop_names = {v[0]: k for k, v in PROVINCES.items()}
        for line in annex_lines("rd1117_2025.pdf"):
            m = re.match(r"^(.+?)\.\s+([\d.]+)$", line.strip())
            if m and m.group(1) in pop_names and pop_names[m.group(1)] not in pop:
                pop[pop_names[m.group(1)]] = int(m.group(2).replace(".", ""))
        page = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ",
                      common.fetch("rd400_2023.html", URLS["rd400_2023.html"]).read_text(errors="ignore"))))
        annex = page[page.index("ANEXO"):]
        seats_2023 = {name: int(re.search(re.escape(name) + r"\. (\d+)", annex).group(1)) for name in PROVINCES}
        area, _ = common.eurostat("reg_area3", unit="KM2", landuse="L0008")
        latest = lambda code: [v for v in area[code].values() if v is not None][-1]
        rows = []
        for name, (_, shape, display, label, nuts) in PROVINCES.items():
            rows.append(dict(province=name, shape=shape, name=display, label=label, seats=seats[name],
                             seats_2023=seats_2023[name], pop=pop[name], land_km2=round(sum(latest(c) for c in nuts))))
        pd.DataFrame(rows).to_csv(DATA_CSV, index=False)
    table = pd.read_csv(DATA_CSV)
    table["per_seat"] = table["pop"] / table.seats
    table["weight"] = (table["pop"].sum() / 350) / table.per_seat   # 1 = national average
    table["share"] = table.seats / 350
    return table.sort_values(["seats", "pop"], ascending=False).reset_index(drop=True)


def loreg_162(pop):
    """Seats per constituency under article 162 of the LOREG: 350 seats, two per province,
    one each for Ceuta and Melilla, the other 248 by population with largest remainders."""
    cities = {"Ceuta", "Melilla"}
    provinces = {k: v for k, v in pop.items() if k not in cities}
    quota = sum(provinces.values()) / (350 - 2 * len(provinces) - len(cities))
    extra = {k: int(v // quota) for k, v in provinces.items()}
    left = 350 - 2 * len(provinces) - len(cities) - sum(extra.values())
    for k in sorted(provinces, key=lambda k: provinces[k] / quota - extra[k], reverse=True)[:left]:
        extra[k] += 1
    return {**{k: 2 + extra[k] for k in provinces}, **{k: 1 for k in cities}}


def build_grids(table):
    shapes = common.admin1("ESP")
    shapes["region_name"] = shapes.region_name.map({v[1]: k for k, v in PROVINCES.items()})
    return common.build_grids(shapes, dict(zip(table.province, table.seats.astype(float))), n=350)


# FACTS

def facts(table, grids):
    t = table.set_index("province")
    squares = Counter(cell[0] for landmass in grids["gdp"] for cell in landmass)
    total_pop = table["pop"].sum()
    land = table.land_km2.sum()
    most = table.loc[table.per_seat.idxmin()]
    least = table.loc[table.per_seat.idxmax()]
    small = table[table.seats <= 5]
    changes = table[table.seats != table.seats_2023]
    madrid = t.loc["Madrid"]
    return dict(
        t=t, squares=squares, average=total_pop / 350, most=most, least=least,
        ratio=least.per_seat / most.per_seat,
        small_n=len(small), small_seats=int(small.seats.sum()), small_pop=small["pop"].sum() / total_pop,
        changes=changes, madrid_land=madrid.land_km2 / land, madrid_seats=int(madrid.seats),
        loreg=loreg_162(dict(zip(table.province, table["pop"]))),
    )


def check_claims(table, grids, F):
    t = F["t"]
    gains = F["changes"][F["changes"].seats > F["changes"].seats_2023]
    losses = F["changes"][F["changes"].seats < F["changes"].seats_2023]
    checks = {
        "52 constituencies and 350 seats": len(table) == 52 and table.seats.sum() == 350,
        "one square per seat": all(F["squares"][p] == s for p, s in zip(table.province, table.seats)),
        "the decree matches article 162 of the LOREG applied to the 2025 population":
            all(F["loreg"][p] == s for p, s in zip(table.province, table.seats)),
        "2023 also had 350 seats": table.seats_2023.sum() == 350,
        "only Madrid gains a seat and only Cádiz loses one":
            list(gains.province) == ["Madrid"] and list(losses.province) == ["Cádiz"]
            and (F["changes"].seats - F["changes"].seats_2023).abs().max() == 1,
        "Soria has the fewest residents per seat": F["most"].province == "Soria",
        "Madrid has the most residents per seat": F["least"].province == "Madrid",
        "Madrid elects 38": t.seats["Madrid"] == 38 and t.seats["Soria"] == 2,
        "colour scale covers every province": table.weight.min() >= W_LOW and table.weight.max() <= W_HIGH,
    }
    common.check_claims(checks)


# DESIGN: civic, not partisan. Slate below the national average, ballot gold above it.

INK = "#14213D"
MUTED = "#55607A"
W_LOW, W_MID, W_HIGH = 0.7, 1.0, 3.2
# soft warm: from many residents per seat (brick red) to few (pale cream); the average sits mid-scale
RAMP = ["#A2383A", "#C2614F", "#D98A68", "#E8B184", "#F1CF9E", "#F6E3BA", "#FBF3DD"]



def fmt_int(v):
    return f"{v:,.0f}"


def text_for(table, F, lang="en"):
    """Every word on the poster, in English or Spanish."""
    most, least, t = F["most"], F["least"], F["t"]
    changes = F["changes"].assign(gain=lambda d: d.seats - d.seats_2023).sort_values("gain", ascending=False)
    if lang == "es":
        n = common.es_num
        return dict(
            title="El Congreso",
            subtitle="Por escaños, no por superficie",
            badge="Elecciones generales, 29 de noviembre de 2026",
            dek=[
                "Cada cuadrado es uno de los 350 escaños",
                "del Congreso de los Diputados. Un escaño",
                f"de {most['name']} representa a {n(most.per_seat)} habitantes;",
                f"uno de {least['name']}, a {n(least.per_seat)}: {n(F['ratio'], 1)} veces más.",
            ],
            legend_title="Color: habitantes por escaño",
            legend_low=f"{F['average'] / W_HIGH / 1e3:.0f} mil", legend_high=f"{F['average'] / W_LOW / 1e3:.0f} mil",
            legend_mean=f"España {n(F['average'])}", mean_value=None,
            hero_notes={p: ["1 escaño por cada", f"{t.per_seat[p] / 1e3:.0f} mil habitantes"] for p in ("Madrid", "Barcelona")},
            magnifier=[],
            inset_title="Las mismas provincias, según su superficie",
            note=[f"Madrid tiene el {n(F['madrid_land'] * 100, 1)}%", "del territorio, pero",
                  f"{F['madrid_seats']} de los 350 escaños"],
            band=dict(
                big=f"{F['small_seats']}",
                note=[f"escaños se eligen en las {F['small_n']}", "provincias con cinco o menos,",
                      f"donde vive el {F['small_pop'] * 100:.0f}% de la población"],
                title=f"Solo {'dos' if len(changes) == 2 else len(changes)} cambios desde 2023",
                lines=[f"{r['name']}: de {int(r.seats_2023)} a {int(r.seats)} escaños" for _, r in changes.iterrows()],
            ),
            footer=[
                "Fuente: Real Decreto 806/2026 (BOE, 6 de octubre de 2026), escaños por circunscripción; Real Decreto 400/2023, escaños en 2023; Real Decreto 1117/2025,",
                "cifras oficiales de población a 1 de enero de 2025; Eurostat, superficie terrestre. Los escaños siguen el artículo 162 de la LOREG: dos por provincia,",
                "uno para Ceuta y otro para Melilla, y los 248 restantes según la población. El script los recalcula y comprueba que coinciden con el decreto.",
            ],
            credit=("Josep Ferrer", "databites.tech", "Cuadrícula hecha con popgrid"),
        )
    return dict(
        title="Spain’s Congress",
        subtitle="Sized by Seats, Not Land",
        badge=f"General election, {ELECTION}",
        dek=[
            "Each square is one of the 350 seats in",
            f"the Congress of Deputies. A seat in {most['name']}",
            f"represents {fmt_int(most.per_seat)} residents; one in",
            f"{least['name']}, {fmt_int(least.per_seat)}: {F['ratio']:.1f} times as many.",
        ],
        legend_title="Color: residents per seat",
        legend_low=f"{F['average'] / W_HIGH / 1e3:.0f}K", legend_high=f"{F['average'] / W_LOW / 1e3:.0f}K",
        legend_mean=f"Spain {fmt_int(F['average'])}", mean_value=None,
        hero_notes={p: [f"1 seat per {t.per_seat[p] / 1e3:.0f}K residents"] for p in ("Madrid", "Barcelona")},
        magnifier=[],
        inset_title="The Same Provinces, Sized by Land",
        note=[f"Madrid has {F['madrid_land'] * 100:.1f}% of Spain’s", f"land, but {F['madrid_seats']} of its 350 seats"],
        band=dict(
            big=f"{F['small_seats']}",
            note=[f"seats are elected in the {F['small_n']}", "provinces with five or fewer,",
                  f"home to {F['small_pop'] * 100:.0f}% of residents"],
            title=f"Only {'two' if len(changes) == 2 else len(changes)} changes since 2023",
            lines=[f"{r['name']}: {int(r.seats_2023)} to {int(r.seats)} seats" for _, r in changes.iterrows()],
        ),
        footer=[
            "Source: Real Decreto 806/2026 (BOE, 6 October 2026), seats per constituency; Real Decreto 400/2023, seats in 2023; Real Decreto 1117/2025,",
            "official population on 1 January 2025; Eurostat, land area. Seats follow article 162 of the electoral law (LOREG): two per province, one each",
            "for Ceuta and Melilla, and the other 248 shared by population. The script recomputes them and checks they match the decree.",
        ],
    )


ES_NAMES = {   # province: (name, label) where Spanish differs from English
    "Balears (Illes)": ("Islas Baleares", "Baleares"), "Navarra": ("Navarra", "Navarra"), "Sevilla": ("Sevilla", "Sevilla"),
}

TEXT_SCALE = 1.18   # every text below display size is drawn this much larger than in the GDP posters


class SeatStyle(common.Style):
    def money(self, value):   # labels show seats, not money
        return f"{int(value)}"

    def text(self, x, y, content, size, *args, **kwargs):
        return super().text(x, y, content, size * TEXT_SCALE if size < 30 else size, *args, **kwargs)

    def lines(self, x, y, rows, size, height, *args, **kwargs):
        return super().lines(x, y, rows, size, height * TEXT_SCALE if size < 30 else height, *args, **kwargs)

    def legend(self, x, y, width, title, low, high, mean_label, mean_value=None, mean_size=10):
        """Flush with the right margin: title above, the ends and the average below, fewest residents per seat first."""
        stops = "".join(f'<stop offset="{i / (len(self.ramp) - 1)}" stop-color="{c}"/>'
                        for i, c in enumerate(self.ramp[::-1]))
        tick = x + width / 2   # the average sits mid-scale
        return "".join([
            f'<defs><linearGradient id="legend">{stops}</linearGradient></defs>'
            f'<rect x="{x}" y="{y}" width="{width}" height="11" rx="2" fill="url(#legend)"/>',
            self.text(x + width, y - 8, title, 11, 600, "sans", self.ink, "end"),
            self.text(x, y + 28, low, 11, 700, "sans", self.ink, "start"),
            self.text(x + width, y + 28, high, 11, 700, "sans", self.ink, "end"),
            f'<path d="M{tick:.1f} {y - 2}V{y + 13}" stroke="{self.ink}" stroke-width="1.4"/>',
            self.text(tick, y + 28, mean_label, 10.5, 600, "sans", self.muted),
        ])


S = SeatStyle(INK, MUTED, RAMP, W_LOW, W_HIGH, mid=W_MID, currency="")


class SeatPoster(common.GridPoster):
    def bottom_band(self):
        """The number, its note, the divider and the changes, on one shared top line and baseline."""
        b, y0 = self.T["band"], self.P["band"]["y"]
        note_size, title_size = 12.5, 15     # before TEXT_SCALE
        rows = (y0 + 11, y0 + 31.5, y0 + 52)                 # baselines shared by both blocks
        width = lambda text: len(text) * note_size * TEXT_SCALE * 0.53
        out = [S.text(LEFT - 2, rows[2], b["big"], 74, 900, "serif", INK, "start", 'letter-spacing="-1"')]
        x = LEFT + 132
        out += [S.text(x, y, line, note_size, 500, "sans", INK, "start") for y, line in zip(rows, b["note"])]
        x += max(width(line) for line in b["note"]) + 30
        out.append(f'<path d="M{x:.1f} {y0 - 1}V{y0 + 55}" stroke="{INK}" stroke-width=".8"/>')
        x += 30
        out.append(S.text(x, y0 + 13, b["title"], title_size, 800, "serif", INK, "start"))
        out += [S.text(x, y, line, note_size, 500, "sans", INK, "start") for y, line in zip(rows[1:], b["lines"])]
        return "".join(out)


def header_extra(poster):
    """The election badge, flush with the right margin under the subtitle."""
    T = poster.T
    bw = len(T["badge"]) * 9 + 36
    return (f'<rect x="{RIGHT - bw}" y="{BADGE_Y}" width="{bw}" height="34" rx="17" fill="{INK}"/>'
            + S.text(RIGHT - bw / 2, BADGE_Y + 22.5, T["badge"], 13, 700, "sans", "#ffffff"))


LEFT, RIGHT = 26, 1174   # the page margins every block lines up with
CELL, MAP_X, MAP_Y, COL0, ROW0 = 37, LEFT, 356, -13, 8   # 31 columns: the map spans margin to margin
BADGE_Y = 220


def X(col):
    return MAP_X + (col - COL0) * CELL


def Y(row):
    """Top edge of a row."""
    return MAP_Y + (ROW0 - row) * CELL


P = dict(
    width=1200, height=1520,
    header=dict(subtitle_y=198, dek=(LEFT, 194), dek_size=19, dek_leading=26, dek_anchor="start", legend=(RIGHT - 400, 300, 400)),
    map=dict(cell=CELL, x=MAP_X, y=MAP_Y, pack={"Santa Cruz de Tenerife": (-13, -11, 1), "Palmas (Las)": (-10, -15, 1)}),
    labels={
        "Ceuta": dict(at=(-5, -15), out=(X(-3), Y(-15) + 13)),
        "Melilla": dict(at=(1, -17), out=(X(3), Y(-17) + 13)),
        "Rioja (La)": dict(n=8, v=11.5, dx=-8), "Soria": dict(n=8.5, v=11.5, dx=3),
    },
    hero={"Madrid": dict(name=24, value=40), "Barcelona": dict(name=22, value=36)},
    inset=dict(x=RIGHT - 30 * 8, y=Y(-10) + 24, cell=8, title=(RIGHT, Y(-10) + 2, "end"),
               note=(RIGHT - 30 * 8 - 16, Y(-10) + 84), note_anchor="end", highlight="Madrid", highlight_label=False,
               pack={"Santa Cruz de Tenerife": (-12, -13, 1), "Palmas (Las)": (-12, -15, 1)},
               arrow_from=(RIGHT - 30 * 8 - 6, Y(-10) + 77), arrow_bend=-0.3),
    band=dict(y=Y(-18) + 32),
    footer_y=Y(-18) + 116,
    extra=[header_extra],
)


def build_page(table, grids, font_css, F=None, lang="en"):
    F = F or facts(table, grids)
    if lang == "es":
        table = table.copy()
        for p, (name, label) in ES_NAMES.items():
            table.loc[table.province == p, ["name", "label"]] = [name, label]
        F = dict(F, most=table.loc[F["most"].name], least=table.loc[F["least"].name],
                 changes=table.loc[F["changes"].index])
    data = {r.province: r for r in table.assign(gdp=table.seats, per_person=table.weight).itertuples()}
    poster = SeatPoster(S, data, grids, P, text_for(table, F, lang))
    poster.table = table
    return poster.build(font_css)


def run(args):
    table = load_provinces(args.refresh)
    grids = build_grids(table)
    F = facts(table, grids)
    if not args.skip_checks:
        check_claims(table, grids, F)
    return build_page(table, grids, common.fetch_fonts(), F, getattr(args, "lang", "en")), P["width"], P["height"]


if __name__ == "__main__":
    common.cli(__doc__.split("\n")[1], HERE / "spain-seats-2026.png", run)

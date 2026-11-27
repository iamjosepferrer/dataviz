"""
Shared code for the "Sized by GDP, Not Land" posters.

Each country or region folder keeps its own make_map.py with the parts that are
really its own: data loading, claims, label placement and the page layout. Everything
that is the same on every poster lives here: downloads, fonts, the popgrid grid,
colour, SVG drawing, the magnifier, the land inset and the PNG export.

    import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import common as c
"""

import html
import io
import json
import math
import os
import shutil
import subprocess
import tarfile
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

POPGRID_VERSION = "0.7.1"
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"          # shared by every poster: fonts, Natural Earth, downloads

# Fontsource package -> family, version, (weight, style) faces
FONTS = {
    "playfair-display": ("Playfair Display", "5.3.0", [(700, "normal"), (800, "normal"), (900, "normal")]),
    "inter": ("Inter", "5.3.0", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal")]),
    "source-serif-4": ("Source Serif 4", "5.3.1", [(700, "normal")]),
}
FACES = {"sans": "Inter", "serif": "Playfair Display", "num": "Source Serif 4"}


# DOWNLOADS

def fetch(name, url, folder=None, timeout=180):
    """Download a source file once into the cache and return its path."""
    folder = Path(folder or CACHE)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.exists():
        print(f"Downloading {name}")
        for attempt in range(3):  # some statistics servers drop the first connection
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()
                break
            except requests.exceptions.ConnectionError:
                if attempt == 2:
                    raise
        path.write_bytes(response.content)
    return path


def fetch_fonts():
    """Extract the needed woff2 files from the Fontsource npm tarballs; return @font-face CSS."""
    folder = CACHE / "fonts"
    folder.mkdir(parents=True, exist_ok=True)
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


def load_imf(csv_path, year, refresh=False):
    """GDP by country in USD billions for one year, from a committed snapshot."""
    csv_path = Path(csv_path)
    column = f"gdp_{year}_usd_billions"
    if not csv_path.exists() or refresh:
        url = f"https://www.imf.org/external/datamapper/api/v1/NGDPD?periods={year}"
        raw = json.loads(fetch(f"imf_ngdpd_{year}.json", url).read_text())["values"]["NGDPD"]
        rows = [(code, v[str(year)]) for code, v in raw.items() if str(year) in v and len(code) == 3]
        pd.DataFrame(rows, columns=["code", column]).sort_values("code").to_csv(csv_path, index=False)
    table = pd.read_csv(csv_path, keep_default_na=False)
    return dict(zip(table.code, table[column].astype(float)))


# GRID

def check_popgrid():
    import popgrid
    if popgrid.__version__ != POPGRID_VERSION:
        raise SystemExit(f"popgrid {popgrid.__version__} is installed; these posters are pinned to "
                         f"{POPGRID_VERSION}. Run: pip install popgrid=={POPGRID_VERSION}")


def _squares(grid):
    """[[key, column, row], ...] per landmass. Positions are private in popgrid 0.7.1."""
    if not isinstance(getattr(grid, "_cell_size", None), (int, float)) or not getattr(grid, "_landmasses", None):
        raise SystemExit("popgrid internals changed (_cell_size or _landmasses). Check common._squares().")
    out = []
    size = grid._cell_size
    for landmass in grid._landmasses:
        cells = landmass["cells"]
        if cells is None or len(cells) == 0:
            continue
        bounds = cells.geometry.bounds
        out.append([
            [key, round(x / size), round(y / size)]
            for key, x, y in zip(cells.region_name, bounds.minx, bounds.miny)
        ])
    return out


def repair_quotas(landmasses, quotas):
    """
    popgrid 0.7.1 can give a very small region no square while a neighbour gets one
    too many. Hand the extra square back, taking the donor's westernmost one.
    """
    cells = [cell for landmass in landmasses for cell in landmass]
    counts = Counter(cell[0] for cell in cells)
    short = [key for key, quota in quotas.items() if counts[key] < quota]
    extra = [key for key, quota in quotas.items() if counts[key] > quota]
    if not short and extra:
        # popgrid drew more squares than the quota: drop the donor's most exposed square
        taken = {(c[1], c[2]) for c in cells}
        for donor in extra:
            for _ in range(counts[donor] - quotas[donor]):
                mine = [c for lm in landmasses for c in lm if c[0] == donor]
                square = min(mine, key=lambda c: (sum((c[1] + dc, c[2] + dr) in taken
                                                      for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1))), c[1]))
                for lm in landmasses:
                    if square in lm:
                        lm.remove(square)
                taken.discard((square[1], square[2]))
                print(f"Removed one extra square from {donor}")
        return
    if len(short) != len(extra):
        raise SystemExit(f"Cannot repair squares: short {short}, extra {extra}")
    for needy, donor in zip(short, extra):
        square = min((cell for cell in cells if cell[0] == donor), key=lambda cell: cell[1])
        square[0] = needy
        print(f"Moved one square from {donor} to {needy}")


def build_grids(shapes, weights, n=1000, land=True):
    """
    shapes: GeoDataFrame with region_name and geometry. weights: {region_name: value}.
    Values go in through popgrid's public from_geodataframe(weight_col=...).
    Returns {"gdp": [...], "land": [...]}, one [key, col, row] list per landmass.
    """
    from popgrid import AreaGrid, PopGrid

    check_popgrid()
    missing = set(weights) - set(shapes.region_name)
    if missing:
        raise SystemExit(f"No shape for: {sorted(missing)}")
    shapes = shapes[shapes.region_name.isin(weights)].reset_index(drop=True)
    frame = shapes.assign(weight=shapes.region_name.map(weights).astype(float))
    gdp_grid = PopGrid.from_geodataframe(frame[["region_name", "weight", "geometry"]],
                                         region_col="region_name", weight_col="weight", n=n).build()
    grids = {"gdp": _squares(gdp_grid)}
    repair_quotas(grids["gdp"], gdp_grid.quotas)
    if land:
        land_grid = AreaGrid.from_geodataframe(shapes[["region_name", "geometry"]], region_col="region_name", n=n).build()
        grids["land"] = _squares(land_grid)
    return grids


def admin1(iso3, dissolve_by=None):
    """popgrid's Natural Earth admin-1 regions for a country: region_name, geometry."""
    from popgrid.data import load_country_regions
    return load_country_regions(iso3, dissolve_by=dissolve_by)[["region_name", "geometry"]]


def natural_earth_admin1():
    """The full Natural Earth admin-1 file popgrid caches, as a GeoDataFrame."""
    import geopandas as gpd
    from popgrid.data import _download_admin1
    return gpd.read_file(_download_admin1())


def check_claims(checks):
    """checks: {sentence on the graphic: bool}. Stops the build if one fails."""
    failed = [claim for claim, passed in checks.items() if not passed]
    print(f"Claims checked: {len(checks) - len(failed)} of {len(checks)} pass")
    if failed:
        raise SystemExit("These claims no longer hold:\n  " + "\n  ".join(failed))


EUROPE = {"ALB", "AUT", "BEL", "BGR", "BIH", "CHE", "CYP", "CZE", "DEU", "DNK", "ESP", "EST", "FIN", "FRA",
          "GBR", "GRC", "HRV", "HUN", "IRL", "ISL", "ITA", "LTU", "LUX", "LVA", "MDA", "MKD", "MLT", "MNE",
          "NLD", "NOR", "POL", "PRT", "ROU", "SRB", "SVK", "SVN", "SWE", "UKR"}


def bigger_than(value, imf_table, exclude=(), k=2, names=None, pool=None):
    """
    The k largest recognizable economies below value (closest below is most striking).
    pool: optional set of codes to choose from, e.g. EUROPE for a European region.
    """
    names = names or COMPARE_NAMES
    below = sorted(((gdp, code) for code, gdp in imf_table.items()
                    if code in names and code not in exclude and gdp < value
                    and (pool is None or code in pool)), reverse=True)
    return [code for _, code in below[:k]]


COMPARE_NAMES = {
    "USA": "the U.S.", "CHN": "China", "DEU": "Germany", "JPN": "Japan", "IND": "India",
    "GBR": "the U.K.", "FRA": "France", "ITA": "Italy", "CAN": "Canada", "BRA": "Brazil",
    "RUS": "Russia", "KOR": "South Korea", "AUS": "Australia", "ESP": "Spain", "MEX": "Mexico",
    "IDN": "Indonesia", "TUR": "Türkiye", "NLD": "the Netherlands", "SAU": "Saudi Arabia",
    "CHE": "Switzerland", "POL": "Poland", "ARG": "Argentina", "SWE": "Sweden", "BEL": "Belgium",
    "IRL": "Ireland", "ISR": "Israel", "SGP": "Singapore", "ARE": "the UAE", "NOR": "Norway",
    "THA": "Thailand", "AUT": "Austria", "PHL": "the Philippines", "VNM": "Vietnam",
    "DNK": "Denmark", "ZAF": "South Africa", "EGY": "Egypt", "COL": "Colombia", "CHL": "Chile",
    "PRT": "Portugal", "GRC": "Greece", "NZL": "New Zealand", "PER": "Peru", "FIN": "Finland",
    "CZE": "Czechia", "ROU": "Romania", "HUN": "Hungary", "UKR": "Ukraine", "MAR": "Morocco",
    "NGA": "Nigeria", "PAK": "Pakistan", "BGD": "Bangladesh", "MYS": "Malaysia", "KAZ": "Kazakhstan",
    "SVK": "Slovakia", "LUX": "Luxembourg", "HRV": "Croatia", "BGR": "Bulgaria", "ECU": "Ecuador",
    "URY": "Uruguay", "CRI": "Costa Rica", "SRB": "Serbia", "LTU": "Lithuania", "SVN": "Slovenia",
    "TUN": "Tunisia", "KEN": "Kenya", "ETH": "Ethiopia", "QAT": "Qatar", "KWT": "Kuwait",
    "IRQ": "Iraq", "DZA": "Algeria", "ISL": "Iceland", "EST": "Estonia", "LVA": "Latvia",
    "CYP": "Cyprus", "MLT": "Malta", "BOL": "Bolivia", "PRY": "Paraguay", "GTM": "Guatemala",
    "DOM": "the Dominican Republic", "PAN": "Panama", "LKA": "Sri Lanka", "MMR": "Myanmar",
    "MKD": "North Macedonia", "ALB": "Albania", "BIH": "Bosnia", "SLV": "El Salvador", "HND": "Honduras",
    "JAM": "Jamaica", "MNG": "Mongolia", "GEO": "Georgia", "ARM": "Armenia", "NPL": "Nepal", "KHM": "Cambodia",
}


# COLOUR

def to_rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(to_rgb(a), to_rgb(b)))


def luminance(color):
    channels = [v / 255 for v in to_rgb(color)]
    channels = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


RAMPS = {
    # U.S. green, sequential
    "green": ["#E9F4E4", "#B9DFB4", "#7CC394", "#3B9C74", "#14705A", "#0A4A40", "#052B26"],
    # EU gold to pale to blue, diverging around the average
    "eu": ["#B98A00", "#FFCC00", "#FFE78A", "#EFECE1", "#7F9FE0", "#003399", "#001E63"],
    # Spain: red and yellow of the flag, sequential
    "spain": ["#FFF3C4", "#FFDA5C", "#F6B40E", "#E8792B", "#C8102E", "#8E0B21", "#560514"],
    # Catalonia: yellow to the senyera red
    "catalonia": ["#FFF6CC", "#FCE27A", "#F9C80E", "#EE8F1C", "#DA3A1F", "#A4161A", "#62090C"],
    # Netherlands: oranje, sequential
    "netherlands": ["#FFF1E0", "#FFD3A3", "#FFA552", "#F47A1F", "#C8541A", "#8A3712", "#4F1F0A"],
    # China: gold to red
    "china": ["#FFF4CC", "#FFDE6B", "#FFB81C", "#F07A1E", "#DE2910", "#A3150B", "#5E0A06"],
    # Chile: white to blue, with the flag's blue at the dark end
    "chile": ["#EEF3FA", "#C4D4EE", "#8FAEDC", "#5A85C4", "#2E5FA8", "#0039A6", "#001E5E"],
}


class Style:
    """One poster's ink, muted text, ramp, colour scale and currency."""

    def __init__(self, ink, muted, ramp, low, high, mid=None, currency="$"):
        self.ink, self.muted = ink, muted
        self.ramp = RAMPS[ramp] if isinstance(ramp, str) else ramp
        self.low, self.mid, self.high = low, mid, high
        self.currency = currency

    def color(self, per_person):
        """Sequential scale from low to high, or diverging around mid when mid is set."""
        ramp = self.ramp
        if self.mid is None:
            position = max(0, min(1, (per_person - self.low) / (self.high - self.low))) * (len(ramp) - 1)
        else:
            if per_person <= self.mid:
                position = 0.5 * max(0, per_person - self.low) / (self.mid - self.low)
            else:
                position = 0.5 + 0.5 * min(1, (per_person - self.mid) / (self.high - self.mid))
            position *= len(ramp) - 1
        i = min(int(position), len(ramp) - 2)
        return mix(ramp[i], ramp[i + 1], position - i)

    def text_color(self, background):
        """White or ink, whichever has more contrast against the background."""
        on_white = 1.05 / (luminance(background) + 0.05)
        on_ink = (luminance(background) + 0.05) / (luminance(self.ink) + 0.05)
        return "#ffffff" if on_white > on_ink else self.ink

    def money(self, value):
        cur = self.currency
        if value >= 1e12:
            return f"{cur}{value / 1e12:.2f}T"
        if value >= 1e9:
            return f"{cur}{value / 1e9:.0f}B"
        return f"{cur}{value / 1e6:.0f}M"

    # SVG

    def text(self, x, y, content, size, weight=700, family="sans", fill=None, anchor="middle",
             extra="", halo=None, halo_width=3.5):
        """SVG text. A halo in the square's color cuts the grid lines behind the label."""
        if halo:
            extra += f' paint-order="stroke" stroke="{halo}" stroke-width="{halo_width}" stroke-linejoin="round"'
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FACES[family]}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill or self.ink}" text-anchor="{anchor}" style="font-feature-settings:\'lnum\' 1" {extra}>'
            f"{html.escape(content)}</text>"
        )

    def lines(self, x, y, rows, size, height, weight=500, family="sans", fill=None, anchor="start"):
        return "".join(self.text(x, y + i * height, row, size, weight, family, fill, anchor) for i, row in enumerate(rows))

    def leader(self, x1, y1, path_tail):
        """Thin line with a white casing and a dot on the region."""
        d = f"M{x1:.1f} {y1:.1f}{path_tail}"
        return (
            f'<path d="{d}" fill="none" stroke="#fff" stroke-width="2.6" stroke-opacity=".85"/>'
            f'<path d="{d}" fill="none" stroke="{self.ink}" stroke-width=".9"/>'
            f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="2.2" fill="#fff" stroke="{self.ink}" stroke-width=".9"/>'
        )

    def name_and_value(self, x, y, name, value, anchor):
        """Name and value on one line, so the gap stays even whatever the length of the name."""
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{self.ink}" style="font-feature-settings:\'lnum\' 1">'
            f'<tspan font-family="Inter" font-size="10.5" font-weight="700">{html.escape(name)}</tspan>'
            f'<tspan dx="6" font-family="Source Serif 4" font-size="12.5" font-weight="700">{html.escape(value)}</tspan></text>'
        )

    def squares(self, cells, size, origin_x, origin_y, fill_of, gap=0.6, radius=1.2, border=1.7, tint=0.30):
        """Draw {(col, row): key} as squares, with an outline around each region."""
        px = lambda col: origin_x + col * size
        py = lambda row: origin_y - (row + 1) * size
        out = []
        for (col, row), key in cells.items():
            fill = fill_of(key)
            out.append(
                f'<rect x="{px(col):.2f}" y="{py(row):.2f}" width="{size}" height="{size}" fill="{mix(fill, "#ffffff", tint)}"/>'
                f'<rect x="{px(col) + gap:.2f}" y="{py(row) + gap:.2f}" width="{size - 2 * gap:.2f}" '
                f'height="{size - 2 * gap:.2f}" rx="{radius}" fill="{fill}"/>'
            )
        edges = []
        for (col, row), key in cells.items():
            x, y = px(col), py(row)
            if cells.get((col - 1, row)) != key:
                edges.append(f"M{x:.2f} {y:.2f}V{y + size:.2f}")
            if cells.get((col + 1, row)) != key:
                edges.append(f"M{x + size:.2f} {y:.2f}V{y + size:.2f}")
            if cells.get((col, row + 1)) != key:
                edges.append(f"M{x:.2f} {y:.2f}H{x + size:.2f}")
            if cells.get((col, row - 1)) != key:
                edges.append(f"M{x:.2f} {y + size:.2f}H{x + size:.2f}")
        out.append(f'<path d="{"".join(edges)}" fill="none" stroke="{self.ink}" stroke-width="{border}" stroke-linecap="square"/>')
        return "".join(out)

    def legend(self, x, y, width, title, low, high, mean_label, mean_value=None, mean_size=10):
        """Gradient bar with its ends, a title above and a tick for the average."""
        ramp = self.ramp
        stops = "".join(f'<stop offset="{i / (len(ramp) - 1)}" stop-color="{c}"/>' for i, c in enumerate(ramp))
        if mean_value is None:
            tick = width / 2
        else:
            tick = (mean_value - self.low) / (self.high - self.low) * width
        return "".join([
            f'<defs><linearGradient id="legend">{stops}</linearGradient></defs>'
            f'<rect x="{x}" y="{y}" width="{width}" height="11" rx="2" fill="url(#legend)"/>',
            self.text(x, y - 6, title, 11, 600, "sans", self.ink, "start"),
            self.text(x - 5, y + 10, low, 11.5, 700, "num", self.ink, "end"),
            self.text(x + width + 5, y + 10, high, 11.5, 700, "num", self.ink, "start"),
            f'<path d="M{x + tick:.1f} {y - 2}V{y + 13}" stroke="{self.ink}" stroke-width="1.4"/>',
            self.text(x + tick, y + 25, mean_label, mean_size, 600, "sans", self.muted),
        ])

    def magnifier(self, cells, center, radius, start, title, value, caption, fill_of,
                  size=11, title_dy=-40, value_dy=44, value_size=21, block_dy=-6, ring=None, title_size=12):
        """
        A circle with a tiny region's squares drawn larger, joined to its real spot.
        cells: {(col, row): key} of that region; start: (x, y) of the leader on the map.
        """
        cx, cy = center
        start_x, start_y = start
        angle = math.atan2(cy - start_y, cx - start_x)
        end_x, end_y = cx - radius * math.cos(angle), cy - radius * math.sin(angle)
        stroke = f'stroke="{ring[0]}" stroke-width="{ring[1]}"' if ring else f'stroke="{self.ink}" stroke-width="1.6"'
        out = [
            self.leader(start_x, start_y, f"L{end_x:.1f} {end_y:.1f}"),
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="#fff" {stroke}/>',
        ]
        cols = [c for c, _ in cells]
        rows = [r for _, r in cells]
        block_w = (max(cols) - min(cols) + 1) * size
        block_h = (max(rows) - min(rows) + 1) * size
        out.append(self.squares(
            cells, size, cx - block_w / 2 - min(cols) * size, cy + block_dy + block_h / 2 + min(rows) * size,
            fill_of, gap=0.5, border=1.2,
        ))
        out.append(self.text(cx, cy + title_dy, title, title_size, 700))
        out.append(self.text(cx, cy + value_dy, value, value_size, 900, "serif"))
        out.append(self.lines(cx, cy + radius + 18, caption, 11.5, 14.5, weight=600, anchor="middle"))
        return "".join(out)

    def footer(self, y, rows, width=1200, credit=("Josep Ferrer", "databites.tech", "Grid built with popgrid")):
        right = width - 26
        return "".join([
            f'<path d="M26 {y}H{right}" stroke="{self.ink}" stroke-width="1"/>',
            self.lines(26, y + 20, rows, 10.5, 14.5, weight=400, fill=self.muted),
            self.lines(right, y + 20, list(credit), 11, 14.5, weight=600, anchor="end"),
        ])


def largest_rectangle(cells, max_height=16):
    """Largest, squarest rectangle inside a region: (col, row, width, height)."""
    best = (0, 0, 0, 0, 0)
    for col, row in sorted(cells):
        for height in range(1, max_height):
            if (col, row + height - 1) not in cells:
                break
            width = 1
            while all((col + width, row + k) in cells for k in range(height)):
                width += 1
            score = width * height - abs(width - height) * 0.15
            if score > best[0]:
                best = (score, col, row, width, height)
    return best[1:]


def fill_holes(land):
    """Close one-square holes in a land grid, so they do not read as territory."""
    for col in range(min(c for c, _ in land), max(c for c, _ in land)):
        for row in range(min(r for _, r in land), max(r for _, r in land)):
            around = [land.get(k) for k in ((col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1))]
            if (col, row) not in land and all(around):
                land[(col, row)] = max(set(around), key=around.count)
    return land


# PAGE AND PNG

def page(svg, font_css, width, height):
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{font_css}'
        "*{margin:0;padding:0}body{background:#fff}text{font-kerning:normal}</style></head><body>"
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{svg}</svg>'
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


def screenshot(page_html, out, width, height, scale=2):
    """Open the page in headless Chrome and save it as a PNG."""
    CACHE.mkdir(parents=True, exist_ok=True)
    page_path = CACHE / f"page-{Path(out).stem}.html"
    page_path.write_text(page_html, encoding="utf-8")
    out = Path(out).resolve()
    command = [
        find_chrome(), "--headless", "--disable-gpu", "--hide-scrollbars",
        f"--window-size={width},{height}", f"--force-device-scale-factor={scale}",
        "--virtual-time-budget=5000", f"--screenshot={out}", page_path.as_uri(),
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # Chrome refuses to run sandboxed as root
        command.insert(1, "--no-sandbox")
    subprocess.run(command, check=True, capture_output=True)


def cli(description, default_out, run):
    """Shared command line: --out, --scale, --skip-checks, --refresh, --html."""
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--out", default=str(default_out))
    parser.add_argument("--scale", type=float, default=2, help="pixel density; 2 gives 2400 px wide")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="download current figures first")
    parser.add_argument("--html", help="also save the page as HTML here (for checks)")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    page_html, width, height = run(args)
    if args.html:
        Path(args.html).write_text(page_html, encoding="utf-8")
    screenshot(page_html, args.out, width, height, args.scale)
    print(f"Saved {args.out}")


# SOURCES USED BY SEVERAL POSTERS

def eurostat(dataset, **params):
    """{geo: {year: value}} from the Eurostat JSON API, plus {geo: label}."""
    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
    response = requests.get(url, params=dict(format="JSON", lang="EN", **params), timeout=180)
    response.raise_for_status()
    data = response.json()
    geo = data["dimension"]["geo"]["category"]
    years = data["dimension"]["time"]["category"]["index"]
    n = len(years)
    values = {g: {y: data["value"].get(str(i * n + j)) for y, j in years.items()} for g, i in geo["index"].items()}
    return values, geo["label"]


def oecd_regions(country, year, cache_name="oecd_regional_gdp.csv"):
    """
    OECD Regional Database, large regions (TL2): GDP and GDP per person in current U.S.
    dollars, and population, for one country and year. Returns a DataFrame indexed by
    region code with columns name, gdp_usd, gdp_usd_per_person, pop.
    """
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.CFE.EDS,DSD_REG_ECO@DF_GDP,/all"
           f"?startPeriod={year}&endPeriod={year}&dimensionAtObservation=AllDimensions&format=csvfilewithlabels")
    table = pd.read_csv(fetch(f"{year}-{cache_name}", url, timeout=600), low_memory=False)
    table = table[(table.COUNTRY == country) & (table.TERRITORIAL_LEVEL == "TL2") & (table.TIME_PERIOD == year)]
    pick = lambda measure, unit: table[(table.MEASURE == measure) & (table.UNIT_MEASURE == unit)].set_index("REF_AREA")
    gdp, per_person, pop = pick("GDP", "USD"), pick("GDP", "USD_PS"), pick("POP", "PS")
    out = pd.DataFrame({
        "name": gdp["Reference area"],
        "gdp_usd": gdp.OBS_VALUE * 10.0 ** gdp.UNIT_MULT.fillna(0),
        "gdp_usd_per_person": per_person.OBS_VALUE,
        "pop": pop.OBS_VALUE * 10.0 ** pop.UNIT_MULT.fillna(0),
    })
    return out


def ecb_usd_per_eur(year):
    """ECB reference rate, annual average: U.S. dollars per euro."""
    url = (f"https://data-api.ecb.europa.eu/service/data/EXR/A.USD.EUR.SP00.A"
           f"?startPeriod={year}&endPeriod={year}&format=csvdata")
    return float(pd.read_csv(fetch(f"ecb_usd_{year}.csv", url)).OBS_VALUE.iloc[0])


def equal_area_km2(shapes):
    """Area of each region in km2, measured on its polygons in an equal-area projection."""
    return dict(zip(shapes.region_name, shapes.to_crs("EPSG:6933").area / 1e6))


def word(n):
    words = "zero one two three four five six seven eight nine ten eleven twelve".split()
    return words[n] if n < len(words) else str(n)


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# GENERIC POSTER (used by every poster after the U.S. and Europe)
#
# Same look as the U.S. and Europe maps. A poster is described by a dict `P`
# (positions and sizes, all hand-tunable) and a dict `T` (every sentence, built
# from computed numbers in make_map.py and checked there).

def pack(cells, left, top, gap=1, align=True):
    """
    Bring an archipelago's islands side by side: each island (a connected group of
    squares) keeps its shape, islands are ordered west to east with `gap` empty
    columns between them, and the group's top-left corner lands at (left, top).
    """
    cells = set(cells)
    groups, seen = [], set()
    for cell in sorted(cells):
        if cell in seen:
            continue
        group, stack = [], [cell]
        seen.add(cell)
        while stack:
            c, r = stack.pop()
            group.append((c, r))
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        groups.append(group)
    groups.sort(key=lambda g: min(c for c, _ in g))
    top_row = max(r for _, r in cells)
    out, x = [], left
    for group in groups:
        c0 = min(c for c, _ in group)
        r0 = max(r for _, r in group) if align else top_row   # align: every island hangs from `top`
        out += [(x + c - c0, top + r - r0) for c, r in group]
        x += max(c for c, _ in group) - c0 + 1 + gap
    return out


class GridPoster:
    def __init__(self, style, data, grids, P, T):
        """
        data: {key: row} with .name, .label, .gdp, .per_person
        grids: {"gdp": [...], "land": [...]} from build_grids
        """
        self.S, self.data, self.grids, self.P, self.T = style, data, grids, P, T
        m = P["map"]
        self.CELL = m["cell"]
        main = {}
        packed = {}
        for landmass in grids["gdp"]:
            for key, col, row in landmass:
                if key in m.get("pack", {}):
                    packed.setdefault(key, []).append((col, row))
                    continue
                dc, dr = m.get("move", {}).get(key, (0, 0))
                main[(col + dc, row + dr)] = key
        for key, cells in packed.items():
            target = m["pack"][key]
            for cell in pack(cells, target[0], target[1], target[2] if len(target) > 2 else 1):
                main[cell] = key
        self.main = main
        cols = [c for c, _ in main]
        rows = [r for _, r in main]
        self.COL0, self.ROW0 = min(cols), max(rows)
        self.MAP_X, self.MAP_Y = m["x"], m["y"]

    def X(self, col):
        return self.MAP_X + (col - self.COL0) * self.CELL

    def Y(self, row):
        """Top edge of a row. Rows count upward, as in the projected map."""
        return self.MAP_Y + (self.ROW0 - row) * self.CELL

    def fill_of(self, key):
        return self.S.color(self.data[key].per_person)

    # parts

    def header(self):
        S, P, T = self.S, self.P, self.T
        h = P["header"]
        out = [
            S.text(24, 122, T["title"], h.get("title_size", 136), 900, "serif", S.ink, "start", 'letter-spacing="-2.4"'),
            S.text(P["width"] - 26, h.get("subtitle_y", 180), T["subtitle"], 44, 700, "serif", S.ink, "end", 'letter-spacing="-.4"'),
        ]
        x, y = h["dek"]
        out.append(S.lines(x, y, T["dek"], h.get("dek_size", 14), h.get("dek_leading", 19.5)))
        lx, ly, lw = h["legend"]
        out.append(S.legend(lx, ly, lw, T["legend_title"], T["legend_low"], T["legend_high"],
                            T["legend_mean"], None if S.mid is not None else T["mean_value"]))
        return "".join(out)

    def labels(self):
        S, P, CELL = self.S, self.P, self.CELL
        px = lambda col: self.MAP_X + (col - self.COL0) * CELL
        py = lambda row: self.MAP_Y + (self.ROW0 - row) * CELL
        cells_of = {}
        for cell, key in self.main.items():
            cells_of.setdefault(key, set()).add(cell)
        conf_all, heroes = P.get("labels", {}), P.get("hero", {})
        out = []
        for key, cells in cells_of.items():
            conf = conf_all.get(key, {})
            if conf.get("out") or conf.get("skip"):
                continue
            col, row, w, h = conf.get("rect") or largest_rectangle(cells)
            cx = px(col) + w * CELL / 2 + conf.get("dx", 0)
            cy = py(row + h - 1) + h * CELL / 2 + conf.get("dy", 0)
            fill = self.fill_of(key)
            ink = S.text_color(fill)
            value = S.money(self.data[key].gdp)
            if key in heroes:
                hero = heroes[key]
                size = hero["value"]
                cx, cy = cx + hero.get("dx", 0), cy + hero.get("dy", 0)
                out.append(S.text(cx, cy - size * 0.62, self.data[key].name, hero["name"], 800, "serif", ink, halo=fill, halo_width=5))
                out.append(S.text(cx, cy + size * 0.5, value, size, 900, "serif", ink, halo=fill, halo_width=6))
                for i, note in enumerate(self.T["hero_notes"].get(key, [])):
                    out.append(S.text(cx, cy + size * 0.5 + 17 + i * 14.5, note, 11.5, 600, "sans", ink, halo=fill, halo_width=4))
                continue
            name = self.data[key].label
            v_size, n_size = conf.get("v", 12.5), conf.get("n", 10)
            if conf.get("one_line"):
                value_w = len(value) * v_size * 0.53
                name_w = len(name) * n_size * 0.62
                x0 = cx - (name_w + 4 + value_w) / 2
                out.append(S.text(x0, cy + n_size * 0.36, name, n_size, 600, "sans", ink, "start", halo=fill))
                out.append(S.text(x0 + name_w + 4, cy + v_size * 0.34, value, v_size, 700, "num", ink, "start", halo=fill))
            else:
                out.append(S.text(cx, cy - v_size * 0.30, name, n_size, 600, "sans", ink, halo=fill))
                out.append(S.text(cx, cy + v_size * 0.78, value, v_size, 700, "num", ink, halo=fill))
        for key, conf in conf_all.items():
            if not conf.get("out"):
                continue
            x, y = conf["out"]
            col, row = conf["at"]
            tail = conf.get("tail") or (f"V{y:.1f}H{x:.1f}" if conf.get("up") else f"L{x:.1f} {y:.1f}")
            out.append(S.leader(px(col) + CELL / 2, py(row) + CELL / 2, tail))
            end = conf.get("anchor") == "end"
            out.append(S.name_and_value(x + (-4 if end else 4), y + 4.4, self.data[key].label,
                                        S.money(self.data[key].gdp), "end" if end else "start"))
        return "".join(out)

    def magnifier(self):
        mg = self.P.get("magnifier")
        if not mg:
            return ""
        key = mg["key"]
        cells = {cell: k for cell, k in self.main.items() if k == key}
        col, row = mg["at"]
        start = (self.X(col) + self.CELL / 2, self.Y(row) + self.CELL / 2)
        return self.S.magnifier(cells, mg["center"], mg.get("radius", 60), start, self.data[key].label,
                                self.S.money(self.data[key].gdp), self.T["magnifier"], self.fill_of,
                                size=mg.get("size", 12), ring=mg.get("ring"))

    def land_inset(self):
        S, ins = self.S, self.P["inset"]
        land, packed = {}, {}
        for landmass in self.grids["land"]:
            for key, col, row in landmass:
                if col < ins.get("min_col", -10**9):   # far-off islands left out of the inset
                    continue
                if key in ins.get("pack", {}):
                    packed.setdefault(key, []).append((col, row))
                    continue
                dc, dr = ins.get("move", {}).get(key, (0, 0))
                land[(col + dc, row + dr)] = key
        fill_holes(land)
        for key, cells in packed.items():
            target = ins["pack"][key]
            for cell in pack(cells, target[0], target[1], target[2] if len(target) > 2 else 1):
                land[cell] = key
        size, left, top = ins["cell"], ins["x"], ins["y"]
        origin_x = left - min(c for c, _ in land) * size
        origin_y = top + (max(r for _, r in land) + 1) * size
        out = [S.squares(land, size, origin_x, origin_y, self.fill_of, gap=0, radius=0, border=0.7, tint=0)]
        for key in ins.get("outline", []):  # a group of regions, outlined without labels
            region = {cell: k for cell, k in land.items() if k == key}
            out.append(S.squares(region, size, origin_x, origin_y, self.fill_of, gap=0, radius=0, border=1.8, tint=0))
        key = ins.get("highlight")
        if key:
            region = {cell: k for cell, k in land.items() if k == key}
            cx = origin_x + (sum(c for c, _ in region) / len(region) + 0.5) * size
            cy = origin_y - (sum(r for _, r in region) / len(region) + 0.5) * size
            out.append(S.squares(region, size, origin_x, origin_y, self.fill_of, gap=0, radius=0, border=1.8, tint=0))
            if ins.get("highlight_label", True):
                out.append(S.text(cx + ins.get("label_dx", 0), cy + 3.5 + ins.get("label_dy", 0), self.data[key].label,
                                  10, 700, "sans", S.text_color(self.fill_of(key)), halo=self.fill_of(key), halo_width=2.5))
            self.highlight_xy = (cx, cy)
        tx, ty, anchor = ins["title"]
        out.append(S.text(tx, ty, self.T["inset_title"], 17, 800, "serif", S.ink, anchor))
        nx, ny = ins["note"]
        out.append(S.lines(nx, ny, self.T["note"], ins.get("note_size", 17), ins.get("note_leading", 22.5), weight=800, family="serif"))
        if ins.get("arrow_from") and key:
            (sx, sy), (tx, ty) = ins["arrow_from"], self.highlight_xy
            bend = ins.get("arrow_bend", 0.25)
            qx, qy = (sx + tx) / 2 - (ty - sy) * bend, (sy + ty) / 2 + (tx - sx) * bend
            ins = dict(ins, arrow=f"M{sx:.1f} {sy:.1f}Q{qx:.1f} {qy:.1f} {tx:.1f} {ty:.1f}")
        if ins.get("arrow"):
            out.append(
                f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
                f'<path d="M0 0L10 5L0 10" fill="none" stroke="{S.ink}" stroke-width="1.6"/></marker></defs>'
                f'<path d="{ins["arrow"]}" fill="none" stroke="{S.ink}" stroke-width="1.4" marker-end="url(#arrow)"/>')
        return "".join(out)

    def bottom_band(self):
        S, b = self.S, self.P["band"]
        y, x = b["y"], b.get("x", 44)
        out = []
        for i, block in enumerate(self.T["stats"]):
            if i:
                out.append(f'<path d="M{x - 24} {y - 44}V{y + 12}" stroke="{S.ink}" stroke-width=".8"/>')
            if "big" in block:
                out.append(S.text(x, y, block["big"], block.get("size", 52), 900, "serif", b.get("accent", S.ink), "start", 'letter-spacing="-.8"'))
                nx = x + block.get("gap", 114)
                out.append(S.lines(nx, y - 31, block["note"], 12.5, 17))
                x = nx + block.get("note_width", 210) + 24
            else:
                out.append(S.text(x, y - 22, block["title"], 17, 800, "serif", S.ink, "start"))
                out.append(S.lines(x, y, block["note"], 12.5, 17))
                x += block.get("width", 300) + 24
        return "".join(out)

    def extra(self):
        return "".join(f(self) for f in self.P.get("extra", []))

    def build(self, font_css):
        P = self.P
        origin_x = self.MAP_X - self.COL0 * self.CELL
        origin_y = self.MAP_Y + (self.ROW0 + 1) * self.CELL
        svg = "".join([
            self.header(),
            self.S.squares(self.main, self.CELL, origin_x, origin_y, self.fill_of),
            self.labels(),
            self.magnifier(),
            self.land_inset(),
            self.bottom_band(),
            self.extra(),
            self.S.footer(P["footer_y"], self.T["footer"], P["width"]),
        ])
        return page(svg, font_css, P["width"], P["height"])


def wrap_note(content, width=24):
    """Split a short note into lines of at most `width` characters."""
    lines, line = [], ""
    for w in content.split():
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + [line]


def ascii_grid(grids, which="gdp", labels=None):
    """Print the grid as text with one letter per region, to plan label positions."""
    cells = {(c, r): k for lm in grids[which] for k, c, r in lm}
    keys = sorted(set(cells.values()))
    letters = labels or {k: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"[i % 62] for i, k in enumerate(keys)}
    cols = [c for c, _ in cells]
    rows = [r for _, r in cells]
    print("    " + "".join(str(abs(c) % 10) for c in range(min(cols), max(cols) + 1)))
    for r in range(max(rows), min(rows) - 1, -1):
        print(f"{r:4d}" + "".join(letters.get(cells.get((c, r)), ".") for c in range(min(cols), max(cols) + 1)))
    print({letters[k]: k for k in keys})

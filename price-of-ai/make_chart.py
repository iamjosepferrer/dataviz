#!/usr/bin/env python3
"""
The Price of AI
Josep Ferrer, databites.tech

Log-scale chart of API prices per million tokens since GPT-4 launched in March 2023.
One line follows the cheapest model at least as good as GPT-4 on GPQA Diamond,
at the price charged by the company that built it.
The dots show flagship models from OpenAI, Anthropic and Google at launch.

Setup:
    pip install -r requirements.txt
    Google Chrome, Chromium or Edge must be installed (it renders the PNG).

Run:
    python make_chart.py               # writes price-of-ai.png
    python make_chart.py --scale 1

Data, committed so the chart stays reproducible:
    flagships.csv           launch list prices for each flagship
    same_level.csv          each new low in the price of GPT-4-level AI, and its source
    third_party.csv         the lowest third-party host price seen on November 3, 2026
    gpqa_diamond_epoch.csv  GPQA Diamond scores from Epoch AI's Benchmarking Hub
"""

import argparse
import html
import io
import math
import os
import shutil
import subprocess
import tarfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"

FONTS = {
    "playfair-display": ("Playfair Display", "5.3.0", [(700, "normal"), (800, "normal"), (900, "normal")]),
    "inter": ("Inter", "5.3.0", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal")]),
    "source-serif-4": ("Source Serif 4", "5.3.1", [(700, "normal")]),
}

WORDS_PER_TOKEN = 0.75
WORDS_PER_NOVEL = 90_000


# DATA

def blend(frame):
    """Three parts input to one part output, the mix used by Epoch AI and the AI Index."""
    frame["price"] = (3 * frame.input + frame.output) / 4
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def load():
    flagships = blend(pd.read_csv(HERE / "flagships.csv"))
    same_level = blend(pd.read_csv(HERE / "same_level.csv")).sort_values("date").reset_index(drop=True)
    third_party = blend(pd.read_csv(HERE / "third_party.csv"))
    scores = pd.read_csv(HERE / "gpqa_diamond_epoch.csv")
    return flagships, same_level, third_party, scores


def check_claims(flagships, same_level, third_party, scores):
    """Recompute every claim printed on the graphic."""
    first, last = same_level.iloc[0], same_level.iloc[-1]
    newest = flagships[flagships.date >= "2026-06-01"]
    top_today = newest.price.max()
    cheaper = first.price / last.price
    words_then = 1e6 / first.price * WORDS_PER_TOKEN
    words_now = 1e6 / last.price * WORDS_PER_TOKEN
    score = scores.set_index("model_version").score
    errors = scores.set_index("model_version").stderr
    gpt4 = score["gpt-4-0314"]
    open_host = third_party.iloc[0]

    checks = {
        "every step is a new low": same_level.price.is_monotonic_decreasing and same_level.price.is_unique,
        "every step scores at least GPT-4": all(score[name] >= gpt4 for name in same_level.gpqa_version),
        "the third-party price also qualifies": score[open_host.gpqa_version] >= gpt4,
        "the third-party price is below the line": open_host.price < last.price,
        "the third-party price prints as $0.023": money(open_host.price) == "$0.023",
        "GPT-4 starts at $37.50": first.price == 37.5,
        "nearly 700x cheaper": 680 < cheaper < 700,
        "the line ends at $0.055": last.price == 0.055,
        "the priciest new flagships cost $20": top_today == 20,
        "frontier about 2x cheaper, $20 about half of GPT-4": 1.8 < first.price / top_today < 2.0,
        "GPT-4 scores 36%": round(gpt4 * 100) == 36,
        "Qwen3.7 Flash scores 82%": round(score[last.gpqa_version] * 100) == 82,
        "$1 bought 20,000 words": round(words_then) == 20_000,
        "$1 buys 13.6 million words": round(words_now / 1e6, 1) == 13.6,
        "about 150 novels": 145 < words_now / WORDS_PER_NOVEL < 155,
        "682 squares": round(cheaper) == 682,
        "364x gap": round(top_today / last.price) == 364,
        "GPT-4.5 is the priciest": flagships.price.idxmax() == flagships.index[flagships.model == "GPT-4.5"][0],
        "GPT-4.5 is $93.75": flagships.price.max() == 93.75,
        "Claude 3 Haiku and GPT-4o mini are within the margin of error": all(
            0 <= score[m] - gpt4 < errors[m] for m in ("claude-3-haiku-20240307", "gpt-4o-mini-2024-07-18")
        ),
        "the other steps clear GPT-4 by more than the margin of error": all(
            score[m] - gpt4 > errors[m] for m in same_level.gpqa_version[1:]
            if m not in ("claude-3-haiku-20240307", "gpt-4o-mini-2024-07-18")
        ),
        "others launch as low as $4": newest.price.min() == 4,
        "o1-preview and o1 share $26.25": all(flagships[flagships.model == m].price.iloc[0] == 26.25 for m in ("o1-preview", "o1")),
        "Gemini 4 Argon is $4 now, $8 from December": flagships[flagships.model == "Gemini 4 Argon"].price.iloc[0] == 4 and (3 * 4 + 20) / 4 == 8,
        "Gemini 1.0 Pro is the lowest, about $1.25": flagships.price.idxmin() == flagships.index[flagships.model == "Gemini 1.0 Pro"][0] and flagships.price.min() == 1.25,
        "Gemini 2.5 Pro, GPT-5 and GPT-5.1 are at $3.44": set(flagships[flagships.price.round(2) == 3.44].model) == {"Gemini 2.5 Pro", "GPT-5", "GPT-5.1"},
        "the newest flagships at $20 are the three named": set(newest[newest.price == 20].model) == {"Claude Fable 5", "Claude Fable 5.1", "GPT-6 Astra"},
    }
    failed = [claim for claim, passed in checks.items() if not passed]
    print(f"Claims checked: {len(checks) - len(failed)} of {len(checks)} pass")
    if failed:
        raise SystemExit("These claims no longer hold:\n  " + "\n  ".join(failed))


# DESIGN

WIDTH, HEIGHT = 1200, 1282
BG = "#0B1020"
INK = "#F4F1EA"
MUTED = "#A3ABBF"
GRID = "#2A3148"
CORAL = "#FF7A59"
MINT = "#4FE0B5"

LEFT, RIGHT, TOP, BOTTOM = 92, 1070, 300, 1080
START, END = date(2023, 1, 1), date(2027, 1, 1)
PRICE_MAX, PRICE_MIN = 150, 0.016


def x_of(day):
    day = day.date() if hasattr(day, "date") else day
    return LEFT + (day - START).days / (END - START).days * (RIGHT - LEFT)


def y_of(price):
    span = math.log10(PRICE_MAX) - math.log10(PRICE_MIN)
    return TOP + (math.log10(PRICE_MAX) - math.log10(price)) / span * (BOTTOM - TOP)


def money(price):
    if price >= 1:
        return f"${price:.2f}".replace(".00", "")
    if price >= 0.1:
        return f"${price:.2f}"
    return f"${price + 1e-9:.3f}"


def text(x, y, content, size, weight=500, family="sans", fill=INK, anchor="start", extra=""):
    face = {"sans": "Inter", "serif": "Playfair Display", "num": "Source Serif 4"}[family]
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{face}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}" style="font-feature-settings:\'lnum\' 1" {extra}>'
        f"{html.escape(content)}</text>"
    )


def lines(x, y, rows, size, height, weight=500, family="sans", fill=INK, anchor="start"):
    return "".join(text(x, y + i * height, row, size, weight, family, fill, anchor) for i, row in enumerate(rows))


def halo(x, y, content, size, weight, family, fill, anchor="start"):
    """Text with a background-colored outline, so it stays clear of the gridlines."""
    return text(x, y, content, size, weight, family, fill, anchor,
                f'paint-order="stroke" stroke="{BG}" stroke-width="4" stroke-linejoin="round"')


# THE PAGE

def header():
    return "".join([
        text(24, 128, "The Price of AI", 146, 900, "serif", INK, "start", 'letter-spacing="-2.4"'),
        text(1174, 182, "GPT-4-Level AI Is Nearly 700x Cheaper. The Priciest Flagships, Just 2x.", 31, 700, "serif", INK, "end"),
        lines(26, 226, [
            "Since GPT-4 launched, the cheapest model that matches it has fallen from $37.50 to $0.055 per million tokens.",
            "The priciest new flagships still launch at $20, about half of what GPT-4 cost, while others launch as low as $4.",
        ], 15, 21),
    ])


def axes():
    out = []
    for price, label in ((100, "$100"), (10, "$10"), (1, "$1"), (0.1, "$0.10")):
        y = y_of(price)
        out.append(f'<path d="M{LEFT} {y:.1f}H{RIGHT}" stroke="{GRID}" stroke-width="1"/>')
        out.append(text(LEFT - 10, y + 4.5, label, 13, 700, "num", MUTED, "end"))
    for year in range(2023, 2027):
        x = x_of(date(year, 1, 1))
        out.append(f'<path d="M{x:.1f} {TOP}V{BOTTOM}" stroke="{GRID}" stroke-width="1"/>')
        out.append(text(x + 6, BOTTOM + 20, str(year), 13, 600, "sans", MUTED))
    out.append(f'<path d="M{LEFT} {BOTTOM}H{RIGHT}" stroke="{MUTED}" stroke-width="1.2"/>')
    return "".join(out)


def same_level_line(same_level):
    points = [(x_of(r.date), y_of(r.price)) for r in same_level.itertuples()]
    d = f"M{points[0][0]:.1f} {points[0][1]:.1f}"
    for (x, y) in points[1:]:
        d += f"H{x:.1f}V{y:.1f}"
    d += f"H{x_of(date(2026, 11, 3)):.1f}"
    out = [f'<path d="{d}" fill="none" stroke="{MINT}" stroke-width="4" stroke-linejoin="round"/>']
    offsets = {
        "GPT-4 Turbo": (10, -9, "start"), "Mistral Large": (11, -5, "start"), "Claude 3 Sonnet": (-9, 4, "end"), "Claude 3 Haiku": (10, -9, "start"),
        "GPT-4o mini": (8, -9, "start"), "Gemini 1.5 Flash": (10, -8, "start"), "Qwen Turbo": (10, -9, "start"),
    }
    for r in same_level.itertuples():
        x, y = x_of(r.date), y_of(r.price)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{BG}" stroke="{MINT}" stroke-width="2.5"/>')
        if r.model in offsets:
            dx, dy, anchor = offsets[r.model]
            out.append(halo(x + dx, y + dy, f"{r.model} {money(r.price)}", 12.5, 700, "sans", MINT, anchor))
    return "".join(out)


def flagship_dots(flagships):
    out = []
    # (dx, dy, anchor) from the dot, for the labeled models
    offsets = {
        "GPT-4": (12, -9, "start"), "Claude 2": (0, -13, "middle"), "Claude 3 Opus": (9, -11, "start"),
        "Gemini 1.5 Pro": (10, 17, "start"), "Claude Opus 4": (10, -10, "start"),
        "Claude Fable 5": (-10, -12, "end"),
    }
    # Released two days apart at the same price, so they are nudged apart to show both
    nudge = {"Claude Fable 5.1": -6, "GPT-6 Astra": 6}
    for r in flagships.itertuples():
        x, y = x_of(r.date) + nudge.get(r.model, 0), y_of(r.price)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6.5" fill="{CORAL}" stroke="{BG}" stroke-width="1.6"/>')
        if r.label and r.model in offsets:
            dx, dy, anchor = offsets[r.model]
            out.append(halo(x + dx, y + dy, f"{r.model} {money(r.price)}", 12.5, 600, "sans", INK, anchor))

    def spot(model):
        row = flagships[flagships.model == model].iloc[0]
        return x_of(row.date), y_of(row.price)

    x, y = spot("o1-preview")
    out.append(halo(x - 4, y - 11, "o1-preview, o1 $26.25", 12.5, 600, "sans", INK))

    x, y = spot("GPT-4.5")
    out.append(halo(x + 12, y - 10, "GPT-4.5 $93.75", 14, 700, "sans", INK))
    out.append(halo(x + 12, y + 7, "Priciest flagship launch", 11.5, 500, "sans", MUTED))

    x, y = spot("Gemini 1.0 Pro")
    out.append(halo(x - 11, y - 1, "Gemini 1.0 Pro ~$1.25", 12.5, 700, "sans", INK, "end"))
    out.append(halo(x - 11, y + 14, "Lowest flagship launch", 11.5, 500, "sans", MUTED, "end"))

    x0, y = spot("Gemini 2.5 Pro")
    x1, _ = spot("GPT-5.1")
    out.append(halo(x1 + 8, y + 25, "Gemini 2.5 Pro, GPT-5, GPT-5.1 $3.44", 12.5, 600, "sans", INK, "end"))

    x, y = spot("Claude Fable 5.1")
    out.append(halo(x + 8, y - 28, "Claude Fable 5.1,", 12.5, 600, "sans", INK))
    out.append(halo(x + 8, y - 12, "GPT-6 Astra $20", 12.5, 600, "sans", INK))

    x, y = spot("Gemini 4 Argon")
    out.append(halo(x + 4, y + 25, "Gemini 4 Argon $4", 12.5, 600, "sans", INK, "end"))
    out.append(halo(x + 4, y + 41, "Introductory price, $8 from December 1", 11, 500, "sans", MUTED, "end"))
    x8, y8 = x_of(date(2026, 12, 1)), y_of(8)
    out.append(f'<path d="M{x + 5:.1f} {y - 5:.1f}L{x8 - 4:.1f} {y8 + 4:.1f}" stroke="{CORAL}" stroke-width="1.2" stroke-dasharray="2 3"/>')
    out.append(f'<circle cx="{x8:.1f}" cy="{y8:.1f}" r="5.5" fill="{BG}" stroke="{CORAL}" stroke-width="2"/>')
    return "".join(out)


def callouts(same_level, third_party):
    first, last = same_level.iloc[0], same_level.iloc[-1]
    open_host = third_party.iloc[0]
    y0 = y_of(first.price)
    x_end = x_of(date(2026, 11, 3))
    y_low, y_high = y_of(last.price), y_of(20)
    bracket_x = RIGHT + 16
    host_x, host_y = x_of(open_host.date), y_of(open_host.price)
    return "".join([
        f'<path d="M{x_of(first.date):.1f} {y0:.1f}H{RIGHT}" stroke="{CORAL}" stroke-width="1" stroke-dasharray="3 5" stroke-opacity=".7"/>',
        f'<path d="M{x_end:.1f} {TOP}V{BOTTOM}" stroke="{MUTED}" stroke-width="1" stroke-dasharray="2 4"/>',
        text(x_end, BOTTOM + 20, "Nov 3", 13, 600, "sans", MUTED, "middle"),
        halo(x_of(date(2025, 12, 1)), y0 - 7, "GPT-4\u2019s launch price, $37.50", 11.5, 600, "sans", CORAL),
        f'<circle cx="{x_end:.1f}" cy="{y_low:.1f}" r="7" fill="{MINT}"/>',
        text(x_end - 8, y_low + 25, last.model, 13, 700, "sans", MINT, "end"),
        text(x_end - 8, y_low + 61, money(last.price), 36, 900, "serif", MINT, "end"),
        lines(x_end - 8, y_low + 79, ["Qwen3.7 Flash scores 82% on GPQA", "Diamond. GPT-4 scored 36%."], 11.5, 14.5, weight=500, fill=MUTED, anchor="end"),
        f'<circle cx="{host_x:.1f}" cy="{host_y:.1f}" r="5" fill="{BG}" stroke="{MUTED}" stroke-width="2"/>',
        text(host_x - 11, host_y - 2, f"{open_host.model} {money(open_host.price)}", 12, 600, "sans", MUTED, "end"),
        text(host_x - 11, host_y + 13, "on a third-party host", 11, 500, "sans", MUTED, "end"),
        f'<path d="M{bracket_x - 6} {y_high:.1f}H{bracket_x}V{y_low:.1f}H{bracket_x - 6}" fill="none" stroke="{INK}" stroke-width="1.4"/>',
        text(bracket_x + 10, (y_high + y_low) / 2 - 12, "364x", 28, 900, "serif", INK),
        lines(bracket_x + 10, (y_high + y_low) / 2 + 8, ["price gap", "today"], 12, 15, weight=500, fill=MUTED),
    ])


def dollar_panel(same_level):
    first, last = same_level.iloc[0], same_level.iloc[-1]
    squares = round(first.price / last.price)
    x, y = 112, 748
    size, per_row = 8, 27
    out = [
        f'<rect x="{x - 12}" y="{y - 14}" width="256" height="292" fill="{BG}"/>',
        text(x, y, "What $1 Buys", 22, 800, "serif", INK),
        f'<rect x="{x}" y="{y + 18}" width="{size - 1}" height="{size - 1}" fill="{CORAL}"/>',
        text(x + 16, y + 26, "2023: 20,000 words with GPT-4", 12, 600, "sans", INK),
    ]
    top = y + 44
    for i in range(squares):
        col, row = i % per_row, i // per_row
        out.append(f'<rect x="{x + col * size}" y="{top + row * size}" width="{size - 1}" height="{size - 1}" fill="{MINT}"/>')
    rows = math.ceil(squares / per_row)
    bottom = top + rows * size + 20
    out.append(lines(x, bottom, ["2026: 682 times as much, or 13.6", "million words. About 150 novels."], 12, 16, weight=600))
    return "".join(out)


def legend():
    x, y = 26, 284
    x2 = x + 318
    x3 = x2 + 342
    return "".join([
        text(1174, y, "Price per million tokens, log scale", 12.5, 600, "sans", MUTED, "end"),
        f'<circle cx="{x + 7}" cy="{y - 4}" r="6.5" fill="{CORAL}"/>',
        text(x + 19, y, "Flagship at launch (OpenAI, Anthropic, Google)", 12.5, 600),
        f'<path d="M{x2} {y - 4}H{x2 + 28}" stroke="{MINT}" stroke-width="4"/>',
        f'<circle cx="{x2 + 14}" cy="{y - 4}" r="5" fill="{BG}" stroke="{MINT}" stroke-width="2.5"/>',
        text(x2 + 36, y, "New low at GPT-4 level, at the developer\u2019s price", 12.5, 600),
        f'<circle cx="{x3 + 6}" cy="{y - 4}" r="5" fill="{BG}" stroke="{MUTED}" stroke-width="2"/>',
        text(x3 + 18, y, "Third-party host", 12.5, 600),
        f'<circle cx="{x3 + 142}" cy="{y - 4}" r="5.5" fill="{BG}" stroke="{CORAL}" stroke-width="2"/>',
        text(x3 + 153, y, "Scheduled price", 12.5, 600),
    ])


def footer():
    y = 1112
    return "".join([
        f'<path d="M26 {y}H1174" stroke="{MUTED}" stroke-width="1"/>',
        lines(26, y + 22, [
            "Price per million tokens, blending three parts input to one part output, as Epoch AI and the Stanford AI Index do. List prices at the shortest context tier, excluding batch discounts",
            "and free tiers. Flagships: the top generally available API tier from OpenAI, Anthropic and Google at each release, excluding Pro tiers, at first priced public availability. GPT-4 and GPT-4",
            "Turbo are both flagships and new lows. Gemini 1.0 Pro is approximate, converted from per-character pricing; Gemini 1.0 Ultra was allowlist only. \u201cAs good as GPT-4\u201d means a GPQA Diamond",
            "score at or above GPT-4\u2019s 36%, a PhD-level science test, among models scored by Epoch AI. Claude 3 Haiku and GPT-4o mini clear it within the test\u2019s margin of error. The line uses each",
            "developer\u2019s own price at release or after a price cut. Per-token prices do not capture reasoning models using more tokens per task. Words assume 0.75 per token, novels 90,000 words.",
            "",
            "Source: Epoch AI Benchmarking Hub (CC BY); OpenAI, Anthropic, Google, Alibaba and Mistral pricing; TechCrunch; OpenRouter (third-party price, November 3, 2026); llm-prices.com.",
        ], 10.5, 14.5, weight=400, fill=MUTED),
        lines(1174, y + 102, ["Josep Ferrer", "databites.tech"], 11, 14.5, weight=600, anchor="end"),
    ])


def build_page(flagships, same_level, third_party, font_css):
    svg = "".join([
        header(),
        legend(),
        axes(),
        callouts(same_level, third_party),
        same_level_line(same_level),
        flagship_dots(flagships),
        dollar_panel(same_level),
        footer(),
    ])
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{font_css}'
        f"*{{margin:0;padding:0}}body{{background:{BG}}}text{{font-kerning:normal}}</style></head><body>"
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>{svg}</svg></body></html>'
    )


def fetch_fonts():
    """Extract the needed woff2 files from the Fontsource npm tarballs."""
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
    page_path = CACHE / "chart.html"
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
    parser.add_argument("--out", default=str(HERE / "price-of-ai.png"))
    parser.add_argument("--scale", type=float, default=2, help="pixel density; 2 gives 2400 px wide")
    parser.add_argument("--skip-checks", action="store_true")
    args = parser.parse_args()

    CACHE.mkdir(exist_ok=True)
    flagships, same_level, third_party, scores = load()
    if not args.skip_checks:
        check_claims(flagships, same_level, third_party, scores)
    screenshot(build_page(flagships, same_level, third_party, fetch_fonts()), args.out, args.scale)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()

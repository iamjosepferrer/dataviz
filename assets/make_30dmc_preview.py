"""Build the #30DayMapChallenge preview for the dataviz README.

Usage:
    python make_30dmc_preview.py path/to/30daymapchallenge [out.png]

Takes the headline PNG of every finished day in 2026/, in order, and lays
them out in a grid of 5 per row. Run it again after each new day.
"""
import re
import sys
from pathlib import Path

from PIL import Image

COLS = 5
THUMB_W, THUMB_H = 480, 600  # same 4:5 ratio as the maps (2160x2700)
GAP = 20
BG = "white"

repo = Path(sys.argv[1]).expanduser() / "2026"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("assets/30daymapchallenge-2026.png")

# The README's image for each day is the headline map, so read it from there
readme = (repo / "README.md").read_text(encoding="utf-8")
images = re.findall(r"^## Day \d+.*?\n+!\[[^\]]*\]\(([^)]+\.png)\)", readme, flags=re.M | re.S)
paths = [repo / p for p in images if (repo / p).exists()]
if not paths:
    sys.exit("No day images found")

rows = -(-len(paths) // COLS)
sheet = Image.new("RGB", (COLS * THUMB_W + (COLS - 1) * GAP, rows * THUMB_H + (rows - 1) * GAP), BG)
for i, p in enumerate(paths):
    thumb = Image.open(p).convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)
    r, c = divmod(i, COLS)
    sheet.paste(thumb, (c * (THUMB_W + GAP), r * (THUMB_H + GAP)))

out.parent.mkdir(parents=True, exist_ok=True)
sheet.save(out, optimize=True)
print(f"{len(paths)} maps -> {out} ({sheet.width}x{sheet.height})\n")

# The list for the dataviz README, one line per day, linking to its section
URL = "https://github.com/iamjosepferrer/30daymapchallenge/blob/main/2026/README.md"
for title, alt in re.findall(r"^## (Day \d+: [^\n]+)\n+!\[([^\]]*)\]", readme, flags=re.M):
    anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
    # Lowercase the first word unless it is a name (Europe, Spain) or a number
    first = alt.split()[0]
    common = {"a", "an", "the", "half", "only", "most", "almost", "every", "one", "all", "more", "fewer"}
    finding = alt[0].lower() + alt[1:] if first.lower() in common else alt
    print(f"- [{title.replace(':', ',')}]({URL}#{anchor}): {finding}")

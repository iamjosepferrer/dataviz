# Spain's Economy, Sized by 2025 GDP, Not Land

![Spain's Economy, Sized by 2025 GDP, Not Land](spain-economy-by-gdp.png)

Versión en español: [La economía española, por PIB de 2025, no por superficie](spain-economy-by-gdp-es.png)

A tiled map of Spain's 17 autonomous communities and the cities of Ceuta and Melilla, with 1,000 squares, each one 0.1% of their combined GDP (about €1.7 billion). Square count shows GDP, color shows GDP per person. A small land map of the same regions sits alongside for contrast. Amounts are in euros.

The squares come from [popgrid](https://github.com/databites-tech/popgrid). Shared drawing code lives in [`../common.py`](../common.py).

## Scope

- 2025 figures are INE's first estimate (published 30 September 2026). INE revises them in December 2026; rerun with `--refresh` then.
- Extra-regional GDP is left out.
- The Canary Islands are moved next to the peninsula, with their islands side by side.

## Run it

Needs Python and an installed Chrome, Chromium or Edge, which renders the PNG.

```bash
pip install -r requirements.txt
python make_map.py            # rebuilds spain-economy-by-gdp.png from the snapshot
python make_map.py --refresh  # downloads current figures first
python make_map.py --lang es  # Spanish version, spain-economy-by-gdp-es.png
```

The script checks every claim on the graphic against the data and stops if one no longer holds. Revised figures can trigger this after `--refresh`.

## Sources

- [INE, Spanish Regional Accounts](https://www.ine.es/dyngs/Prensa/en/avCRE2025.htm): GDP at current prices and population by autonomous community, 2025 (tables 82284 and 80113)
- [Eurostat](https://ec.europa.eu/eurostat/databrowser/view/reg_area3/default/table): land area (reg_area3)
- [IMF World Economic Outlook](https://www.imf.org/en/publications/weo): 2025 GDP by country, for the comparisons
- [ECB](https://data.ecb.europa.eu/): 2025 average euro reference rate

Snapshots of the figures are in `data.csv` and `imf_2025.csv`.

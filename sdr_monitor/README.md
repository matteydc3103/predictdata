# SDR Swap Monitor

Trade blotter over Bloomberg's Swap Data Repository dissemination feed
(`SDR <GO>`, asset class **Rates**, tab **Vanilla**): every vanilla
fixed-float IRS printed to the market, filtered and enriched into a
tick-refreshing table. Two front ends share one pipeline
(`sdr_core.py`):

- **`sdr_blotter.py`** — runs locally on your own machine, renders the
  blotter to a self-refreshing **HTML page** fed by SDR CSV exports.
- **`sdr_swap_monitor.ipynb`** — the BQuant notebook app (ipydatagrid),
  for when the direct BQL feed entitlement is wired up.

## What it shows

| Column | Meaning |
|---|---|
| `tenor` | expiration − effective date (American `MM/DD/YYYY` dates), snapped to the standard tenor grid with tolerance for business-day adjustment — a 10y whose maturity rolled a few business days still labels `10y`; broken dates show as decimals (`9.71y`) |
| `rate` | traded fixed rate |
| `notional` | notional, `+` preserved for SDR-capped prints (`250mm+`) |
| `dv01` | par-annuity approximation `N × (1−(1+r)^−T)/r × 1bp`, discounted for forward starts |
| `index` | floating leg index (ESTR, EURIBOR 6M, …) |
| `time` | execution timestamp, sorted newest first |
| `platform` | execution venue |
| `related` | trades submitted at the exact same timestamp are one package — legs share a tag (`P1`, `P2`, …) |

Filters: currency = **EUR** (UI-switchable); platforms **TWSF / TREU / BBSF /
BMTF excluded**; non-vanilla products (swaptions, caps/floors, FRAs, XCCY,
inflation) dropped. Venue codes are renamed for display: BGCD→BGC,
TPSE→TP/ICAP, TSEF→TRADS, GSEF→GFI. The HTML blotter has a per-column
filter row under the headers (persists across the auto-reload).

## Trade classification rules

Same-timestamp prints are one risk transfer and collapse to a single row,
with the level quoted in **bp**. First, legs with identical tenor + index +
rate at one second merge as clips of the same trade (sizes and dv01 sum —
e.g. four 30y prints, 2×25mm at each of two levels, become one 50mm
eurex/lch). Then:

| Pattern | Reported as |
|---|---|
| 2 legs, same tenor + notional, different index | `3s6s basis` (etc.) — level = higher index rate − lower (6M−3M, 12M−6M) |
| 2 legs, same tenor + notional + index | `eurex/lch` — level = \|rate difference\| |
| 2 legs, different tenors | curve trade, tenor `10s30s` — level = long − short, size/dv01 of the longer leg |
| 3 legs, distinct tenors | fly, tenor `8s9s10s` — level = 2×belly − wings, belly size/dv01 |
| anything else at one timestamp | legs kept separate, tagged `P1`, `P2`, … |

Single prints with a rate quoted to **5 decimal places** get a `GADGET`
note: `5Y GADGET` for tenors ≤ 6y, else `10Y GADGET`.

## Running locally (HTML blotter)

Needs only Python with `pandas`/`numpy` — no Bloomberg libraries.

```
python sdr_monitor/sdr_blotter.py --watch
```

With no arguments this finds the Bloomberg export drop folder automatically:
it takes the **newest `grid*` file in `C:\blp\data`** (grid.csv, grid.xls,
grid(1).csv … whichever the terminal wrote last). It writes
`sdr_blotter.html`, opens it in your browser, and keeps watching: every
re-export from `SDR <GO>` (Rates / Vanilla tab → *Actions → Export*) shows
up on the page's next auto-reload (default every 15s) — numbered copies
included, since the newest match is re-resolved on every poll. The workflow
is: terminal does the exporting, the script does the monitoring.

Useful flags:

| Flag | Meaning |
|---|---|
| `--csv PATH` | export location — a file, a folder (newest file inside), or a stem like `C:/blp/data/grid` (newest `grid*` match). Default: `./sdr_export.csv`, then `C:/blp/data/grid*`, then `~/Downloads/sdr_export.csv` |
| `--watch` | keep running and re-render whenever the CSV changes |
| `--demo` | synthetic prints, no export needed (page carries a DEMO badge) |
| `--ccy` / `--exclude` | filters (default `EUR`, `TWSF,TREU,BBSF`) |
| `--reload N` | page auto-reload seconds (0 = off) · `--out`, `--max-rows`, `--no-open` |

Exports with title/preamble lines above the header are handled — the reader
scans for the real SDR header row and sniffs the delimiter. Excel-format
grid exports (`.xls`/`.xlsx`) work too (needs `openpyxl` for xlsx, `xlrd`
for legacy xls); Lotus `.wk1` output is rejected with a pointer to choose
CSV/Excel in the export dialog.

## Running in BQuant (notebook)

1. Upload `sdr_swap_monitor.ipynb` to BQuant and **Run All Cells** — the app
   is the last cell and auto-refreshes (default 30s, configurable in the UI).
2. Pick a data source in the dropdown:
   - **SDR feed (BQL)** — SDR dissemination data is an entitlement-dependent
     BQL dataset; paste your account's query string into `SDR_BQL_QUERY` in
     the config cell. Incoming columns are auto-mapped from common header
     synonyms, so schema differences are absorbed.
   - **CSV export** — `SDR <GO>` Vanilla tab → *Actions → Export*, save as
     `sdr_export.csv` next to the notebook; the file is re-read every refresh.
   - **Demo data** — synthetic prints (clearly labelled) so the whole pipeline
     and UI run anywhere, including outside BQuant.

All tunables (currency, excluded venues, refresh interval, row cap, CSV path,
BQL query) live in the single config cell.

## Structure

`sdr_core.py` holds the whole pipeline (column normalisation from header
synonyms, vanilla product filter, currency/platform filters, tenor snapping,
DV01, package tagging, export-file sniffing) — pure pandas, no Bloomberg
dependencies, covered by `tests/test_sdr_core.py`. The blotter script and
the notebook are thin front ends over it (the notebook embeds a copy of the
core so it stays a single uploadable file).

## Next iterations

- Wire the live SDR entitlement query once confirmed (replaces demo default).
- Curve-based DV01 (discount off the live ESTR curve instead of the flat
  traded-rate annuity).
- Forward-start labelling (`1y5y` style) and package classification
  (curve / fly / compression).
- Intraday analytics on the tape: volume by tenor, ΣDV01 tape, large-print
  alerts.

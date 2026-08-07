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

Filters: currency = **EUR** (UI-switchable); platforms **TWSF / TREU / BBSF
excluded**; non-vanilla products (swaptions, caps/floors, FRAs, basis, XCCY,
inflation) dropped.

## Running locally (HTML blotter)

Needs only Python with `pandas`/`numpy` — no Bloomberg libraries.

```
python sdr_monitor/sdr_blotter.py --csv "C:/Users/you/Downloads/sdr_export.csv" --watch
```

This writes `sdr_blotter.html`, opens it in your browser, and keeps watching
the export file: every time you re-export from `SDR <GO>` (Rates / Vanilla
tab → *Actions → Export*) to the same path, the page updates on its next
auto-reload (default every 15s). The workflow is: terminal does the
exporting, the script does the monitoring.

Useful flags:

| Flag | Meaning |
|---|---|
| `--csv PATH` | export location (default: `./sdr_export.csv`, then `~/Downloads/sdr_export.csv`) |
| `--watch` | keep running and re-render whenever the CSV changes |
| `--demo` | synthetic prints, no export needed (page carries a DEMO badge) |
| `--ccy` / `--exclude` | filters (default `EUR`, `TWSF,TREU,BBSF`) |
| `--reload N` | page auto-reload seconds (0 = off) · `--out`, `--max-rows`, `--no-open` |

Exports with title/preamble lines above the header are handled — the reader
scans for the real SDR header row and sniffs the delimiter.

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

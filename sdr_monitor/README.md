# SDR Swap Monitor (BQuant)

Live BQuant dashboard over Bloomberg's Swap Data Repository dissemination
feed (`SDR <GO>`, asset class **Rates**, tab **Vanilla**): every vanilla
fixed-float IRS printed to the market, filtered and enriched into a
tick-refreshing table.

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

## Running it

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

Config → environment → core logic (pure pandas, no BQL) → data layer (one
function per source) → UI. The core logic is BQL-free and covered by the
local checks used during development (tenor snapping incl. business-day
drift, capped-notional parsing, DV01 sanity, package tagging, platform and
currency filters), so feed problems localise to the data-layer cell.

## Next iterations

- Wire the live SDR entitlement query once confirmed (replaces demo default).
- Curve-based DV01 (discount off the live ESTR curve instead of the flat
  traded-rate annuity).
- Forward-start labelling (`1y5y` style) and package classification
  (curve / fly / compression).
- Intraday analytics on the tape: volume by tenor, ΣDV01 tape, large-print
  alerts.

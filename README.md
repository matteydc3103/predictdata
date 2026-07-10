# Economist Prediction Rankings

Rank economists (Santander's Stephen Stanley, J.P. Morgan's Michael Feroli,
Goldman's Jan Hatzius, ...) by how accurately they predict economic data
releases over the last 10 years, so you can see which forecasters are
actually worth following.

## Quick start

```bash
pip install -r requirements.txt
python -m economist_rankings
```

That runs the full pipeline on the bundled sample data and writes
`output/rankings.xlsx` with five sheets:

| Sheet | Contents |
|---|---|
| Overall Ranking | One row per economist, best forecaster first |
| By Indicator | Separate leaderboard per indicator (CPI, GDP, unemployment) |
| By Year | Skill score per economist per year |
| Forecast Detail | Every individual forecast with its error and percentile |
| Notes | Metric definitions and data provenance |

## Using your own forecast data

**The bundled forecasts are SYNTHETIC.** The economist names are real, but
their numbers were simulated (`scripts/generate_sample_data.py`) purely so
the pipeline runs out of the box. Do not treat the sample rankings as real
assessments of those people.

To rank real forecasts, point the CLI at your own spreadsheet:

```bash
python -m economist_rankings --forecasts my_forecasts.xlsx --output output/rankings.xlsx
```

The file (Excel or CSV) needs one row per forecast with these columns
(header names are matched flexibly - "Forecaster"/"Firm"/"Prediction" etc.
all work):

| economist | institution | indicator | period | forecast | actual (optional) |
|---|---|---|---|---|---|
| Stephen Stanley | Santander | cpi_yoy_dec | 2023 | 3.2 | 3.4 |

Actual released values come from `data/actuals.csv` (matched on
`indicator` + `period`); an `actual` column inside your forecast file fills
any gaps, so a self-contained spreadsheet also works with `--actuals none`.
Periods can be any label (`2023`, `2023Q2`, `2023-06`) as long as forecasts
and actuals use the same labels.

### Where to get real individual-economist forecasts

Individual named forecasts are mostly proprietary, but these sources exist:

- **WSJ Economic Forecasting Survey** - named economists, downloadable
  spreadsheets, long history.
- **Bloomberg ECFC / economist surveys** - named contributor forecasts per
  release (terminal required).
- **Philadelphia Fed Survey of Professional Forecasters** - free,
  40+ years of individual forecasts, but forecasters are anonymised IDs.

Export any of these into the column format above and run the CLI.

## How the ranking works

Raw errors aren't comparable across indicators (CPI is in percent, payrolls
in thousands), so the headline **skill_score** is scale-free: for every
release, economists are ranked against each other by absolute error and
given a percentile (100 = closest call, 0 = furthest). An economist's skill
score is their average percentile across all releases - consistently
beating the pack scores high no matter which indicator it was.

Also reported per economist:

- **norm_abs_error** - average miss relative to the average forecaster's
  miss on the same release (below 1.0 = better than the pack).
- **mae / rmse** - errors in the indicator's own units (compare within one
  indicator only).
- **bias** - mean signed error; positive means they tend to forecast too high.

Use `--min-forecasts N` (default 5) to keep economists with only a handful
of forecasts out of the overall leaderboard.

## Refreshing actual values

`data/actuals.csv` ships with approximate US actuals for 2015-2024
(December CPI YoY, December unemployment rate, annual real GDP growth).
When you have network access to FRED, regenerate it with the exact numbers:

```bash
python scripts/refresh_actuals.py --start 2015
```

## Project layout

```
economist_rankings/
  forecasts.py   # flexible CSV/Excel loading + column normalisation
  scoring.py     # per-forecast errors, within-release percentiles
  ranking.py     # overall / per-indicator / per-year leaderboards
  report.py      # formatted Excel output
  cli.py         # command-line interface
data/
  actuals.csv                    # released values (indicator, period, actual)
  forecasts_sample_SYNTHETIC.csv # simulated demo forecasts
scripts/
  generate_sample_data.py        # rebuilds the synthetic sample
  refresh_actuals.py             # rebuilds actuals.csv from FRED
```

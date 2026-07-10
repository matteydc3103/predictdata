# Economist Prediction Rankings

Two components live here:

1. **`nfp_analysis/`** - statistical tests and a trade strategy built on the
   Bloomberg NFP forecaster panel (102 releases, Jan 2018 - Jun 2026, 7,720
   individual submissions). See [NFP analysis](#nfp-forecaster-analysis--trade-signal) below.
2. **`economist_rankings/`** - the generic forecast-ranking pipeline that
   works on any indicator/forecast spreadsheet.

## NFP forecaster analysis & trade signal

Raw survey inputs are in `data/nfp/` (extracted from the LIVE workbook);
everything else is recomputed from scratch - firm metrics match the workbook
to 5 decimals (pinned by `tests/test_nfp_validation.py`).

```bash
pip install -r requirements.txt
python -m nfp_analysis report      # every test -> output/nfp_report.xlsx
python -m nfp_analysis backtest    # walk-forward stats to the console
python -m nfp_analysis signal      # live playbook for the next print
python -m pytest tests/            # validate against workbook values
```

### What the tests found

| Test | Result |
|---|---|
| Firm skill (74 qualified firms, FDR-corrected) | Only 4CAST/Continuum's positive IC survives (q=0.048); Credit Agricole's IC is significantly *negative* (q=0.089). Everything else is indistinguishable from noise. |
| Persistence (split-half) | Rank correlations 0.04-0.09, all p>0.45 - past accuracy does not predict future accuracy. |
| Pooled IC | -0.003 (p=0.83): deviating from consensus carries no information on average. |
| Top-5 IC agreement signal | **The workbook's claimed 74% hit / p=0.017 does not replicate** - ~20 definitional variants give 40-71% hit, none significant, and the signal fails the base-rate permutation null (p≈0.29). Its apparent edge is mostly the period's upside-surprise base rate. |
| Consensus bias | The robust finding: the survey median systematically low-balls payrolls. Mean z-surprise +0.95 full sample (t=2.8, p=0.006), +1.45 in 2022-26 (t=3.4, p=0.001), attenuating in 2024-26 (p=0.07). |

### The recommended strategy: dispersion regime

A systematic feature screen (8 walk-forward-legal candidates tested
against the surprise) found exactly one strong directional predictor:
**relative panel dispersion**. When forecasters disagree more than usual,
surprises skew negative; when they cluster tightly, surprises skew
positive (Spearman r=-0.33, p=0.003, survives Bonferroni; dispersion
averages 2.0x its norm before downside surprises vs 1.3x before upside,
Mann-Whitney p=0.01). Disagreement is a downside-risk barometer the
consensus median fails to price.

`nfp_analysis/strategy.py`, all walk-forward (no lookahead - a release's
dispersion is known before the print since the survey closes days ahead):

- **Direction**: `disp_rel` = release dispersion / trailing-24-release
  median dispersion. Long the surprise when disp_rel ≤ 1.0, short when
  ≥ 1.3, stand aside between.
- **Size**: 1.5x when the trailing-24 bias t-stat agrees with the
  direction (|t| ≥ 1.5), 0.5x when it disagrees, else 1.0x.
- **Expression**: positive expected surprise = short front-end rates
  (2y note / SOFR futures) / long USD into the print; negative = reverse.
- **2022-26 walk-forward**: 53 fires on 55 releases, **77% hit rate,
  t=4.35, permutation-vs-base-rate p=0.003**, halves 77%/78%, shorts
  7/10 (vs 31% base rate), ~+43k average surprise captured per event.
  Every threshold cell in the 0.9-1.1 x 1.2-1.5 grid gives t ≥ 3.5, and
  the rule kept working in 2024-26 after the raw long bias faded (76%,
  t=2.84). Recent live behaviour: short before Feb-26 (surprise -147k)
  and Jun-26 (-56k), both correct.
- **Benchmarks** (same window): always-long 69%/t=3.4; bias tilt
  61%/t=2.33; top-5 IC agreement 67%/t=0.57 and fails the base-rate null.

Caveats, stated plainly: the dispersion feature was screened on the full
sample, so the 2022-26 evaluation overlaps its discovery data - the
defence is threshold-insensitivity, subperiod stability, and mechanism,
not a clean holdout. 2018-21 was breakeven-ish (+0.68 avg z, t=0.8).
Scored against first print only; market reaction, slippage and costs are
not modelled. ~10 events per year fire.

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

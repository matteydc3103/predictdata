# Euro swap spread rich/cheap monitor (RXAISPE)

BQuant notebook that estimates whether the **Bund future invoice spread vs
ESTR (`RXAISPE`)** looks rich or cheap, and shows the full history of that
judgement on a z-score basis.

## Notebook

`bund_estr_asw_richcheap.ipynb` — open in BQuant on the Bloomberg Terminal
and *Run All Cells*. The last cell renders a live app with a Refresh button.

## Model

1. **Factors** (all tunable in the CONFIG cell): `EUFR0CF Curncy`,
   Euribor–ESTR basis `(TKY2 − ER2) × 100`, `EUSS0210 Curncy` (2s10s),
   `GTDEM10Y Govt`, `MOVE Index`, `UXYAISPE Comdty`, 10y BTP–Bund
   (`DBRBTP10Y Index`, with a `GBTPGR10 − GDBR10` fallback), and 3m10y EUR
   swaption vol (`EUSV0310 Curncy` by default — swap for your preferred
   vol ticker).
2. **Covariance diagnostics** — covariance, correlation and univariate beta
   of each factor's daily changes vs the Bund spread, full sample and
   trailing 252 days.
3. **Fair value: rolling PCR** — each day the standardised factor block over
   the trailing 252-day window is decomposed with PCA; the spread level is
   regressed on the top-4 principal components and today's factor
   observation is projected through the fit.
4. **Residual → z-score** — residual (market − fair) is smoothed with an
   AR(1) Kalman filter and z-scored on a rolling 252-day window.
   `z > 0` = spread wide/cheap vs model, `z < 0` = tight/rich.
   Bands: watch at ±1.5, trade at ±2.0.

## Notes

- Untested outside BQuant (BQL is Terminal-only). If the data request
  errors, check ticker yellow-keys in CONFIG first (e.g. `RXAISPE Comdty`
  vs `RXAISPE Index`).
- The numerical core (PCR, Kalman, z-score, factor construction and the
  fallback path) has been unit-tested on synthetic data.

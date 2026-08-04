"""Spot FRA-ESTR basis analysis: is a big widening followed by a plummet?

The object under study is the SPOT basis

    B(t) = Euribor3M fix(t) - 3M ESTR OIS(t)      (in bp)

Both legs cover (nearly) the same 3-month window, so ECB-path expectations
cancel to first order and B should be the pure credit/liquidity premium.
The practical problem - the reason "3m ESTR moves with rallies and sell-offs
while the Euribor fix doesn't" - is that the fix is STICKY: it is set once a
day at 11:00 CET from panel submissions that pass changes in the expected
policy path through with a lag of several days, while the OIS reprices
instantly. A fast rally therefore widens measured B mechanically, and B then
falls back as the fix catches up - which can masquerade as (or genuinely be
part of) the proposition "wide basis plummets within 10 days".

The pipeline separates the two effects:

1. `data`       - flexible loading + alignment of the two daily series,
                  with a same-day / previous-day OIS alignment switch so
                  timing artefacts can be ruled out.
2. `stickiness` - a distributed-lag pass-through model of the fix on the
                  OIS leg. Its tail weights give a walk-forward "pending
                  catch-up": the part of future basis change that is
                  already predictable from OIS moves the fix has not yet
                  digested. basis + pending = the CATCH-UP-ADJUSTED basis,
                  the fair way to compare the two series day to day.
3. `backtest`   - rolling z-scores define "widened a lot"; an event study
                  measures the forward basis change at 1-20 day horizons
                  against a random-day permutation null, decomposes each
                  10-day "plummet" into mechanical catch-up vs genuine
                  premium reversion, and runs robustness (threshold grid,
                  widening-speed signal, tightening asymmetry) plus a
                  naive sell-the-basis P&L.
4. `report`     - markdown report + charts + events CSV.

Run `python -m basis_analysis` for the bundled SYNTHETIC demo, or point
`--fix/--ois` at real exports (Bloomberg: `EUR003M Index`,
`EESWEC Curncy`).
"""

from __future__ import annotations

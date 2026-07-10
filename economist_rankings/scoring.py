"""Score individual forecasts against the actual released numbers.

Different indicators live on different scales (CPI in percent, payrolls in
thousands), so raw errors are not comparable across indicators. Two
scale-free measures make cross-indicator ranking fair:

* ``percentile`` - for each release (indicator + period), economists are
  ranked by absolute error; 100 means the closest call among peers that
  release, 0 the furthest. Averaging percentiles across releases rewards
  consistently beating the pack regardless of indicator units.
* ``norm_abs_error`` - absolute error divided by the cross-economist mean
  absolute error for that release, so 1.0 is "as wrong as the average
  forecaster" and lower is better.
"""

from __future__ import annotations

import pandas as pd


def merge_actuals(forecasts: pd.DataFrame, actuals: pd.DataFrame | None) -> pd.DataFrame:
    """Attach actual values to forecasts.

    Values from the actuals table win; an ``actual`` column already present
    in the forecast file fills the gaps. Rows with no actual are dropped
    (the release hasn't happened or isn't covered).
    """
    df = forecasts.copy()
    inline = df.pop("actual") if "actual" in df.columns else None

    if actuals is not None and not actuals.empty:
        df = df.merge(actuals, on=["indicator", "period"], how="left")
    else:
        df["actual"] = pd.NA

    if inline is not None:
        df["actual"] = df["actual"].fillna(inline)

    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    return df.dropna(subset=["actual"]).reset_index(drop=True)


def score_forecasts(scored: pd.DataFrame) -> pd.DataFrame:
    """Add per-forecast error columns to a frame of forecast+actual rows."""
    df = scored.copy()
    df["error"] = df["forecast"] - df["actual"]
    df["abs_error"] = df["error"].abs()

    by_release = df.groupby(["indicator", "period"])["abs_error"]

    # Percentile rank within each release: 100 = best (smallest error).
    # With n forecasters, rank 1 -> 100 and rank n -> 0.
    rank = by_release.rank(method="average", ascending=True)
    n = by_release.transform("count")
    df["percentile"] = ((n - rank) / (n - 1).clip(lower=1) * 100).where(n > 1, 50.0)

    # Normalised absolute error: 1.0 == average miss for that release.
    mean_abs = by_release.transform("mean")
    df["norm_abs_error"] = (df["abs_error"] / mean_abs).where(mean_abs > 0, 0.0)

    return df

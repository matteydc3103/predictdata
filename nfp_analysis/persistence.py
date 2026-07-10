"""Split-half persistence test: does past skill predict future skill?

Firms' metrics are computed separately before and after a split date,
then the cross-firm rank correlation between halves is tested. If skill
were real and stable, firms good in the first half would tend to be good
in the second; the workbook (and this recomputation) finds correlations
near zero.
"""

from __future__ import annotations

import pandas as pd
from scipy import stats

from .rankings import firm_metrics

DEFAULT_SPLIT = "2022-09-02"
METRICS = ["zmae", "beat_median_pct", "ic_pearson"]


def split_half(
    panel: pd.DataFrame,
    split_date: str = DEFAULT_SPLIT,
    min_n: int = 5,
    qualified_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (per-firm H1/H2 metrics, correlation summary).

    ``qualified_only`` restricts to firms qualified on the full sample
    (the workbook's universe); ``min_n`` additionally requires that many
    submissions in each half so a half-metric is meaningful.
    """
    split = pd.Timestamp(split_date)
    h1 = firm_metrics(panel[panel["release_date"] < split], qualified_n=0)
    h2 = firm_metrics(panel[panel["release_date"] >= split], qualified_n=0)

    cols = ["firm", "n"] + METRICS
    merged = h1[cols].merge(h2[cols], on="firm", suffixes=("_h1", "_h2"))
    merged = merged[(merged["n_h1"] >= min_n) & (merged["n_h2"] >= min_n)]

    if qualified_only:
        full = firm_metrics(panel)
        qualified = set(full.loc[full["qualified"], "firm"])
        merged = merged[merged["firm"].isin(qualified)]

    summary_rows = []
    for m in METRICS:
        pair = merged[[f"{m}_h1", f"{m}_h2"]].dropna()
        r, p = stats.spearmanr(pair[f"{m}_h1"], pair[f"{m}_h2"])
        summary_rows.append(
            {"metric": m, "n_firms": len(pair), "spearman_r": r, "p_value": p}
        )
    return merged.reset_index(drop=True), pd.DataFrame(summary_rows)

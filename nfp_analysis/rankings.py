"""Per-firm skill metrics, ranks, and formal significance tests.

The workbook reports the metrics; this module adds the tests the metrics
imply, plus a Benjamini-Hochberg false-discovery correction across firms.
With ~75 qualified firms, a handful of nominal p<0.05 results are expected
by chance alone - the q-values say whether anything survives.

Tests per firm:
* Beat Median % - exact binomial test of strict beats vs strict losses
  (ties dropped) against p=0.5.
* IC - Pearson (workbook's live metric) and Spearman (preferred, rank
  based) correlation of zDev vs zSurprise, with their p-values.
* Directional hit rate on bold calls - exact binomial test against 0.5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

QUALIFIED_N = 45


def _bh_fdr(pvals: pd.Series) -> pd.Series:
    """Benjamini-Hochberg q-values (NaNs passed through)."""
    p = pvals.dropna()
    if p.empty:
        return pvals
    n = len(p)
    order = p.sort_values()
    q = order * n / np.arange(1, n + 1)
    q = pd.Series(np.minimum.accumulate(q[::-1])[::-1], index=order.index).clip(upper=1.0)
    return pvals.to_frame("p").join(q.rename("q"))["q"]


def firm_metrics(panel: pd.DataFrame, qualified_n: int = QUALIFIED_N) -> pd.DataFrame:
    """One row per firm with metrics, ranks, composite, and p/q-values."""
    rows = []
    for firm, sub in panel.groupby("firm"):
        n = len(sub)
        bold = sub[sub["bold"]]
        wins = int((sub["beat_median"] == 1.0).sum())
        losses = int((sub["beat_median"] == 0.0).sum())
        row = {
            "firm": firm,
            "economist": sub.sort_values("release_date")["economist"].iloc[-1],
            "n": n,
            "mae": sub["abs_error"].mean(),
            "zmae": sub["z_err"].mean(),
            "beat_median_pct": sub["beat_median"].mean(),
            "rel_mae": sub["abs_error"].mean() / sub["consensus_abs_error"].mean(),
            "bold_pct": sub["bold"].mean(),
            "dir_hit_pct": bold["dir_correct"].mean() if len(bold) else np.nan,
            "p_beat": stats.binomtest(wins, wins + losses, 0.5).pvalue if wins + losses else np.nan,
            "p_dir": (
                stats.binomtest(int(bold["dir_correct"].sum()), len(bold), 0.5).pvalue
                if len(bold)
                else np.nan
            ),
        }
        if n >= 3 and sub["z_dev"].std() > 0:
            row["ic_pearson"], row["p_ic_pearson"] = stats.pearsonr(sub["z_dev"], sub["z_surprise"])
            row["ic_spearman"], row["p_ic_spearman"] = stats.spearmanr(sub["z_dev"], sub["z_surprise"])
        else:
            row["ic_pearson"] = row["p_ic_pearson"] = np.nan
            row["ic_spearman"] = row["p_ic_spearman"] = np.nan
        rows.append(row)

    df = pd.DataFrame(rows)
    df["qualified"] = df["n"] >= qualified_n

    # Ranks and composite computed among qualified firms, as in the workbook.
    q = df["qualified"]
    df.loc[q, "rank_zmae"] = df.loc[q, "zmae"].rank(method="min")
    df.loc[q, "rank_beat"] = df.loc[q, "beat_median_pct"].rank(method="min", ascending=False)
    df.loc[q, "rank_ic"] = df.loc[q, "ic_pearson"].rank(method="min", ascending=False)
    df["composite"] = df[["rank_zmae", "rank_beat", "rank_ic"]].mean(axis=1)
    df.loc[q, "overall_rank"] = df.loc[q, "composite"].rank(method="min")

    # FDR correction across qualified firms only (the tested universe).
    for col in ("p_beat", "p_ic_pearson", "p_ic_spearman", "p_dir"):
        df["q" + col[1:]] = np.nan
        df.loc[q, "q" + col[1:]] = _bh_fdr(df.loc[q, col])

    return df.sort_values(["overall_rank", "n"], na_position="last").reset_index(drop=True)


def panel_level_tests(panel: pd.DataFrame, bias_start: str | None = None) -> dict:
    """Panel-wide tests that don't depend on picking individual firms.

    * Pooled IC: Spearman correlation of every submission's zDev against
      the release zSurprise. Near zero means deviating from consensus
      carries no information on average.
    * Consensus bias: is the surprise (actual - consensus) systematically
      one-sided? Tested on release-level z-surprises with a t-test and a
      sign test, optionally restricted to releases from ``bias_start``.
    """
    pooled_r, pooled_p = stats.spearmanr(panel["z_dev"], panel["z_surprise"])

    releases = panel.drop_duplicates("period")[["release_date", "surprise", "z_surprise"]]
    if bias_start:
        releases = releases[releases["release_date"] >= pd.Timestamp(bias_start)]
    z = releases["z_surprise"]
    t, p_t = stats.ttest_1samp(z, 0.0)
    ups = int((z > 0).sum())
    downs = int((z < 0).sum())
    p_sign = stats.binomtest(ups, ups + downs, 0.5).pvalue if ups + downs else np.nan

    return {
        "pooled_spearman_ic": pooled_r,
        "pooled_ic_p": pooled_p,
        "n_releases": len(releases),
        "mean_z_surprise": z.mean(),
        "median_surprise_k": releases["surprise"].median(),
        "upside_share": ups / (ups + downs) if ups + downs else np.nan,
        "bias_t_stat": t,
        "bias_p_t": p_t,
        "bias_p_sign": p_sign,
    }

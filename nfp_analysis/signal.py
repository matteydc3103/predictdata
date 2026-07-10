"""Top-5 IC agreement trade signal, with an out-of-sample backtest.

Rule (from the workbook's Signal sheet): before each NFP print, take the
5 forecasters with the highest *trailing* IC among qualified firms that
submitted an estimate. If at least 4 of the 5 deviate from the consensus
median in the same direction (net sign of deviations >= 3), position for
an NFP surprise in that direction; otherwise stand down.

Everything here is walk-forward: the IC used to pick the top-5 for a
given release is computed only from releases *before* it, so the backtest
contains no lookahead. The live sheet uses full-sample IC for convenience;
`live_signal` here uses trailing IC, matching the backtest discipline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TOP_N = 5
NET_THRESHOLD = 3  # |sum of deviation signs| required to fire
MIN_TRAILING_N = 45  # submissions needed before a firm's trailing IC counts


def trailing_ic(panel: pd.DataFrame, before: pd.Timestamp, min_n: int = MIN_TRAILING_N) -> pd.Series:
    """Pearson IC per firm using only releases strictly before ``before``."""
    hist = panel[panel["release_date"] < before]
    out = {}
    for firm, sub in hist.groupby("firm"):
        if len(sub) >= min_n and sub["z_dev"].std() > 0:
            out[firm] = np.corrcoef(sub["z_dev"], sub["z_surprise"])[0, 1]
    return pd.Series(out, name="trailing_ic")


def signal_for_release(
    panel: pd.DataFrame,
    key: str,
    top_n: int = TOP_N,
    net_threshold: int = NET_THRESHOLD,
    min_trailing_n: int = MIN_TRAILING_N,
) -> dict:
    """Evaluate the rule for one release. Returns direction 0 when standing down."""
    rel = panel[panel["key"] == key]
    if rel.empty:
        raise ValueError(f"No submissions found for release key {key!r}")
    date = rel["release_date"].iloc[0]

    ic = trailing_ic(panel, date, min_trailing_n)
    submitted = rel[rel["firm"].isin(ic.index)].copy()
    submitted["trailing_ic"] = submitted["firm"].map(ic)
    top = submitted.nlargest(top_n, "trailing_ic")

    net = int(np.sign(top["z_dev"]).sum()) if len(top) == top_n else 0
    direction = int(np.sign(net)) if abs(net) >= net_threshold else 0

    return {
        "key": key,
        "release_date": date,
        "n_selectable": len(submitted),
        "top5": top[["firm", "economist", "estimate", "z_dev", "trailing_ic"]],
        "consensus": rel["consensus"].iloc[0],
        "net_sign": net,
        "direction": direction,  # +1 long-surprise, -1 short-surprise, 0 no trade
        "actual": rel["actual"].iloc[0],
        "surprise": rel["surprise"].iloc[0],
        "z_surprise": rel["z_surprise"].iloc[0],
    }


def backtest(
    panel: pd.DataFrame,
    start: str = "2022-01-01",
    top_n: int = TOP_N,
    net_threshold: int = NET_THRESHOLD,
    min_trailing_n: int = MIN_TRAILING_N,
) -> pd.DataFrame:
    """Walk the rule forward over every included release from ``start``."""
    keys = (
        panel[panel["release_date"] >= pd.Timestamp(start)]
        .sort_values("release_date")["key"]
        .unique()
    )
    rows = []
    for key in keys:
        s = signal_for_release(panel, key, top_n, net_threshold, min_trailing_n)
        if s["n_selectable"] < top_n:
            continue  # not evaluable: too few qualified submitters yet
        rows.append(
            {
                "key": key,
                "release_date": s["release_date"],
                "net_sign": s["net_sign"],
                "direction": s["direction"],
                "surprise": s["surprise"],
                "z_surprise": s["z_surprise"],
                "hit": (
                    np.nan
                    if s["direction"] == 0
                    else float(np.sign(s["surprise"]) == s["direction"])
                ),
                "signed_z_surprise": (
                    np.nan if s["direction"] == 0 else s["direction"] * s["z_surprise"]
                ),
            }
        )
    return pd.DataFrame(rows)


def backtest_stats(bt: pd.DataFrame, n_permutations: int = 20000, seed: int = 0) -> dict:
    """Significance tests on a backtest run.

    ``p_binomial`` tests against a coin flip - the workbook's null. That
    null is too easy in a window dominated by upside surprises, so
    ``p_base_rate`` also runs a permutation test: random release picks
    with the same long/short mix, scored against the realised surprise
    signs. Beating THAT is evidence of selection skill.
    """
    fires = bt.dropna(subset=["hit"])
    n, hits = len(fires), int(fires["hit"].sum())
    out = {
        "n_evaluable": len(bt),
        "n_fires": n,
        "fire_rate": n / len(bt) if len(bt) else np.nan,
        "hits": hits,
        "hit_rate": hits / n if n else np.nan,
        "p_binomial": stats.binomtest(hits, n, 0.5).pvalue if n else np.nan,
    }
    if n:
        rng = np.random.default_rng(seed)
        signs = np.sign(bt["surprise"].to_numpy())
        dirs = fires["direction"].to_numpy()
        sims = np.empty(n_permutations)
        for i in range(n_permutations):
            picks = rng.choice(len(signs), size=n, replace=False)
            sims[i] = (signs[picks] == rng.permutation(dirs)).sum()
        out["p_base_rate"] = float((sims >= hits).mean())
    if n >= 2:
        t, p = stats.ttest_1samp(fires["signed_z_surprise"], 0.0)
        out["avg_signed_z_surprise"] = fires["signed_z_surprise"].mean()
        out["t_stat"] = t
        out["p_t"] = p

    for label, side in (("long", 1), ("short", -1)):
        f = fires[fires["direction"] == side]
        base = (np.sign(bt["surprise"]) == side).mean() if len(bt) else np.nan
        out[f"{label}_fires"] = len(f)
        out[f"{label}_hit_rate"] = f["hit"].mean() if len(f) else np.nan
        out[f"{label}_base_rate"] = base

    if n >= 4:
        half = n // 2
        out["hit_rate_first_half"] = fires["hit"].iloc[:half].mean()
        out["hit_rate_second_half"] = fires["hit"].iloc[half:].mean()
        out["last10_hits"] = int(fires["hit"].tail(10).sum())
        out["last10_n"] = min(10, n)
    return out


def parameter_sensitivity(
    panel: pd.DataFrame,
    start: str = "2022-01-01",
    top_ns: tuple[int, ...] = (4, 5, 6),
    thresholds: tuple[int, ...] = (2, 3, 4),
) -> pd.DataFrame:
    """Re-run the backtest across neighbouring parameterisations.

    A rule that only works at exactly top-5/net>=3 is curve-fit; nearby
    settings should degrade gracefully if the effect is real.
    """
    rows = []
    for top_n in top_ns:
        for thr in thresholds:
            if thr > top_n:
                continue
            st = backtest_stats(backtest(panel, start, top_n, thr))
            rows.append(
                {
                    "top_n": top_n,
                    "net_threshold": thr,
                    "n_fires": st["n_fires"],
                    "hit_rate": st["hit_rate"],
                    "p_binomial": st["p_binomial"],
                    "avg_signed_z_surprise": st.get("avg_signed_z_surprise"),
                }
            )
    return pd.DataFrame(rows)

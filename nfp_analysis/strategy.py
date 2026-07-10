"""Trade strategies informed by the statistical tests, with honest nulls.

Ranked by the strength of evidence behind them:

1. DISPERSION REGIME (primary) - forecaster disagreement is a downside
   barometer the consensus median fails to price. Dispersion before
   downside surprises averages 2.0x its trailing norm vs 1.3x before
   upside ones (Mann-Whitney p=0.01); rank correlation of relative
   dispersion with the surprise is -0.33 (p=0.003, survives Bonferroni
   across the 8 features screened). Rule: long the surprise when the
   panel is tight (disp_rel <= 1.0), short when it is stretched
   (disp_rel >= 1.3), stand aside between. 2022-26 walk-forward:
   77% hit over 53 events, t=4.35, permutation-vs-base-rate p=0.003,
   stable across halves and threshold choices, and it kept working in
   2024-26 when the raw long bias faded.
2. CONSENSUS BIAS (sizing modifier) - the survey median has
   systematically low-balled payrolls (mean z-surprise +0.95 full
   sample, t=2.8), but it is fading (2024-26 p=0.07). Used to scale the
   dispersion trade 1.5x/0.5x when the trailing-24 bias t-stat
   agrees/disagrees with the direction.
3. TOP-5 IC AGREEMENT (informational only) - the workbook's claimed
   out-of-sample edge (74% hit, p=0.017) does NOT replicate from raw
   data under ~20 definitional variants, and no forecaster-selection
   feature (IC weighting, bold imbalance, panel skew) predicts the
   surprise. Reported for context; it never drives a position.

Caveat disclosed: the dispersion feature was identified on the full
sample, so the 2022-26 evaluation overlaps the discovery data. Its
defence is threshold-insensitivity, subperiod stability (including the
newest regime), a real mechanism, and a Bonferroni-surviving screen -
not a clean holdout.

Everything below is walk-forward: each release's inputs (trailing
median dispersion, trailing bias t-stat) see only prior releases, and
the release's own dispersion is known before the print (the survey
closes days ahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .signal import backtest as signal_backtest
from .signal import signal_for_release

GATE_WINDOW = 24  # trailing included releases used to test the bias
GATE_T = 1.5  # |t| needed on trailing mean z-surprise to open a tilt

DISP_WINDOW = 24  # trailing releases whose median dispersion sets "normal"
DISP_LONG = 1.0  # disp_rel at or below this -> long the surprise
DISP_SHORT = 1.3  # disp_rel at or above this -> short the surprise


def release_table(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per release with disp_rel and trailing-bias t-stat attached.

    disp_rel = this release's dispersion over the median dispersion of the
    previous DISP_WINDOW releases (min 12) - a self-normalising regime
    gauge that needs no absolute calibration.
    """
    rels = (
        panel.drop_duplicates("period")[
            ["key", "period", "release_date", "dispersion", "surprise", "z_surprise"]
        ]
        .sort_values("release_date")
        .reset_index(drop=True)
    )
    rels["disp_rel"] = rels["dispersion"] / rels["dispersion"].shift(1).rolling(
        DISP_WINDOW, min_periods=12
    ).median()

    t_stats = np.full(len(rels), np.nan)
    for i in range(len(rels)):
        hist = rels["z_surprise"].iloc[max(0, i - GATE_WINDOW) : i]
        if len(hist) >= GATE_WINDOW:
            t_stats[i], _ = stats.ttest_1samp(hist, 0.0)
    rels["trailing_bias_t"] = t_stats
    return rels


def dispersion_backtest(
    panel: pd.DataFrame,
    start: str = "2022-01-01",
    long_at: float = DISP_LONG,
    short_at: float = DISP_SHORT,
) -> pd.DataFrame:
    """Walk-forward record of the dispersion-regime strategy.

    size = 1.0, scaled to 1.5 when the trailing bias t-stat agrees with the
    direction (|t| >= GATE_T) and 0.5 when it disagrees.
    """
    rels = release_table(panel)
    rels = rels[rels["release_date"] >= pd.Timestamp(start)]
    rows = []
    for _, r in rels.iterrows():
        if np.isnan(r["disp_rel"]):
            continue
        if r["disp_rel"] <= long_at:
            direction = 1
        elif r["disp_rel"] >= short_at:
            direction = -1
        else:
            direction = 0
        size = 1.0
        if direction != 0 and not np.isnan(r["trailing_bias_t"]):
            if r["trailing_bias_t"] * direction >= GATE_T:
                size = 1.5
            elif r["trailing_bias_t"] * direction <= -GATE_T:
                size = 0.5
        rows.append(
            {
                "key": r["key"],
                "release_date": r["release_date"],
                "disp_rel": r["disp_rel"],
                "trailing_bias_t": r["trailing_bias_t"],
                "direction": direction,
                "size": 0.0 if direction == 0 else size,
                "surprise": r["surprise"],
                "z_surprise": r["z_surprise"],
                "hit": (
                    np.nan
                    if direction == 0
                    else float(np.sign(r["surprise"]) == direction)
                ),
                "pnl_z": (
                    np.nan if direction == 0 else size * direction * r["z_surprise"]
                ),
            }
        )
    return pd.DataFrame(rows)


def bias_backtest(
    panel: pd.DataFrame,
    start: str = "2022-01-01",
    window: int = GATE_WINDOW,
    gate_t: float = GATE_T,
) -> pd.DataFrame:
    """Walk-forward: tilt toward the surprise direction the trailing
    window supports; stand down when the trailing t-stat is inside the gate."""
    releases = (
        panel.drop_duplicates("period")[["key", "release_date", "surprise", "z_surprise"]]
        .sort_values("release_date")
        .reset_index(drop=True)
    )
    rows = []
    for i, rel in releases.iterrows():
        hist = releases.iloc[:i].tail(window)
        if rel["release_date"] < pd.Timestamp(start) or len(hist) < window:
            continue
        t, _ = stats.ttest_1samp(hist["z_surprise"], 0.0)
        direction = int(np.sign(t)) if abs(t) >= gate_t else 0
        rows.append(
            {
                "key": rel["key"],
                "release_date": rel["release_date"],
                "trailing_t": t,
                "direction": direction,
                "surprise": rel["surprise"],
                "z_surprise": rel["z_surprise"],
                "hit": (
                    np.nan
                    if direction == 0
                    else float(np.sign(rel["surprise"]) == direction)
                ),
                "signed_z_surprise": (
                    np.nan if direction == 0 else direction * rel["z_surprise"]
                ),
            }
        )
    return pd.DataFrame(rows)


def combined_backtest(panel: pd.DataFrame, start: str = "2022-01-01") -> pd.DataFrame:
    """Bias tilt as the trigger, top-5 agreement as the sizing skew.

    size = 1.0 when the tilt fires alone, 1.5 when the top-5 signal agrees,
    0.5 when it disagrees. P&L proxy is size x direction x z-surprise.
    """
    bias = bias_backtest(panel, start=start).set_index("key")
    sig = signal_backtest(panel, start=start).set_index("key")

    rows = []
    for key, b in bias.iterrows():
        s_dir = int(sig.loc[key, "direction"]) if key in sig.index else 0
        direction = int(b["direction"])
        if direction == 0:
            size = 0.0
        elif s_dir == direction:
            size = 1.5
        elif s_dir == -direction:
            size = 0.5
        else:
            size = 1.0
        rows.append(
            {
                "key": key,
                "release_date": b["release_date"],
                "bias_direction": direction,
                "top5_direction": s_dir,
                "size": size,
                "surprise": b["surprise"],
                "z_surprise": b["z_surprise"],
                "pnl_z": size * direction * b["z_surprise"],
            }
        )
    return pd.DataFrame(rows)


def strategy_stats(
    bt: pd.DataFrame,
    direction_col: str = "direction",
    n_permutations: int = 20000,
    seed: int = 0,
) -> dict:
    fires = bt[bt[direction_col] != 0]
    n = len(fires)
    if n == 0:
        return {"n_evaluable": len(bt), "n_fires": 0}
    hits = int((np.sign(fires["surprise"]) == fires[direction_col]).sum())
    signed = fires[direction_col] * fires["z_surprise"]
    t, p_t = stats.ttest_1samp(signed, 0.0) if n >= 2 else (np.nan, np.nan)

    # Permutation null: random release picks with the same long/short mix.
    rng = np.random.default_rng(seed)
    signs = np.sign(bt["surprise"].to_numpy())
    dirs = fires[direction_col].to_numpy()
    sims = np.empty(n_permutations)
    for i in range(n_permutations):
        picks = rng.choice(len(signs), size=n, replace=False)
        sims[i] = (signs[picks] == rng.permutation(dirs)).sum()

    return {
        "n_evaluable": len(bt),
        "n_fires": n,
        "hit_rate": hits / n,
        "p_binomial": stats.binomtest(hits, n, 0.5).pvalue,
        "p_base_rate": float((sims >= hits).mean()),
        "avg_signed_z_surprise": signed.mean(),
        "avg_surprise_captured_k": (fires[direction_col] * fires["surprise"]).mean(),
        "t_stat": t,
        "p_t": p_t,
    }


def live_recommendation(panel: pd.DataFrame, key: str | None = None) -> dict:
    """The playbook for a print, from walk-forward-legal inputs only.

    Hierarchy: dispersion regime decides the direction, trailing consensus
    bias scales the size, the top-5 panel is reported for context only.
    For an upcoming print, paste its survey submissions into the panel
    first - the signal only needs the estimates, not the actual.
    """
    rels = release_table(panel)
    if key is None:
        key = rels["key"].iloc[-1]
    r = rels[rels["key"] == key].iloc[0]

    if np.isnan(r["disp_rel"]):
        direction = 0
    elif r["disp_rel"] <= DISP_LONG:
        direction = 1
    elif r["disp_rel"] >= DISP_SHORT:
        direction = -1
    else:
        direction = 0

    size = 0.0
    if direction != 0:
        size = 1.0
        if not np.isnan(r["trailing_bias_t"]):
            if r["trailing_bias_t"] * direction >= GATE_T:
                size = 1.5
            elif r["trailing_bias_t"] * direction <= -GATE_T:
                size = 0.5

    sig = signal_for_release(panel, key)

    return {
        "release_key": key,
        "disp_rel": r["disp_rel"],
        "trailing_bias_t": r["trailing_bias_t"],
        "direction": direction,
        "position_size": size,
        "top5_net_sign": sig["net_sign"],
        "top5_direction": sig["direction"],
        "top5_names": sig["top5"],
        "expression": (
            f"Stand aside - panel dispersion is {r['disp_rel']:.2f}x its trailing norm, "
            f"inside the no-trade band ({DISP_LONG:.1f}-{DISP_SHORT:.1f})."
            if direction == 0
            else (
                f"Panel dispersion is {r['disp_rel']:.2f}x its trailing norm -> position for "
                f"{'an upside' if direction > 0 else 'a downside'} NFP surprise at {size:.1f}x base unit: "
                + (
                    "short front-end rates (2y note / SOFR futures) and/or long USD into the print."
                    if direction > 0
                    else "long front-end rates (2y note / SOFR futures) and/or short USD into the print."
                )
            )
        ),
    }

"""Trade strategy informed by the statistical tests, with honest nulls.

What the tests actually support (see rankings/persistence/signal):

1. CONSENSUS BIAS - the only panel feature significant after proper
   testing: the survey median has systematically low-balled payrolls
   (mean z-surprise +0.95 full sample, t=2.8). Tradeable as a standing
   tilt toward upside surprise, gated on the bias still being present in
   a trailing window (it attenuates in 2024-26).
2. TOP-5 IC AGREEMENT - the workbook's claimed out-of-sample edge
   (74% hit, p=0.017) does NOT replicate from the raw data under ~20
   definitional variants, and against the base-rate null the best variant
   is p~0.3. Retained here as a *skew modifier only*: it may add or
   remove conviction, it never initiates a trade by itself.

The bias tilt is validated walk-forward below: at each release, the gate
sees only prior releases.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .signal import backtest as signal_backtest
from .signal import signal_for_release

GATE_WINDOW = 24  # trailing included releases used to test the bias
GATE_T = 1.5  # |t| needed on trailing mean z-surprise to open a tilt


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


def strategy_stats(bt: pd.DataFrame, direction_col: str = "direction") -> dict:
    fires = bt[bt[direction_col] != 0]
    n = len(fires)
    if n == 0:
        return {"n_evaluable": len(bt), "n_fires": 0}
    hits = int((np.sign(fires["surprise"]) == fires[direction_col]).sum())
    signed = fires[direction_col] * fires["z_surprise"]
    t, p_t = stats.ttest_1samp(signed, 0.0) if n >= 2 else (np.nan, np.nan)
    return {
        "n_evaluable": len(bt),
        "n_fires": n,
        "hit_rate": hits / n,
        "p_binomial": stats.binomtest(hits, n, 0.5).pvalue,
        "avg_signed_z_surprise": signed.mean(),
        "t_stat": t,
        "p_t": p_t,
    }


def live_recommendation(panel: pd.DataFrame, key: str | None = None) -> dict:
    """The playbook for the next print, from walk-forward-legal inputs only."""
    releases = panel.drop_duplicates("period").sort_values("release_date")
    if key is None:
        key = releases["key"].iloc[-1]

    hist = releases[releases["key"] != key].tail(GATE_WINDOW)
    t, p = stats.ttest_1samp(hist["z_surprise"], 0.0)
    bias_dir = int(np.sign(t)) if abs(t) >= GATE_T else 0

    sig = signal_for_release(panel, key)
    if bias_dir == 0:
        size = 0.0
    elif sig["direction"] == bias_dir:
        size = 1.5
    elif sig["direction"] == -bias_dir:
        size = 0.5
    else:
        size = 1.0

    return {
        "release_key": key,
        "trailing_bias_t": t,
        "trailing_bias_p": p,
        "bias_direction": bias_dir,
        "top5_net_sign": sig["net_sign"],
        "top5_direction": sig["direction"],
        "top5_names": sig["top5"],
        "position_size": size,
        "direction": bias_dir,
        "expression": (
            "Stand down - no statistically supported view."
            if bias_dir == 0
            else (
                f"Position for an {'upside' if bias_dir > 0 else 'downside'} NFP surprise at "
                f"{size:.1f}x base unit: "
                + (
                    "short front-end rates (e.g. 2y note / SOFR futures) and/or long USD into the print."
                    if bias_dir > 0
                    else "long front-end rates (e.g. 2y note / SOFR futures) and/or short USD into the print."
                )
            )
        ),
    }

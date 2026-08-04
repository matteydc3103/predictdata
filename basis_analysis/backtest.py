"""Event-study backtest of "wide spot basis -> plummet within 10 days".

Signal definitions (both self-normalising, so they work across regimes):

* level    - z-score of the basis level vs its rolling window: the direct
             reading of "euribor3m minus estr3m becomes high".
* widening - z-score of the `widen_days`-day CHANGE in the basis: "widened
             a lot recently", robust to slow regime drift in the level.

Events are first CROSSINGS above the threshold with a cooldown so episodes
are not double-counted. Everything an event uses is known at the event date
(rolling stats include the day itself, nothing later; pending catch-up is
fitted walk-forward).

For each event the study records the forward basis change at 1-20 day
horizons, its split into fix leg vs OIS leg, the pre-event OIS move (was the
widening rally-driven?), and the pending catch-up at entry (how much of the
future move was mechanically owed). Significance comes from a random-day
permutation null - the mean forward change over as many randomly drawn
eligible days, 10k draws - not from an i.i.d. assumption.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .stickiness import walkforward_pending

Z_WINDOW = 125  # ~6 months of business days
MIN_PERIODS = 100
WIDEN_DAYS = 5
HORIZONS = tuple(range(1, 21))
H_STAR = 10  # the proposition's horizon
MECH_THRESHOLD_BP = -2.0  # pending below this => "stale-fix" widening


def add_signals(
    df: pd.DataFrame,
    z_window: int = Z_WINDOW,
    min_periods: int = MIN_PERIODS,
    widen_days: int = WIDEN_DAYS,
    n_lags: int = 10,
) -> pd.DataFrame:
    """Attach rolling z-scores and the walk-forward pending catch-up."""
    df = df.copy()
    roll = df["basis_bp"].rolling(z_window, min_periods=min_periods)
    df["basis_mean"] = roll.mean()
    df["basis_std"] = roll.std()
    df["z_level"] = (df["basis_bp"] - df["basis_mean"]) / df["basis_std"]

    widen = df["basis_bp"].diff(widen_days)
    wroll = widen.rolling(z_window, min_periods=min_periods)
    df["widen_bp"] = widen
    df["z_widen"] = (widen - wroll.mean()) / wroll.std()

    df["pending_bp"] = walkforward_pending(df, n_lags=n_lags)
    df["basis_adj_bp"] = df["basis_bp"] + df["pending_bp"]
    return df


def extract_events(
    df: pd.DataFrame,
    signal: str = "level",
    z_entry: float = 1.5,
    cooldown: int = H_STAR,
    direction: int = 1,
) -> list[int]:
    """Positions where the chosen z-score first crosses the threshold.

    direction=+1 catches widenings (z >= z_entry), -1 catches tightenings
    (z <= -z_entry, the asymmetry check). A new event needs the z to have
    been inside the threshold the day before (a crossing, not persistence)
    and at least `cooldown` business days since the previous event.
    """
    col = {"level": "z_level", "widening": "z_widen"}[signal]
    z = (df[col].to_numpy() * direction) >= z_entry
    valid = df[col].notna().to_numpy()
    events: list[int] = []
    last = -(10**9)
    for i in range(1, len(df)):
        if z[i] and not z[i - 1] and valid[i] and valid[i - 1] and i - last >= cooldown:
            events.append(i)
            last = i
    return events


def event_study(
    df: pd.DataFrame,
    events: list[int],
    horizons: tuple[int, ...] = HORIZONS,
    h_star: int = H_STAR,
    pre_days: int = 5,
) -> pd.DataFrame:
    """One row per event with forward outcomes and entry-time context."""
    basis = df["basis_bp"].to_numpy()
    fix = df["fix"].to_numpy() * 100.0
    ois = df["ois"].to_numpy() * 100.0
    n = len(df)
    rows = []
    for i in events:
        row: dict = {
            "date": df.index[i],
            "basis_bp": basis[i],
            "z_level": df["z_level"].iloc[i],
            "z_widen": df["z_widen"].iloc[i],
            "excess_bp": basis[i] - df["basis_mean"].iloc[i],
            "pending_bp": df["pending_bp"].iloc[i],
            "d_ois_pre_bp": ois[i] - ois[i - pre_days] if i >= pre_days else np.nan,
            "d_basis_pre_bp": basis[i] - basis[i - pre_days] if i >= pre_days else np.nan,
        }
        for h in horizons:
            row[f"d_basis_{h}d"] = basis[i + h] - basis[i] if i + h < n else np.nan
        if i + h_star < n:
            row["d_fix_bp"] = fix[i + h_star] - fix[i]
            row["d_ois_bp"] = ois[i + h_star] - ois[i]
        else:
            row["d_fix_bp"] = row["d_ois_bp"] = np.nan
        rows.append(row)
    ev = pd.DataFrame(rows)
    if not ev.empty:
        target = ev[f"d_basis_{h_star}d"]
        ev["retrace_frac"] = np.where(
            ev["excess_bp"] > 1e-9, -target / ev["excess_bp"], np.nan
        )
        # object dtype so events with no pending estimate stay None (excluded
        # from the mechanical/genuine split) instead of defaulting to False
        ev["mechanical"] = pd.Series(
            [
                None if not np.isfinite(p) else bool(p <= MECH_THRESHOLD_BP)
                for p in ev["pending_bp"]
            ],
            index=ev.index,
            dtype=object,
        )
    return ev


def _forward_changes(df: pd.DataFrame, h: int) -> pd.Series:
    return df["basis_bp"].shift(-h) - df["basis_bp"]


def _eligible_mask(df: pd.DataFrame, h: int) -> np.ndarray:
    """Days that could have been events: z defined and full forward window."""
    fwd = _forward_changes(df, h)
    return (df["z_level"].notna() & fwd.notna()).to_numpy()


def permutation_pvalue(
    df: pd.DataFrame,
    observed_mean: float,
    n_events: int,
    h: int = H_STAR,
    n_draws: int = 10_000,
    seed: int = 0,
) -> float:
    """P(mean forward change over n random eligible days <= observed).

    One-sided against the proposition's direction (a plummet = very
    negative mean). Random-day draws inherit the sample's autocorrelation
    and trend, unlike an i.i.d. t-test.
    """
    fwd = _forward_changes(df, h).to_numpy()[_eligible_mask(df, h)]
    if len(fwd) < n_events + 10 or n_events == 0:
        return np.nan
    rng = np.random.default_rng(seed)
    draws = rng.choice(len(fwd), size=(n_draws, n_events), replace=True)
    null_means = fwd[draws].mean(axis=1)
    return float((null_means <= observed_mean).mean())


def study_stats(
    df: pd.DataFrame,
    ev: pd.DataFrame,
    h_star: int = H_STAR,
    n_draws: int = 10_000,
    seed: int = 0,
) -> dict:
    """Headline test of the proposition plus the catch-up decomposition."""
    col = f"d_basis_{h_star}d"
    evh = ev.dropna(subset=[col])
    out: dict = {"n_events": len(ev), "n_evaluable": len(evh), "h_star": h_star}
    if len(evh) < 3:
        return out
    d = evh[col]
    fwd_all = _forward_changes(df, h_star).to_numpy()[_eligible_mask(df, h_star)]
    t, p_t = stats.ttest_1samp(d, 0.0)
    out.update(
        mean_bp=d.mean(),
        median_bp=d.median(),
        hit_rate=float((d < 0).mean()),
        base_rate=float((fwd_all < 0).mean()),
        t_stat=float(t),
        p_t_two_sided=float(p_t),
        p_permutation=permutation_pvalue(
            df, d.mean(), len(evh), h=h_star, n_draws=n_draws, seed=seed
        ),
        unconditional_mean_bp=float(fwd_all.mean()),
        median_retrace_frac=float(evh["retrace_frac"].median()),
        mean_d_fix_bp=float(evh["d_fix_bp"].mean()),
        mean_d_ois_bp=float(evh["d_ois_bp"].mean()),
    )

    # Mechanical vs genuine: how much of the plummet was owed catch-up?
    sub = evh.dropna(subset=["pending_bp"])
    if len(sub) >= 5:
        slope, intercept, r, p, _ = stats.linregress(sub["pending_bp"], sub[col])
        resid = sub[col] - sub["pending_bp"]
        t_res, p_res = stats.ttest_1samp(resid, 0.0)
        out.update(
            catchup_slope=float(slope),
            catchup_intercept=float(intercept),
            catchup_r=float(r),
            mean_pending_bp=float(sub["pending_bp"].mean()),
            mean_beyond_catchup_bp=float(resid.mean()),
            t_beyond_catchup=float(t_res),
            p_beyond_catchup=float(p_res),
        )
        mech_mask = sub["mechanical"].astype(bool)
        for label, grp in (
            ("mechanical", sub[mech_mask]),
            ("genuine", sub[~mech_mask]),
        ):
            g: dict = {"n": len(grp)}
            if len(grp) >= 3:
                tg, pg = stats.ttest_1samp(grp[col], 0.0)
                g.update(
                    mean_bp=float(grp[col].mean()),
                    hit_rate=float((grp[col] < 0).mean()),
                    t_stat=float(tg),
                    p_t=float(pg),
                    p_permutation=permutation_pvalue(
                        df, grp[col].mean(), len(grp), h=h_star,
                        n_draws=n_draws, seed=seed + 1,
                    ),
                )
            out[label] = g
    return out


def horizon_curve(
    ev: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    n_boot: int = 4000,
    seed: int = 0,
) -> pd.DataFrame:
    """Mean forward basis change by horizon with bootstrap 95% CI over events."""
    rng = np.random.default_rng(seed)
    rows = []
    for h in horizons:
        d = ev[f"d_basis_{h}d"].dropna().to_numpy()
        if len(d) < 3:
            continue
        boots = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
        rows.append(
            {
                "h": h,
                "mean_bp": d.mean(),
                "ci_lo": np.percentile(boots, 2.5),
                "ci_hi": np.percentile(boots, 97.5),
                "n": len(d),
            }
        )
    return pd.DataFrame(rows)


def event_paths(
    df: pd.DataFrame, events: list[int], back: int = 10, fwd: int = 20
) -> pd.DataFrame:
    """Basis path around each event, re-based to 0 at the event date."""
    basis = df["basis_bp"].to_numpy()
    n = len(df)
    offsets = range(-back, fwd + 1)
    data = {}
    for i in events:
        path = [
            basis[i + k] - basis[i] if 0 <= i + k < n else np.nan for k in offsets
        ]
        data[df.index[i]] = path
    return pd.DataFrame(data, index=list(offsets))


def threshold_grid(
    df: pd.DataFrame,
    signal: str = "level",
    z_entries: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5),
    horizons: tuple[int, ...] = (5, 10, 15, 20),
    cooldown: int = H_STAR,
    n_draws: int = 4000,
    seed: int = 0,
) -> pd.DataFrame:
    """Mean forward change and permutation p across the z x horizon grid."""
    rows = []
    for z in z_entries:
        events = extract_events(df, signal=signal, z_entry=z, cooldown=cooldown)
        ev = event_study(df, events)
        for h in horizons:
            d = ev[f"d_basis_{h}d"].dropna() if not ev.empty else pd.Series(dtype=float)
            rows.append(
                {
                    "z_entry": z,
                    "h": h,
                    "n": len(d),
                    "mean_bp": d.mean() if len(d) else np.nan,
                    "p_permutation": (
                        permutation_pvalue(df, d.mean(), len(d), h=h,
                                           n_draws=n_draws, seed=seed)
                        if len(d) >= 3
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def strategy_pnl(
    ev: pd.DataFrame, h_star: int = H_STAR, cost_bp: float = 0.5
) -> pd.DataFrame:
    """Naive P&L: 'sell the basis' at each event, hold h_star days.

    pnl_bp = -(forward basis change) - cost. This treats the SPOT basis as
    tradeable, which it is not, quite: the market instrument is the front
    FRA (or Euribor future) vs ESTR OIS/futures, which settles on a FUTURE
    fix that already prices predictable catch-up. So the mechanical part of
    this P&L is NOT capturable in practice - read it next to the
    mechanical/genuine split, and weight the 'genuine' bucket.
    """
    col = f"d_basis_{h_star}d"
    trades = ev.dropna(subset=[col]).copy()
    trades["pnl_bp"] = -trades[col] - cost_bp
    trades["cum_pnl_bp"] = trades["pnl_bp"].cumsum()
    return trades


def strategy_stats(trades: pd.DataFrame) -> dict:
    if len(trades) < 3:
        return {"n_trades": len(trades)}
    pnl = trades["pnl_bp"]
    t, p = stats.ttest_1samp(pnl, 0.0)
    cum = trades["cum_pnl_bp"]
    return {
        "n_trades": len(trades),
        "total_bp": float(pnl.sum()),
        "mean_bp": float(pnl.mean()),
        "median_bp": float(pnl.median()),
        "hit_rate": float((pnl > 0).mean()),
        "t_stat": float(t),
        "p_t": float(p),
        "worst_bp": float(pnl.min()),
        "max_drawdown_bp": float((cum.cummax() - cum).max()),
    }

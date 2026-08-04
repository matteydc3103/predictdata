"""Validation of the FRA-ESTR basis pipeline: math identities, recovery of
the synthetic generator's known truth, and no-lookahead guarantees."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from basis_analysis import backtest as bt  # noqa: E402
from basis_analysis.data import build_dataset  # noqa: E402
from basis_analysis.stickiness import (  # noqa: E402
    PassthroughFit,
    fit_passthrough,
    pending_catchup,
    walkforward_pending,
)

FIX = ROOT / "data" / "basis" / "euribor3m_fix_SYNTHETIC.csv"
OIS = ROOT / "data" / "basis" / "estr3m_ois_SYNTHETIC.csv"


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return bt.add_signals(build_dataset(FIX, OIS))


@pytest.fixture(scope="module")
def events(df) -> list[int]:
    return bt.extract_events(df, signal="level", z_entry=1.5, cooldown=10)


@pytest.fixture(scope="module")
def ev(df, events) -> pd.DataFrame:
    return bt.event_study(df, events)


def test_generator_is_deterministic():
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_sample_basis_data import simulate

    a, b = simulate(), simulate()
    pd.testing.assert_frame_equal(a, b)


def test_basis_definition(df):
    np.testing.assert_allclose(
        df["basis_bp"], (df["fix"] - df["ois"]) * 100.0, atol=1e-9
    )


def test_pending_weights_single_step():
    """One 10bp OIS step, betas known -> pending = pass-through still owed."""
    fit = PassthroughFit(alpha=0.0, betas=np.array([0.4, 0.3, 0.2, 0.1]),
                         r2=1.0, nobs=100)
    np.testing.assert_allclose(fit.pending_weights, [0.6, 0.3, 0.1])

    n, t0 = 20, 10
    ois = np.zeros(n)
    ois[t0:] = 0.10  # +10bp step at t0
    frame = pd.DataFrame(
        {"fix": np.zeros(n), "ois": ois},
        index=pd.bdate_range("2024-01-01", periods=n),
    )
    pend = pending_catchup(frame, fit)
    np.testing.assert_allclose(pend.iloc[t0], 6.0)  # (0.3+0.2+0.1)*10
    np.testing.assert_allclose(pend.iloc[t0 + 1], 3.0)
    np.testing.assert_allclose(pend.iloc[t0 + 2], 1.0)
    np.testing.assert_allclose(pend.iloc[t0 + 3], 0.0, atol=1e-12)


def test_passthrough_recovers_generator_truth(df):
    """Generator: geometric lags, mean ~3 days, total pass-through 1.0."""
    fit = fit_passthrough(df, n_lags=10)
    assert 0.80 <= fit.total_passthrough <= 1.10
    assert 1.5 <= fit.mean_lag_days <= 4.5


def test_events_are_crossings_with_cooldown(df, events):
    z = df["z_level"].to_numpy()
    assert len(events) > 10
    for i in events:
        assert z[i] >= 1.5
        assert z[i - 1] < 1.5
    assert (np.diff(events) >= 10).all()


def test_decomposition_identity(ev):
    """d_basis over the horizon must equal d_fix - d_ois exactly."""
    sub = ev.dropna(subset=["d_basis_10d"])
    np.testing.assert_allclose(
        sub["d_basis_10d"], sub["d_fix_bp"] - sub["d_ois_bp"], atol=1e-9
    )


def test_no_lookahead(df):
    """Signals and walk-forward pending at t are unchanged when all data
    after t is deleted - nothing feeds back from the future."""
    cut = len(df) - 150
    full = df
    trunc = bt.add_signals(build_dataset(FIX, OIS).iloc[:cut])
    cols = ["z_level", "z_widen", "basis_mean", "pending_bp", "basis_adj_bp"]
    pd.testing.assert_frame_equal(trunc[cols], full[cols].iloc[:cut])

    ev_full = bt.extract_events(full.iloc[:cut], z_entry=1.5, cooldown=10)
    ev_trunc = bt.extract_events(trunc, z_entry=1.5, cooldown=10)
    assert ev_full == ev_trunc


def test_event_study_stats_shape(df, ev):
    stats = bt.study_stats(df, ev, n_draws=500)
    assert stats["n_evaluable"] >= 10
    for key in ("mean_bp", "hit_rate", "p_permutation", "mean_pending_bp",
                "mean_beyond_catchup_bp"):
        assert key in stats
    assert 0.0 <= stats["p_permutation"] <= 1.0
    # the split only covers events with a pending estimate
    assert (stats["mechanical"]["n"] + stats["genuine"]["n"]
            == ev["pending_bp"].notna().sum())


def test_strategy_pnl_is_minus_change_minus_cost(ev):
    trades = bt.strategy_pnl(ev, h_star=10, cost_bp=0.5)
    np.testing.assert_allclose(
        trades["pnl_bp"], -trades["d_basis_10d"] - 0.5, atol=1e-12
    )


def test_ois_alignment_shifts_one_day():
    same = build_dataset(FIX, OIS, ois_align="same_day")
    prev = build_dataset(FIX, OIS, ois_align="prev_day")
    # under prev_day, today's row carries yesterday's OIS
    d = same.index[500]
    d_prev = same.index[499]
    assert prev.loc[d, "ois"] == same.loc[d_prev, "ois"]
    assert prev.loc[d, "fix"] == same.loc[d, "fix"]

"""Distributed-lag pass-through model of the Euribor fix on the OIS leg.

Model (daily changes, both in bp):

    d_fix(t) = alpha + sum_{k=0..K} beta_k * d_ois(t-k) + eps(t)

If the fix were instantaneous, beta_0 ~ 1 and the rest ~ 0. In reality the
mass sits on k >= 1: the fix digests an OIS move over several days. The tail
sums of the betas then give, at any t, the pass-through still OWED to OIS
moves that already happened:

    pending(t) = sum_{j=0..K-1} w_j * d_ois(t-j),   w_j = sum_{k>j} beta_k

Under a (near-)martingale OIS leg, pending(t) is the expected MECHANICAL
drift of the fix - and hence of the basis - over the next ~K days. Two uses:

* catch-up-adjusted basis  B*(t) = B(t) + pending(t): where the basis will
  settle once the fix finishes catching up, all else equal. This is the
  fair day-to-day comparison of a sticky fix with a live OIS.
* event decomposition: realised 10-day basis change vs pending at entry
  separates "stale-fix artefact" widenings from genuine premium spikes.

`walkforward_pending` refits on an expanding window so pending(t) only ever
uses data up to t - safe to feed the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PassthroughFit:
    alpha: float
    betas: np.ndarray  # index k = 0..n_lags
    r2: float
    nobs: int

    @property
    def total_passthrough(self) -> float:
        return float(self.betas.sum())

    @property
    def mean_lag_days(self) -> float:
        w = self.betas
        return float((np.arange(len(w)) * w).sum() / w.sum())

    @property
    def pending_weights(self) -> np.ndarray:
        """w_j = sum of betas at lags strictly greater than j, j = 0..K-1."""
        return np.cumsum(self.betas[::-1])[::-1][1:]


def _lag_matrix(x: np.ndarray, n_lags: int) -> np.ndarray:
    """Rows t = n_lags..len(x)-1, columns x[t], x[t-1], ..., x[t-n_lags]."""
    return np.column_stack(
        [x[n_lags - k : len(x) - k] for k in range(n_lags + 1)]
    )


def fit_passthrough(df: pd.DataFrame, n_lags: int = 10) -> PassthroughFit:
    d_fix = df["fix"].diff().to_numpy() * 100.0
    d_ois = df["ois"].diff().to_numpy() * 100.0
    y = d_fix[n_lags + 1 :]
    X = _lag_matrix(d_ois[1:], n_lags)
    keep = ~(np.isnan(y) | np.isnan(X).any(axis=1))
    y, X = y[keep], X[keep]
    if len(y) < 10 * (n_lags + 2):
        raise ValueError(f"only {len(y)} obs to fit {n_lags + 2} parameters")
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    r2 = 1.0 - resid.var() / y.var()
    return PassthroughFit(alpha=float(coef[0]), betas=coef[1:], r2=float(r2), nobs=len(y))


def _pending_from_weights(d_ois_bp: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """pending(t) = sum_j weights[j] * d_ois(t-j), causal convolution."""
    x = np.nan_to_num(d_ois_bp)
    return np.convolve(x, weights)[: len(x)]


def pending_catchup(df: pd.DataFrame, fit: PassthroughFit) -> pd.Series:
    """Full-sample-fit pending catch-up in bp (diagnostic use)."""
    d_ois = df["ois"].diff().to_numpy() * 100.0
    out = _pending_from_weights(d_ois, fit.pending_weights)
    out[: len(fit.pending_weights)] = np.nan
    return pd.Series(out, index=df.index, name="pending_bp")


def walkforward_pending(
    df: pd.DataFrame,
    n_lags: int = 10,
    min_obs: int = 250,
    refit_every: int = 21,
) -> pd.Series:
    """pending(t) using coefficients fitted on data up to t only.

    Refits the pass-through regression every `refit_every` business days on
    an expanding window; NaN until `min_obs` daily changes are available.
    """
    d_ois = df["ois"].diff().to_numpy() * 100.0
    n = len(df)
    out = np.full(n, np.nan)
    start = min_obs + n_lags + 1
    for block_start in range(start, n, refit_every):
        fit = fit_passthrough(df.iloc[:block_start], n_lags=n_lags)
        pend = _pending_from_weights(d_ois, fit.pending_weights)
        block_end = min(block_start + refit_every, n)
        out[block_start:block_end] = pend[block_start:block_end]
    return pd.Series(out, index=df.index, name="pending_bp")

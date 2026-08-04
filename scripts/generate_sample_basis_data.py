"""Rebuild the SYNTHETIC Euribor3M-fix / 3M-ESTR-OIS sample in data/basis/.

The simulated world deliberately contains BOTH mechanisms that can make the
spot FRA-ESTR basis (Euribor3M fix minus 3M ESTR OIS) spike and then fall:

1. STICKY-FIX artefact - the fix is a distributed lag of the OIS leg
   (geometric weights, mean lag ~3 days), so a fast OIS rally widens the
   measured basis mechanically and it decays as panel banks catch up.
2. GENUINE credit/liquidity stress - a slow AR(1) premium (~7bp) with
   Poisson stress jumps that decay with a 5-12 day half-life, plus a few
   large hand-placed crises where an OIS rally and a credit jump hit
   together (the realistic confound, e.g. a March-2023-style bank scare).

Because both live in the data, the pipeline's job of separating them can be
demonstrated end to end. Numbers are calibrated to look like 2019-2026 EUR
rates but ARE NOT REAL - do not draw market conclusions from them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260804
START, END = "2019-01-02", "2026-07-31"

# (date, ois shock in %, credit jump in bp, half-life in business days)
CRISES = [
    ("2020-03-12", -0.18, 30.0, 10.0),
    ("2022-09-28", -0.10, 15.0, 7.0),
    ("2023-03-13", -0.30, 28.0, 9.0),
    ("2024-08-05", -0.15, 12.0, 6.0),
    ("2025-04-07", -0.12, 14.0, 7.0),
]

# Anchor points for the policy-path level the OIS oscillates around.
ANCHORS = {
    "2019-01-02": -0.40,
    "2020-02-14": -0.45,
    "2020-03-20": -0.55,
    "2021-12-31": -0.55,
    "2022-02-01": -0.50,
    "2022-06-01": -0.20,
    "2022-12-30": 2.20,
    "2023-06-30": 3.55,
    "2023-09-29": 3.90,
    "2024-03-28": 3.85,
    "2024-09-30": 3.10,
    "2025-06-30": 2.00,
    "2026-07-31": 1.85,
}

FIX_LAG_Q = 0.75  # geometric pass-through weight decay -> mean lag ~3 days
FIX_LAGS = 15


def _decay_profile(n: int, start: int, jump: float, half_life: float) -> np.ndarray:
    """Additive path: `jump` at `start`, exponential decay to zero after."""
    out = np.zeros(n)
    k = np.arange(n - start)
    out[start:] = jump * 0.5 ** (k / half_life)
    return out


def simulate() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range(START, END)
    n = len(dates)
    pos = {d: i for i, d in enumerate(dates)}

    anchors = pd.Series(
        {pd.Timestamp(k): v for k, v in ANCHORS.items()}, name="base"
    ).sort_index()
    base = (
        anchors.reindex(anchors.index.union(dates))
        .interpolate(method="time")
        .reindex(dates)
        .to_numpy()
    )

    # Stationary AR(1) wiggle plus mean-zero permanent repricing jumps.
    ar = np.zeros(n)
    eps = rng.normal(0.0, 0.018, n)
    for t in range(1, n):
        ar[t] = 0.85 * ar[t - 1] + eps[t]
    jumps = np.where(rng.random(n) < 8 / 260, rng.normal(0.0, 0.06, n), 0.0)

    ois = base + ar + np.cumsum(jumps)

    prem = np.zeros(n)  # credit/liquidity premium, bp
    prem_ar = np.zeros(n)
    peps = rng.normal(0.0, 0.30, n)
    for t in range(1, n):
        prem_ar[t] = 0.985 * prem_ar[t - 1] + peps[t]
    prem += 7.0 + prem_ar

    # Random credit stress episodes (~4 per year, decay half-life 5-12 days).
    for t in np.flatnonzero(rng.random(n) < 4 / 260):
        prem += _decay_profile(n, t, 4.0 + rng.exponential(8.0), rng.uniform(5, 12))

    # Hand-placed crises: OIS rally over 3 days (60% retraced) + credit jump.
    for day, shock, jump_bp, hl in CRISES:
        t = pos[pd.Timestamp(day)]
        for lag, frac in enumerate((0.5, 0.3, 0.2)):
            ois += _decay_profile(n, t + lag, shock * frac * 0.6, 15.0)
            ois[t + lag :] += shock * frac * 0.4
        prem += _decay_profile(n, t, jump_bp, hl)

    prem = np.maximum(prem, 1.0)

    # Fix leg: geometric distributed lag of the OIS leg + premium + noise.
    w = FIX_LAG_Q ** np.arange(FIX_LAGS + 1)
    w /= w.sum()
    padded = np.concatenate([np.full(FIX_LAGS, ois[0]), ois])
    fix_core = np.convolve(padded, w, mode="valid")
    fix = fix_core + prem / 100.0 + rng.normal(0.0, 0.0015, n)

    return pd.DataFrame(
        {
            "date": dates,
            "euribor3m_fix": np.round(fix, 3),  # published to 3 decimals
            "estr3m_ois": np.round(ois, 4),
            "true_premium_bp": np.round(prem, 2),  # generator truth, for tests
        }
    )


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "data" / "basis"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = simulate()
    df[["date", "euribor3m_fix"]].rename(columns={"euribor3m_fix": "rate"}).to_csv(
        out_dir / "euribor3m_fix_SYNTHETIC.csv", index=False
    )
    df[["date", "estr3m_ois"]].rename(columns={"estr3m_ois": "rate"}).to_csv(
        out_dir / "estr3m_ois_SYNTHETIC.csv", index=False
    )
    df[["date", "true_premium_bp"]].to_csv(
        out_dir / "true_premium_SYNTHETIC.csv", index=False
    )
    print(f"Wrote {len(df)} business days to {out_dir}")


if __name__ == "__main__":
    main()

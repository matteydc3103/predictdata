"""Generate the SYNTHETIC sample forecast file bundled with the repo.

The economists and institutions are real, well-known US forecasters, but
every forecast value here is SIMULATED: each economist is assigned a
made-up skill level (noise size) and bias, and forecasts are drawn around
the true actual value. The file exists only so the ranking pipeline runs
end-to-end out of the box - replace it with real survey data (Bloomberg
ECFC, WSJ Economic Forecasting Survey, Philadelphia Fed SPF) for genuine
rankings.

Run from the repo root:  python scripts/generate_sample_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTUALS = REPO_ROOT / "data" / "actuals.csv"
OUT = REPO_ROOT / "data" / "forecasts_sample_SYNTHETIC.csv"

# (economist, institution, skill multiplier - lower = more accurate, bias)
ECONOMISTS = [
    ("Stephen Stanley", "Santander US Capital Markets", 0.75, 0.05),
    ("Michael Feroli", "J.P. Morgan", 0.80, -0.05),
    ("Jan Hatzius", "Goldman Sachs", 0.85, 0.00),
    ("Ellen Zentner", "Morgan Stanley", 0.95, -0.10),
    ("Mark Zandi", "Moody's Analytics", 1.00, 0.10),
    ("Diane Swonk", "KPMG", 1.05, 0.05),
    ("Gregory Daco", "EY-Parthenon", 1.10, -0.05),
    ("Torsten Slok", "Apollo Global Management", 1.15, 0.15),
    ("James Knightley", "ING", 1.20, -0.15),
    ("Nathan Sheets", "Citi", 1.25, 0.10),
]

# Baseline forecast dispersion per indicator, in the indicator's units.
BASE_NOISE = {
    "cpi_yoy_dec": 0.45,
    "unemployment_rate_dec": 0.35,
    "gdp_annual": 0.60,
}

# Years where every forecaster missed big (pandemic shock and its rebound).
SHOCK_YEARS = {"2020": 3.5, "2021": 2.5}


def main() -> None:
    rng = np.random.default_rng(20260710)
    actuals = pd.read_csv(ACTUALS, comment="#")

    rows = []
    for _, rel in actuals.iterrows():
        noise = BASE_NOISE[rel["indicator"]] * SHOCK_YEARS.get(str(rel["period"]), 1.0)
        for name, firm, skill, bias in ECONOMISTS:
            forecast = rel["actual"] + bias + rng.normal(0.0, noise * skill)
            rows.append(
                {
                    "economist": name,
                    "institution": firm,
                    "indicator": rel["indicator"],
                    "period": rel["period"],
                    "forecast": round(float(forecast), 1),
                }
            )

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(f"Wrote {len(df)} synthetic forecasts to {OUT}")


if __name__ == "__main__":
    main()

"""Load raw NFP survey inputs and rebuild the scored submission panel.

Only the workbook's [INPUT] columns are stored in the repo
(data/nfp/releases.csv and data/nfp/submissions.csv); every derived
quantity is recomputed here so the numbers can be audited and the
formulas can't silently drift.

Methodology (matches the workbook README):
* Consensus = per-release median of the panel.
* Dispersion = 1.4826 x MAD, floored at 15 (thousands of jobs).
* Scored against the FIRST PRINT. COVID reference periods
  (Mar-2020 - Dec-2020) carry include='N' and are excluded from
  metrics unless ``include_all`` is passed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_CSV = REPO_ROOT / "data" / "nfp" / "releases.csv"
SUBMISSIONS_CSV = REPO_ROOT / "data" / "nfp" / "submissions.csv"

DISPERSION_FLOOR = 15.0
MAD_SCALE = 1.4826
BOLD_THRESHOLD = 0.25  # |zDev| above this counts as a bold call


def load_releases(path: str | Path = RELEASES_CSV) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["release_date"])
    df["include"] = df["include"].astype(str).str.upper().eq("Y")
    df["key"] = df["release_date"].dt.strftime("%Y-%m-%d") + "|" + df["period"]
    return df.sort_values("release_date").reset_index(drop=True)


def load_submissions(path: str | Path = SUBMISSIONS_CSV) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["release_date"])
    df["economist"] = df["economist"].fillna("")
    df["estimate"] = pd.to_numeric(df["estimate"], errors="coerce")
    return df.dropna(subset=["estimate"]).reset_index(drop=True)


def build_panel(
    releases: pd.DataFrame | None = None,
    submissions: pd.DataFrame | None = None,
    include_all: bool = False,
) -> pd.DataFrame:
    """Return one row per submission with all derived scoring columns.

    Consensus and dispersion are computed from the full panel of each
    release (including COVID months - the include flag only controls
    which releases enter the *metrics*, mirroring the workbook).
    """
    releases = load_releases() if releases is None else releases
    submissions = load_submissions() if submissions is None else submissions

    per_release = submissions.groupby("period")["estimate"]
    consensus = per_release.median().rename("consensus")
    mad = (
        submissions.assign(
            absdev=lambda d: (d["estimate"] - d["period"].map(consensus)).abs()
        )
        .groupby("period")["absdev"]
        .median()
    )
    dispersion = (MAD_SCALE * mad).clip(lower=DISPERSION_FLOOR).rename("dispersion")

    panel = submissions.merge(
        releases[["period", "release_date", "key", "actual", "prior", "revised", "include"]],
        on=["period", "release_date"],
        how="inner",
    )
    panel["consensus"] = panel["period"].map(consensus)
    panel["dispersion"] = panel["period"].map(dispersion)

    panel["error"] = panel["estimate"] - panel["actual"]
    panel["abs_error"] = panel["error"].abs()
    panel["z_err"] = panel["abs_error"] / panel["dispersion"]
    panel["dev"] = panel["estimate"] - panel["consensus"]
    panel["z_dev"] = panel["dev"] / panel["dispersion"]
    panel["surprise"] = panel["actual"] - panel["consensus"]
    panel["z_surprise"] = panel["surprise"] / panel["dispersion"]
    panel["consensus_abs_error"] = (panel["consensus"] - panel["actual"]).abs()

    panel["beat_median"] = np.select(
        [
            panel["abs_error"] < panel["consensus_abs_error"],
            panel["abs_error"] == panel["consensus_abs_error"],
        ],
        [1.0, 0.5],
        default=0.0,
    )
    panel["bold"] = panel["z_dev"].abs() > BOLD_THRESHOLD
    panel["dir_correct"] = np.sign(panel["dev"]) == np.sign(panel["surprise"])

    if not include_all:
        panel = panel[panel["include"]]
    return panel.reset_index(drop=True)

"""Load and align the Euribor3M fix and 3M ESTR OIS series."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATE_CANDIDATES = ("date", "dates", "day", "asof", "as_of", "time", "period")
VALUE_CANDIDATES = ("rate", "value", "px_last", "last", "close", "fix", "mid", "px")


def load_series(path: str | Path, name: str) -> pd.Series:
    """Read a daily rate series (percent) from CSV/Excel, columns matched
    flexibly: a date-ish column plus a rate-ish (or the only other) column."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, comment="#")
    cols = {str(c).strip().lower(): c for c in df.columns}

    date_col = next((cols[c] for c in DATE_CANDIDATES if c in cols), None)
    if date_col is None:
        raise ValueError(f"{path}: no date column among {list(df.columns)}")
    value_col = next((cols[c] for c in VALUE_CANDIDATES if c in cols), None)
    if value_col is None:
        others = [c for c in df.columns if c != date_col]
        if len(others) != 1:
            raise ValueError(f"{path}: cannot identify the rate column in {others}")
        value_col = others[0]

    s = pd.Series(
        pd.to_numeric(df[value_col], errors="coerce").to_numpy(),
        index=pd.to_datetime(df[date_col]),
        name=name,
    ).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        raise ValueError(f"{path}: no usable rows")
    return s


def build_dataset(
    fix_path: str | Path,
    ois_path: str | Path,
    ois_align: str = "same_day",
) -> pd.DataFrame:
    """Inner-join the two series on common dates; basis_bp = (fix - ois) * 100.

    ois_align:
      same_day - compare today's 11:00 CET fix with today's OIS quote.
      prev_day - compare today's fix with the PREVIOUS trading day's OIS
                 close (the OIS then has no post-fixing information).
    The fix is set at 11:00 CET while an OIS close is end-of-day, so neither
    alignment is exact; a conclusion that only holds under one of them is a
    timing artefact, so run both.
    """
    fix = load_series(fix_path, "fix")
    ois = load_series(ois_path, "ois")
    if ois_align == "prev_day":
        ois = ois.shift(1)
    elif ois_align != "same_day":
        raise ValueError(f"unknown ois_align: {ois_align!r}")

    df = pd.concat([fix, ois], axis=1, join="inner").dropna()
    if len(df) < 300:
        raise ValueError(
            f"only {len(df)} overlapping dates - need at least 300 for the "
            "rolling statistics to mean anything"
        )
    df["basis_bp"] = (df["fix"] - df["ois"]) * 100.0
    df.index.name = "date"
    return df

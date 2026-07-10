"""Load and normalise economist forecast data from CSV or Excel files.

The pipeline works on a tidy (long) table with one row per forecast:

    economist, institution, indicator, period, forecast

Column names in the source file are matched flexibly, so a spreadsheet
that says "Forecaster" / "Firm" / "Metric" / "Year" / "Prediction" loads
without changes. An optional ``actual`` column is carried through and used
when no separate actuals file provides a value for that release.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Canonical column -> accepted aliases (lower-cased, stripped).
COLUMN_ALIASES: dict[str, list[str]] = {
    "economist": ["economist", "forecaster", "name", "analyst", "chief economist"],
    "institution": ["institution", "firm", "bank", "company", "organisation", "organization", "employer"],
    "indicator": ["indicator", "series", "metric", "measure", "variable", "data point", "release"],
    "period": ["period", "year", "date", "reference period", "quarter", "month", "release period"],
    "forecast": ["forecast", "prediction", "estimate", "projection", "predicted", "call", "forecast value"],
    "actual": ["actual", "actual value", "result", "outturn", "released", "realized", "realised"],
}

REQUIRED = ["economist", "indicator", "period", "forecast"]


def _canonical_columns(columns: list[str]) -> dict[str, str]:
    """Map source column names to canonical names."""
    mapping: dict[str, str] = {}
    for col in columns:
        key = str(col).strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if key == canonical or key in aliases:
                if canonical not in mapping.values():
                    mapping[col] = canonical
                break
    return mapping


def load_forecasts(path: str | Path) -> pd.DataFrame:
    """Read a forecast file (.csv, .xlsx or .xls) and return a tidy frame.

    Raises ValueError when required columns cannot be identified, listing
    what was found so the caller can fix the spreadsheet headers.
    """
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    mapping = _canonical_columns(list(df.columns))
    df = df.rename(columns=mapping)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"Could not identify required column(s) {missing} in {path.name}. "
            f"Found columns: {list(df.columns)}. "
            f"Accepted aliases: { {m: COLUMN_ALIASES[m] for m in missing} }"
        )

    if "institution" not in df.columns:
        df["institution"] = ""

    keep = ["economist", "institution", "indicator", "period", "forecast"]
    if "actual" in df.columns:
        keep.append("actual")
    df = df[keep].copy()

    df["economist"] = df["economist"].astype(str).str.strip()
    df["institution"] = df["institution"].fillna("").astype(str).str.strip()
    df["indicator"] = df["indicator"].astype(str).str.strip()
    df["period"] = df["period"].astype(str).str.strip()
    df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce")

    df = df.dropna(subset=["forecast"])
    df = df[df["economist"] != ""]
    return df.reset_index(drop=True)


def load_actuals(path: str | Path) -> pd.DataFrame:
    """Read an actuals file with columns indicator, period, actual."""
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, comment="#")
    df = df.rename(columns=_canonical_columns(list(df.columns)))
    missing = [c for c in ("indicator", "period", "actual") if c not in df.columns]
    if missing:
        raise ValueError(f"Actuals file {path.name} is missing column(s): {missing}")
    df["indicator"] = df["indicator"].astype(str).str.strip()
    df["period"] = df["period"].astype(str).str.strip()
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    return df.dropna(subset=["actual"]).reset_index(drop=True)

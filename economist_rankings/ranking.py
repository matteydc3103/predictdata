"""Aggregate per-forecast scores into economist leaderboards."""

from __future__ import annotations

import pandas as pd


def _aggregate(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    grouped = df.groupby(by, dropna=False)
    out = grouped.agg(
        institution=("institution", "first"),
        n_forecasts=("forecast", "size"),
        skill_score=("percentile", "mean"),
        norm_abs_error=("norm_abs_error", "mean"),
        mae=("abs_error", "mean"),
        rmse=("error", lambda e: float((e**2).mean() ** 0.5)),
        bias=("error", "mean"),
    ).reset_index()

    # Drop the institution column when it's part of the grouping already.
    if "institution" in by:
        out = out.loc[:, ~out.columns.duplicated()]

    out = out.sort_values(["skill_score", "norm_abs_error"], ascending=[False, True])
    out.insert(0, "rank", range(1, len(out) + 1))
    return out.reset_index(drop=True).round(
        {"skill_score": 1, "norm_abs_error": 3, "mae": 3, "rmse": 3, "bias": 3}
    )


def overall_ranking(df: pd.DataFrame, min_forecasts: int = 1) -> pd.DataFrame:
    """One row per economist across every indicator and period.

    ``skill_score`` is the average within-release percentile (100 = always
    the closest forecaster, 50 = middle of the pack). ``bias`` > 0 means the
    economist tends to overshoot the actual number.
    """
    out = _aggregate(df, ["economist"])
    out = out[out["n_forecasts"] >= min_forecasts]
    out["rank"] = range(1, len(out) + 1)
    return out.reset_index(drop=True)


def ranking_by_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """Leaderboard per indicator - who is best at CPI, at GDP, etc."""
    parts = []
    for indicator, sub in df.groupby("indicator"):
        ranked = _aggregate(sub, ["economist"])
        ranked.insert(1, "indicator", indicator)
        parts.append(ranked)
    return pd.concat(parts, ignore_index=True)


def ranking_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Skill score per economist per year (year = first 4 chars of period)."""
    df = df.copy()
    df["year"] = df["period"].str.extract(r"(\d{4})")[0]
    pivot = df.pivot_table(
        index="economist", columns="year", values="percentile", aggfunc="mean"
    ).round(1)
    pivot["overall"] = df.groupby("economist")["percentile"].mean().round(1)
    return pivot.sort_values("overall", ascending=False).reset_index()

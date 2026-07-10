"""Command-line entry point.

Usage:
    python -m economist_rankings --forecasts data/forecasts_sample_SYNTHETIC.csv \
        --actuals data/actuals.csv --output output/rankings.xlsx
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .forecasts import load_actuals, load_forecasts
from .ranking import overall_ranking, ranking_by_indicator, ranking_by_year
from .report import write_report
from .scoring import merge_actuals, score_forecasts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FORECASTS = REPO_ROOT / "data" / "forecasts_sample_SYNTHETIC.csv"
DEFAULT_ACTUALS = REPO_ROOT / "data" / "actuals.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="economist_rankings",
        description="Rank economists by the accuracy of their published forecasts.",
    )
    parser.add_argument(
        "--forecasts",
        default=str(DEFAULT_FORECASTS),
        help="CSV/Excel of forecasts (economist, institution, indicator, period, forecast).",
    )
    parser.add_argument(
        "--actuals",
        default=str(DEFAULT_ACTUALS),
        help="CSV/Excel of released values (indicator, period, actual). "
        "Pass 'none' to rely on an 'actual' column inside the forecast file.",
    )
    parser.add_argument("--output", default="output/rankings.xlsx", help="Excel report path.")
    parser.add_argument(
        "--min-forecasts",
        type=int,
        default=5,
        help="Hide economists with fewer forecasts than this from the overall ranking.",
    )
    args = parser.parse_args(argv)

    forecasts = load_forecasts(args.forecasts)
    actuals = None if args.actuals.lower() == "none" else load_actuals(args.actuals)

    if Path(args.forecasts).resolve() == DEFAULT_FORECASTS.resolve():
        print(
            "NOTE: running on the bundled SYNTHETIC sample data. The economist "
            "names are real but their numbers are simulated for demonstration - "
            "point --forecasts at your own spreadsheet for real rankings.\n"
        )

    scored = score_forecasts(merge_actuals(forecasts, actuals))
    if scored.empty:
        print("No forecasts could be matched to actual values - check that the "
              "indicator and period labels match between the two files.")
        return 1

    overall = overall_ranking(scored, min_forecasts=args.min_forecasts)
    by_indicator = ranking_by_indicator(scored)
    by_year = ranking_by_year(scored)

    notes = [
        "skill_score: average within-release percentile. 100 = always the closest "
        "forecaster among peers, 50 = middle of the pack, 0 = always the furthest.",
        "norm_abs_error: average miss relative to the average forecaster's miss for the "
        "same release. Below 1.0 beats the consensus of misses.",
        "mae / rmse: mean absolute / root-mean-square error in the indicator's own units "
        "(only comparable within a single indicator).",
        "bias: mean signed error. Positive = tends to forecast too high.",
        f"Forecast file: {args.forecasts}",
        f"Actuals file: {args.actuals}",
    ]
    if Path(args.forecasts).resolve() == DEFAULT_FORECASTS.resolve():
        notes.insert(
            0,
            "SYNTHETIC DEMO DATA - forecast values were simulated (see "
            "scripts/generate_sample_data.py). Do not treat these rankings as real "
            "assessments of the named economists.",
        )

    out = write_report(args.output, overall, by_indicator, by_year, scored, notes)

    print(f"Scored {len(scored)} forecasts from {scored['economist'].nunique()} economists "
          f"across {scored['indicator'].nunique()} indicators.\n")
    cols = ["rank", "economist", "institution", "n_forecasts", "skill_score", "norm_abs_error", "bias"]
    print(overall[cols].to_string(index=False))
    print(f"\nFull report written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

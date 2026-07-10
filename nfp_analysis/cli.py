"""CLI: python -m nfp_analysis [report|signal|backtest]"""

from __future__ import annotations

import argparse

import pandas as pd

from .data import build_panel
from .report import build_report
from .signal import backtest, backtest_stats, parameter_sensitivity, signal_for_release
from .strategy import (
    bias_backtest,
    combined_backtest,
    dispersion_backtest,
    live_recommendation,
    strategy_stats,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nfp_analysis")
    sub = parser.add_subparsers(dest="cmd")
    p_report = sub.add_parser("report", help="run every test and write the Excel report")
    p_report.add_argument("--output", default="output/nfp_report.xlsx")
    p_report.add_argument("--start", default="2022-01-01", help="backtest start date")
    p_sig = sub.add_parser("signal", help="live recommendation for a release")
    p_sig.add_argument("--key", default=None, help="release key, e.g. '2026-07-02|Jun-26' (default: latest)")
    p_bt = sub.add_parser("backtest", help="walk-forward stats to the console")
    p_bt.add_argument("--start", default="2022-01-01")
    args = parser.parse_args(argv)

    pd.set_option("display.width", 160)
    panel = build_panel()

    if args.cmd == "signal":
        rec = live_recommendation(panel, key=args.key)
        top5 = rec.pop("top5_names")
        for k, v in rec.items():
            print(f"{k}: {round(v, 4) if isinstance(v, float) else v}")
        print("\nTop-5 trailing-IC forecasters for this release:")
        print(top5.round(3).to_string(index=False))
        return 0

    if args.cmd == "backtest":
        sig_bt = backtest(panel, start=args.start)
        print("Top-5 IC agreement signal (walk-forward):")
        for k, v in backtest_stats(sig_bt).items():
            print(f"  {k}: {round(v, 4) if isinstance(v, float) else v}")
        print("\nParameter sensitivity:")
        print(parameter_sensitivity(panel, start=args.start).round(3).to_string(index=False))
        print("\nStrategy comparison (fires / hit rate / avg signed z-surprise / t):")
        for name, bt, col in (
            ("dispersion", dispersion_backtest(panel, start=args.start), "direction"),
            ("top5_signal", sig_bt, "direction"),
            ("bias_tilt", bias_backtest(panel, start=args.start), "direction"),
            ("combined", combined_backtest(panel, start=args.start), "bias_direction"),
        ):
            s = strategy_stats(bt, col)
            print(f"  {name:12} fires={s['n_fires']:3} hit={s.get('hit_rate', float('nan')):.2f} "
                  f"avgZ={s.get('avg_signed_z_surprise', float('nan')):+.2f} t={s.get('t_stat', float('nan')):.2f}")
        return 0

    out = build_report(output=getattr(args, "output", "output/nfp_report.xlsx"),
                       start=getattr(args, "start", "2022-01-01"))
    print(f"Report written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

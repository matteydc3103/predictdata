"""Command-line entry point: load data, run the study, write the report."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import backtest as bt
from . import report as rp
from .data import build_dataset
from .stickiness import fit_passthrough

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FIX = REPO_ROOT / "data" / "basis" / "euribor3m_fix_SYNTHETIC.csv"
SAMPLE_OIS = REPO_ROOT / "data" / "basis" / "estr3m_ois_SYNTHETIC.csv"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="basis_analysis",
        description=(
            "Backtest 'a big spot FRA-ESTR basis widening plummets within N "
            "days', separating sticky-fix catch-up from genuine reversion."
        ),
    )
    p.add_argument("--fix", default=str(SAMPLE_FIX),
                   help="CSV/Excel with the Euribor 3M fix (date, rate in %%)")
    p.add_argument("--ois", default=str(SAMPLE_OIS),
                   help="CSV/Excel with the 3M ESTR OIS rate (date, rate in %%)")
    p.add_argument("--ois-align", choices=("same_day", "prev_day"),
                   default="same_day",
                   help="compare today's fix with today's or yesterday's OIS")
    p.add_argument("--signal", choices=("level", "widening"), default="level",
                   help="primary event definition (the other is reported as "
                        "robustness)")
    p.add_argument("--z-entry", type=float, default=1.5,
                   help="z-score threshold defining 'widened a lot'")
    p.add_argument("--horizon", type=int, default=bt.H_STAR,
                   help="the proposition's horizon in business days")
    p.add_argument("--cooldown", type=int, default=None,
                   help="min business days between events (default: horizon)")
    p.add_argument("--z-window", type=int, default=bt.Z_WINDOW,
                   help="rolling window for the z-scores")
    p.add_argument("--widen-days", type=int, default=bt.WIDEN_DAYS,
                   help="lookback for the widening-speed signal")
    p.add_argument("--cost-bp", type=float, default=0.5,
                   help="round-trip cost for the naive P&L")
    p.add_argument("--output", default=str(REPO_ROOT / "output" / "basis"),
                   help="directory for report.md, charts and events.csv")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    cooldown = args.cooldown if args.cooldown is not None else args.horizon
    horizons = tuple(range(1, max(21, args.horizon + 1)))
    synthetic = "SYNTHETIC" in Path(args.fix).name.upper()
    if synthetic:
        print("NOTE: running on the bundled SYNTHETIC sample - demo only.\n")

    df = build_dataset(args.fix, args.ois, ois_align=args.ois_align)
    df = bt.add_signals(df, z_window=args.z_window, widen_days=args.widen_days)
    fit = fit_passthrough(df)

    def run(signal: str, direction: int = 1):
        events = bt.extract_events(
            df, signal=signal, z_entry=args.z_entry,
            cooldown=cooldown, direction=direction,
        )
        ev = bt.event_study(df, events, horizons=horizons, h_star=args.horizon)
        return events, ev, bt.study_stats(df, ev, h_star=args.horizon)

    events, ev, stats_primary = run(args.signal)
    other = "widening" if args.signal == "level" else "level"
    _, _, stats_other = run(other)
    _, _, stats_tighten = run(args.signal, direction=-1)

    curve = bt.horizon_curve(ev, horizons=horizons)
    grid = bt.threshold_grid(df, signal=args.signal, cooldown=cooldown)
    trades = bt.strategy_pnl(ev, h_star=args.horizon, cost_bp=args.cost_bp)
    strat = bt.strategy_stats(trades)

    rp.chart_rates(df, out_dir / "rates.png")
    rp.chart_basis(df, ev, out_dir / "basis.png")
    rp.chart_event_path(bt.event_paths(df, events), ev, out_dir / "event_path.png")
    rp.chart_horizon(curve, args.horizon, out_dir / "horizon.png")
    rp.chart_catchup(ev, args.horizon, out_dir / "catchup_scatter.png")

    meta = {
        "n_days": len(df),
        "start": df.index[0].date(),
        "end": df.index[-1].date(),
        "ois_align": args.ois_align,
        "signal": args.signal,
        "z_entry": args.z_entry,
        "z_window": args.z_window,
        "widen_days": args.widen_days,
        "cooldown": cooldown,
        "cost_bp": args.cost_bp,
        "synthetic": synthetic,
    }
    path = rp.write_report(
        out_dir, meta, fit, stats_primary, stats_other, stats_tighten,
        curve, grid, strat, ev,
    )

    h = args.horizon
    print(f"Pass-through: total {fit.total_passthrough:.2f}, "
          f"mean lag {fit.mean_lag_days:.1f}d, R2 {fit.r2:.2f}")
    print(f"Events ({args.signal}, z>={args.z_entry:g}): "
          f"{stats_primary.get('n_evaluable', 0)} evaluable")
    if "mean_bp" in stats_primary:
        print(f"Mean {h}d change {stats_primary['mean_bp']:+.1f}bp "
              f"(hit {stats_primary['hit_rate']:.0%}, "
              f"permutation p={stats_primary['p_permutation']:.3f})")
        if "mean_pending_bp" in stats_primary:
            print(f"  of which owed catch-up {stats_primary['mean_pending_bp']:+.1f}bp; "
                  f"beyond catch-up {stats_primary['mean_beyond_catchup_bp']:+.1f}bp "
                  f"(p={stats_primary['p_beyond_catchup']:.3f})")
    print(f"\nReport: {path}")

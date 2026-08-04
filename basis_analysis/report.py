"""Markdown report + charts for the basis event study."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest import H_STAR, MECH_THRESHOLD_BP
from .stickiness import PassthroughFit

# Palette (validated defaults; first three categorical slots are all-pairs safe).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"


def _style(ax: plt.Axes, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def _legend(ax: plt.Axes) -> None:
    leg = ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="best")
    for line in leg.get_lines():
        line.set_linewidth(2.5)


def _save(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def chart_rates(df: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2), facecolor=SURFACE)
    ax.plot(df.index, df["fix"], color=BLUE, lw=1.6, label="Euribor 3M fix")
    ax.plot(df.index, df["ois"], color=ORANGE, lw=1.6, label="3M €STR OIS")
    _style(ax, "The two legs: Euribor 3M fix vs 3M €STR OIS", "%")
    _legend(ax)
    _save(fig, path)


def chart_basis(df: pd.DataFrame, ev: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2), facecolor=SURFACE)
    ax.plot(df.index, df["basis_bp"], color=BLUE, lw=1.4, label="Spot basis (fix − OIS)")
    ax.plot(
        df.index, df["basis_adj_bp"], color=AQUA, lw=1.1, alpha=0.9,
        label="Catch-up-adjusted basis",
    )
    ax.plot(
        df.index, df["basis_mean"], color=MUTED, lw=1.0, ls="--",
        label="Rolling 125d mean",
    )
    if not ev.empty:
        ax.scatter(
            ev["date"], ev["basis_bp"], s=26, color=ORANGE, zorder=5,
            label="Widening events",
        )
    _style(ax, "Spot FRA–€STR basis with widening events", "bp")
    _legend(ax)
    _save(fig, path)


def chart_event_path(paths: pd.DataFrame, ev: pd.DataFrame, path: Path) -> None:
    """Average basis path around events, split mechanical vs genuine."""
    fig, ax = plt.subplots(figsize=(8.5, 4.5), facecolor=SURFACE)
    x = paths.index.to_numpy()
    mean = paths.mean(axis=1).to_numpy()

    valid = paths.notna().sum(axis=1)
    rng = np.random.default_rng(0)
    lo, hi = np.full(len(x), np.nan), np.full(len(x), np.nan)
    for r, off in enumerate(x):
        d = paths.iloc[r].dropna().to_numpy()
        if len(d) >= 3:
            boots = d[rng.integers(0, len(d), size=(3000, len(d)))].mean(axis=1)
            lo[r], hi[r] = np.percentile(boots, [2.5, 97.5])
    ax.fill_between(x, lo, hi, color=BLUE, alpha=0.15, linewidth=0)
    ax.plot(x, mean, color=BLUE, lw=2.0, label=f"All events (n={valid.max()})")

    if "mechanical" in ev.columns and ev["mechanical"].notna().any():
        by_date = {pd.Timestamp(d): m for d, m in zip(ev["date"], ev["mechanical"])}
        mech_cols = [c for c in paths.columns if by_date.get(pd.Timestamp(c)) is True]
        gen_cols = [c for c in paths.columns if by_date.get(pd.Timestamp(c)) is False]
        if len(mech_cols) >= 3:
            ax.plot(
                x, paths[mech_cols].mean(axis=1), color=ORANGE, lw=1.6,
                label=f"Stale-fix widenings (n={len(mech_cols)})",
            )
        if len(gen_cols) >= 3:
            ax.plot(
                x, paths[gen_cols].mean(axis=1), color=AQUA, lw=1.6,
                label=f"Genuine widenings (n={len(gen_cols)})",
            )
    ax.axhline(0, color=BASELINE, lw=1.0)
    ax.axvline(0, color=BASELINE, lw=1.0, ls=":")
    _style(ax, "Average basis path around widening events (re-based to 0 at entry)", "bp vs entry")
    ax.set_xlabel("Business days from event", color=INK2, fontsize=9)
    _legend(ax)
    _save(fig, path)


def chart_horizon(curve: pd.DataFrame, h_star: int, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 4.2), facecolor=SURFACE)
    ax.fill_between(curve["h"], curve["ci_lo"], curve["ci_hi"], color=BLUE,
                    alpha=0.15, linewidth=0)
    ax.plot(curve["h"], curve["mean_bp"], color=BLUE, lw=2.0, label="Mean change (95% CI)")
    star = curve[curve["h"] == h_star]
    if not star.empty:
        ax.scatter(star["h"], star["mean_bp"], s=45, color=ORANGE, zorder=5,
                   label=f"h = {h_star}d (the proposition)")
    ax.axhline(0, color=BASELINE, lw=1.0)
    _style(ax, "Forward basis change after a widening event, by horizon", "bp")
    ax.set_xlabel("Horizon (business days)", color=INK2, fontsize=9)
    ax.set_xticks(curve["h"][::2])
    _legend(ax)
    _save(fig, path)


def chart_catchup(ev: pd.DataFrame, h_star: int, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 5.6), facecolor=SURFACE)
    col = f"d_basis_{h_star}d"
    sub = ev.dropna(subset=["pending_bp", col])
    mech_mask = sub["mechanical"].astype(bool)
    mech = sub[mech_mask]
    gen = sub[~mech_mask]
    ax.scatter(mech["pending_bp"], mech[col], s=34, color=ORANGE, alpha=0.85,
               label=f"Stale-fix (pending ≤ {MECH_THRESHOLD_BP:g}bp)")
    ax.scatter(gen["pending_bp"], gen[col], s=34, color=BLUE, alpha=0.85,
               label="Genuine widening")
    lim = sub[["pending_bp", col]].abs().to_numpy().max() * 1.1 + 1
    xs = np.array([-lim, lim])
    ax.plot(xs, xs, color=MUTED, lw=1.0, ls="--", label="Pure catch-up (y = x)")
    ax.axhline(0, color=BASELINE, lw=0.8)
    ax.axvline(0, color=BASELINE, lw=0.8)
    _style(ax, f"Owed catch-up at entry vs realised {h_star}d basis change",
           f"Realised {h_star}d basis change (bp)")
    ax.set_xlabel("Pending catch-up at entry (bp, walk-forward)", color=INK2, fontsize=9)
    _legend(ax)
    _save(fig, path)


def _fmt(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def _grid_table(grid: pd.DataFrame) -> str:
    hs = sorted(grid["h"].unique())
    lines = ["| z entry | " + " | ".join(f"h={h}d" for h in hs) + " |",
             "|---|" + "---|" * len(hs)]
    for z, g in grid.groupby("z_entry"):
        cells = []
        for h in hs:
            r = g[g["h"] == h].iloc[0]
            cells.append(
                f"{_fmt(r['mean_bp'], 1)}bp (n={int(r['n'])}, p={_fmt(r['p_permutation'], 3)})"
            )
        lines.append(f"| {z:g} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _split_line(name: str, g: dict) -> str:
    if g.get("n", 0) < 3 or "mean_bp" not in g:
        return f"- **{name}**: n={g.get('n', 0)} - too few to test."
    return (
        f"- **{name}** (n={g['n']}): mean {_fmt(g['mean_bp'], 1)}bp, "
        f"hit rate {g['hit_rate']:.0%}, t={_fmt(g['t_stat'])}, "
        f"permutation p={_fmt(g.get('p_permutation'), 3)}"
    )


def verdict(s: dict) -> str:
    """Plain-language reading of the headline stats."""
    if s.get("n_evaluable", 0) < 3:
        return "Not enough events to say anything."
    sig = s.get("p_permutation", 1) < 0.05 and s.get("mean_bp", 0) < 0
    lines = []
    if sig:
        lines.append(
            f"**The proposition holds in this sample**: after a widening event the "
            f"basis falls {_fmt(-s['mean_bp'], 1)}bp on average over {s['h_star']} days "
            f"(permutation p={_fmt(s['p_permutation'], 3)} vs random days, hit rate "
            f"{s['hit_rate']:.0%} vs {s['base_rate']:.0%} unconditionally), retracing a "
            f"median {s['median_retrace_frac']:.0%} of the excess over the rolling mean."
        )
    else:
        lines.append(
            f"**The proposition does NOT clear the bar in this sample**: mean "
            f"{s['h_star']}d change {_fmt(s['mean_bp'], 1)}bp, permutation "
            f"p={_fmt(s.get('p_permutation'), 3)}."
        )
    if "mean_pending_bp" in s:
        mech_share = (
            s["mean_pending_bp"] / s["mean_bp"] if s.get("mean_bp") else np.nan
        )
        beyond_sig = s.get("p_beyond_catchup", 1) < 0.05
        lines.append(
            f"Of the average move, {_fmt(s['mean_pending_bp'], 1)}bp was mechanically "
            f"owed fix catch-up at entry ({mech_share:.0%} of the realised move); the "
            f"move beyond catch-up averages {_fmt(s['mean_beyond_catchup_bp'], 1)}bp "
            f"(p={_fmt(s.get('p_beyond_catchup'), 3)})."
        )
        if beyond_sig and s.get("mean_beyond_catchup_bp", 0) < 0:
            lines.append(
                "So there is genuine premium mean-reversion beyond the sticky-fix "
                "artefact - the part a forward-settling FRA-vs-OIS trade could "
                "actually capture."
            )
        else:
            lines.append(
                "The plummet is mostly the sticky fix catching up with OIS moves that "
                "had already happened. A FRA-vs-OIS position settles on a FUTURE fix "
                "that already prices this catch-up, so the apparent edge is largely "
                "NOT tradeable."
            )
    return "\n\n".join(lines)


def write_report(
    out_dir: Path,
    meta: dict,
    fit: PassthroughFit,
    stats_level: dict,
    stats_widen: dict,
    stats_tighten: dict,
    curve: pd.DataFrame,
    grid: pd.DataFrame,
    strat: dict,
    ev: pd.DataFrame,
) -> Path:
    h = stats_level.get("h_star", H_STAR)
    md = [f"# Spot FRA–€STR basis: does a big widening plummet within {h} days?", ""]
    if meta.get("synthetic"):
        md += [
            "> **SYNTHETIC DATA.** This run uses the bundled simulated sample "
            "(`scripts/generate_sample_basis_data.py`) so the pipeline can be "
            "demonstrated end to end. Do not draw market conclusions - rerun "
            "with real exports (see README).",
            "",
        ]
    md += [
        f"Data: {meta['n_days']} overlapping business days, "
        f"{meta['start']} → {meta['end']}; OIS alignment: `{meta['ois_align']}`; "
        f"signal: z of basis {meta['signal']} ≥ {meta['z_entry']:g} "
        f"(rolling {meta['z_window']}d window, first crossings, "
        f"{meta['cooldown']}d cooldown).",
        "",
        "## How the two legs are made comparable",
        "",
        "The Euribor 3M fix and the 3M €STR OIS cover the same 3-month window, "
        "so rate expectations cancel in the difference - EXCEPT that the fix is "
        "sticky. A distributed-lag regression of daily fix changes on current and "
        "lagged OIS changes measures that stickiness:",
        "",
        f"- total pass-through: **{_fmt(fit.total_passthrough)}** "
        f"(1.0 = the fix eventually fully follows the OIS leg)",
        f"- mean pass-through lag: **{_fmt(fit.mean_lag_days, 1)} business days** "
        f"(R² {_fmt(fit.r2)}, {fit.nobs} obs)",
        "",
        "The tail of those weights gives each day's **pending catch-up**: the fix "
        "drift already owed to past OIS moves (fitted walk-forward, so it is "
        "backtest-legal). `basis + pending` is the catch-up-adjusted basis - the "
        "fair daily comparison of the two series - and pending at an event date "
        "predicts the mechanical part of the subsequent 'plummet'.",
        "",
        "![rates](rates.png)",
        "![basis](basis.png)",
        "",
        f"## Headline test ({meta['signal']} signal)",
        "",
        f"- events: {stats_level.get('n_events', 0)} "
        f"({stats_level.get('n_evaluable', 0)} with a full {h}d window)",
        f"- mean {h}d basis change: **{_fmt(stats_level.get('mean_bp'), 1)}bp** "
        f"(median {_fmt(stats_level.get('median_bp'), 1)}bp; unconditional mean "
        f"{_fmt(stats_level.get('unconditional_mean_bp'), 1)}bp)",
        f"- hit rate (basis lower after {h}d): "
        f"**{stats_level.get('hit_rate', float('nan')):.0%}** vs "
        f"{stats_level.get('base_rate', float('nan')):.0%} base rate",
        f"- t = {_fmt(stats_level.get('t_stat'))}, permutation p vs random days = "
        f"**{_fmt(stats_level.get('p_permutation'), 3)}**",
        f"- median retracement of the excess over the rolling mean: "
        f"{_fmt(stats_level.get('median_retrace_frac', float('nan')) * 100, 0)}%",
        "",
        "![event path](event_path.png)",
        "![horizon](horizon.png)",
        "",
        "## Mechanical catch-up vs genuine reversion",
        "",
        f"Split of the {h}d move (legs: Δbasis = Δfix − ΔOIS): "
        f"fix leg {_fmt(stats_level.get('mean_d_fix_bp'), 1)}bp, OIS leg "
        f"{_fmt(stats_level.get('mean_d_ois_bp'), 1)}bp.",
        "",
        f"Cross-sectional regression of the realised {h}d change on pending "
        f"catch-up at entry: slope {_fmt(stats_level.get('catchup_slope'))} "
        f"(pure catch-up = 1), r = {_fmt(stats_level.get('catchup_r'))}. Mean move "
        f"beyond catch-up: {_fmt(stats_level.get('mean_beyond_catchup_bp'), 1)}bp "
        f"(p = {_fmt(stats_level.get('p_beyond_catchup'), 3)}).",
        "",
        _split_line("Stale-fix widenings (pending ≤ "
                    f"{MECH_THRESHOLD_BP:g}bp)", stats_level.get("mechanical", {})),
        _split_line("Genuine widenings", stats_level.get("genuine", {})),
        "",
        "![catch-up scatter](catchup_scatter.png)",
        "",
        "## Robustness",
        "",
        "Mean forward change (permutation p) across thresholds and horizons:",
        "",
        _grid_table(grid),
        "",
        f"**Widening-speed signal** (z of {meta['widen_days']}d change ≥ "
        f"{meta['z_entry']:g}): n={stats_widen.get('n_evaluable', 0)}, mean "
        f"{_fmt(stats_widen.get('mean_bp'), 1)}bp, hit "
        f"{stats_widen.get('hit_rate', float('nan')):.0%}, permutation "
        f"p={_fmt(stats_widen.get('p_permutation'), 3)}.",
        "",
        f"**Asymmetry check** - big TIGHTENINGS (z ≤ −{meta['z_entry']:g}): "
        f"n={stats_tighten.get('n_evaluable', 0)}, mean {h}d change "
        f"{_fmt(stats_tighten.get('mean_bp'), 1)}bp "
        f"(p={_fmt(stats_tighten.get('p_permutation'), 3)}). If widenings plummet "
        "AND tightenings bounce, the basis simply mean-reverts from both sides; "
        "a widening-only effect is more specific to the proposition.",
        "",
        "## Naive sell-the-basis P&L",
        "",
        f"Sell the basis at every event, hold {h} days, "
        f"{meta['cost_bp']:g}bp round-trip cost: "
        f"n={strat.get('n_trades', 0)}, total {_fmt(strat.get('total_bp'), 1)}bp, "
        f"mean {_fmt(strat.get('mean_bp'), 1)}bp/trade, hit "
        f"{strat.get('hit_rate', float('nan')):.0%}, t={_fmt(strat.get('t_stat'))}, "
        f"worst {_fmt(strat.get('worst_bp'), 1)}bp, max drawdown "
        f"{_fmt(strat.get('max_drawdown_bp'), 1)}bp.",
        "",
        "**Tradability caveat, stated plainly:** the spot basis is an accounting "
        "quantity, not an instrument. The tradeable expression (front FRA or "
        "Euribor future vs €STR OIS/futures) settles on a *future* fix, and the "
        "market prices predictable fix catch-up into it. Only the *genuine* "
        "bucket's reversion should be treated as capturable edge, and even that "
        "before bid/offer in the basis market.",
        "",
        "## Verdict",
        "",
        verdict(stats_level),
        "",
    ]
    out = out_dir / "report.md"
    out.write_text("\n".join(md))
    ev.to_csv(out_dir / "events.csv", index=False)
    return out

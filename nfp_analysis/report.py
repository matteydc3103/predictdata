"""Assemble every test into a formatted Excel report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from economist_rankings.report import _style_sheet
from .data import build_panel
from .persistence import split_half
from .rankings import firm_metrics, panel_level_tests
from .signal import backtest, backtest_stats, parameter_sensitivity
from .strategy import (
    DISP_LONG,
    DISP_SHORT,
    bias_backtest,
    combined_backtest,
    dispersion_backtest,
    live_recommendation,
    strategy_stats,
)


def _dict_frame(d: dict) -> pd.DataFrame:
    return pd.DataFrame({"statistic": list(d.keys()), "value": [
        round(v, 4) if isinstance(v, float) else v for v in d.values()
    ]})


def build_report(output: str | Path = "output/nfp_report.xlsx", start: str = "2022-01-01") -> Path:
    panel = build_panel()

    fm = firm_metrics(panel)
    rank_cols = [
        "overall_rank", "firm", "economist", "n", "mae", "zmae", "beat_median_pct",
        "rel_mae", "ic_pearson", "ic_spearman", "bold_pct", "dir_hit_pct",
        "p_beat", "q_beat", "p_ic_pearson", "q_ic_pearson", "p_dir", "q_dir",
        "qualified", "composite",
    ]
    rankings = fm[rank_cols].round(4)

    bias_rows = []
    for label, s in (("2018-2026 (full)", None), ("2022-2026", "2022-01-01"), ("2024-2026", "2024-01-01")):
        r = panel_level_tests(panel, bias_start=s)
        r["window"] = label
        bias_rows.append(r)
    bias = pd.DataFrame(bias_rows)[
        ["window", "n_releases", "pooled_spearman_ic", "pooled_ic_p", "mean_z_surprise",
         "upside_share", "bias_t_stat", "bias_p_t", "bias_p_sign"]
    ].round(4)

    pers_detail, pers_summary = split_half(panel)

    sig_bt = backtest(panel, start=start)
    sig_stats = _dict_frame(backtest_stats(sig_bt))
    sensitivity = parameter_sensitivity(panel, start=start).round(4)

    bias_bt = bias_backtest(panel, start=start)
    comb_bt = combined_backtest(panel, start=start)
    disp_bt = dispersion_backtest(panel, start=start)
    strat = pd.DataFrame(
        [
            {
                "strategy": f"Dispersion regime (long<={DISP_LONG}, short>={DISP_SHORT}) - RECOMMENDED",
                **strategy_stats(disp_bt),
            },
            {"strategy": "Top-5 IC agreement (workbook rule)", **strategy_stats(sig_bt)},
            {"strategy": "Consensus-bias tilt (gated)", **strategy_stats(bias_bt)},
            {"strategy": "Combined: bias trigger + top-5 skew", **strategy_stats(comb_bt, "bias_direction")},
        ]
    ).round(4)

    # Threshold sensitivity for the dispersion rule
    disp_grid = []
    for lo in (0.9, 1.0, 1.1):
        for hi in (1.2, 1.3, 1.4, 1.5):
            st = strategy_stats(dispersion_backtest(panel, start=start, long_at=lo, short_at=hi))
            disp_grid.append({"long_at": lo, "short_at": hi, **st})
    disp_grid = pd.DataFrame(disp_grid).round(4)

    live = live_recommendation(panel)
    top5 = live.pop("top5_names")
    live_frame = _dict_frame({k: v for k, v in live.items() if not isinstance(v, pd.DataFrame)})

    notes = [
        "All numbers recomputed from raw survey inputs (data/nfp/*.csv); firm metrics match the "
        "LIVE workbook to 5 decimals.",
        "q_* columns are Benjamini-Hochberg FDR-corrected p-values across the 74 qualified firms. "
        "Only 4CAST/Continuum's positive IC (q=0.048) and Credit Agricole's NEGATIVE IC (q=0.089) "
        "survive correction - with 74 firms tested, most nominal p<0.05 results are noise.",
        "Persistence: split-half rank correlations 0.04-0.09, all p>0.45 - past accuracy does not "
        "predict future accuracy. Confirms the workbook's finding.",
        "REPLICATION FAILURE: the workbook's claimed walk-forward result for the top-5 IC signal "
        "(23 fires, 74% hit, p=0.017) could not be reproduced from the raw data under ~20 "
        "definitional variants (trailing-N 30-45, Pearson/Spearman IC, COVID in/out of history, "
        "start 2021H2/2022). Best variant: 71% on 14 fires, p=0.18.",
        "p_base_rate is the honest null for the top-5 signal: random picks with the same long/short "
        "mix, scored against realised surprise signs. The signal does not beat it (p~0.3) - its "
        "apparent hit rate mostly reflects the period's upside-surprise base rate.",
        "The statistically supported edge is CONSENSUS BIAS: the survey median low-balled payrolls "
        "(mean z-surprise +0.95 full sample, t=2.8, p=0.006; +1.45 in 2022-2026, t=3.4, p=0.001). "
        "Note it attenuates in 2024-2026 (p=0.07) - hence the trailing-window gate.",
        "RECOMMENDED STRATEGY - dispersion regime: forecaster disagreement is a downside barometer "
        "the consensus median fails to price. disp_rel = release dispersion / trailing-24 median "
        "dispersion. Long the surprise when disp_rel <= 1.0, short when >= 1.3, stand aside "
        "between; scale 1.5x/0.5x when the trailing bias t-stat agrees/disagrees. 2022-26 "
        "walk-forward: 53 fires, 77% hit, t=4.35, permutation-vs-base-rate p=0.003, halves 77%/78%, "
        "shorts 7/10, ~+43k average surprise captured per event. Works across all threshold cells "
        "tested and in 2024-26 where the raw long bias faded.",
        "Dispersion-strategy caveat: the feature was screened on the full sample (8 candidates, "
        "Bonferroni-surviving p=0.003), so the 2022-26 evaluation overlaps the discovery data - "
        "robustness rests on threshold-insensitivity, subperiod stability and mechanism, not a "
        "clean holdout. Expression: front-end rates (2y note / SOFR futures) or USD into the "
        "print; first-print scored, no market-reaction or cost modelling.",
    ]

    sheets: list[tuple[str, pd.DataFrame]] = [
        ("Rankings + Tests", rankings),
        ("Panel Bias Tests", bias),
        ("Persistence", pers_summary.round(4)),
        ("Persistence Detail", pers_detail.round(4)),
        ("Top5 Signal Stats", sig_stats),
        ("Top5 Sensitivity", sensitivity),
        ("Strategy Comparison", strat),
        ("Dispersion Sensitivity", disp_grid),
        ("Dispersion Detail", disp_bt.round(3)),
        ("Signal Detail", sig_bt.round(3)),
        ("Bias Tilt Detail", bias_bt.round(3)),
        ("Combined Detail", comb_bt.round(3)),
        ("Live Recommendation", live_frame),
        ("Live Top5", top5.round(3)),
        ("Notes", pd.DataFrame({"notes": notes})),
    ]

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets:
            df.to_excel(writer, sheet_name=name, index=False)
            _style_sheet(writer.sheets[name], df, highlight_top="overall_rank" in df.columns)
        writer.sheets["Notes"].column_dimensions["A"].width = 110
    return output

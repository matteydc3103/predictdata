"""Pin the recomputed metrics to values read directly from the LIVE workbook.

If a refactor changes any of these, the pipeline no longer reproduces the
source-of-truth spreadsheet.
"""

import numpy as np
import pytest

from nfp_analysis.data import build_panel
from nfp_analysis.rankings import firm_metrics

# firm prefix -> (n, mae, zmae, beat%, ic_pearson, bold%, dir_hit%, rel_mae)
WORKBOOK_ROWS = {
    "4CAST/Continuum": (79, 92.7342, 2.49103, 0.53797, 0.37570, 0.81013, 0.6875, 0.90775),
    "BofA Securities": (48, 120.2917, 2.51742, 0.61458, 0.13075, 0.85417, 0.65854, 0.94393),
    "Scotiabank": (91, 96.3077, 2.53951, 0.56044, 0.14502, 0.87912, 0.65000, 1.01700),
    "Southbay": (91, 96.6484, 2.55516, 0.52198, 0.20785, 0.90110, 0.62195, 1.01448),
}


@pytest.fixture(scope="module")
def metrics():
    return firm_metrics(build_panel())


@pytest.mark.parametrize("prefix", WORKBOOK_ROWS)
def test_firm_metrics_match_workbook(metrics, prefix):
    n, mae, zmae, beat, ic, bold, dirhit, rel = WORKBOOK_ROWS[prefix]
    row = metrics[metrics["firm"].str.startswith(prefix)].iloc[0]
    assert row["n"] == n
    assert np.isclose(row["mae"], mae, atol=1e-3)
    assert np.isclose(row["zmae"], zmae, atol=1e-4)
    assert np.isclose(row["beat_median_pct"], beat, atol=1e-4)
    assert np.isclose(row["ic_pearson"], ic, atol=1e-4)
    assert np.isclose(row["bold_pct"], bold, atol=1e-4)
    assert np.isclose(row["dir_hit_pct"], dirhit, atol=1e-4)
    assert np.isclose(row["rel_mae"], rel, atol=1e-4)


def test_release_level_values():
    panel = build_panel()
    jan18 = panel[panel["period"] == "Jan-18"].iloc[0]
    assert jan18["consensus"] == 180.0
    assert np.isclose(jan18["dispersion"], 22.239)
    # dispersion floor binds when 1.4826 * MAD < 15
    may18 = panel[panel["period"] == "May-18"].iloc[0]
    assert may18["dispersion"] == 15.0


def test_covid_exclusion():
    panel = build_panel()
    assert panel["period"].nunique() == 92  # 102 releases minus 10 COVID months
    assert not panel["period"].str.match(r"(Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-20$").any()


def test_qualified_universe_matches_workbook():
    fm = firm_metrics(build_panel())
    assert int(fm["qualified"].sum()) == 74
    assert fm.iloc[0]["firm"].startswith("4CAST")  # workbook overall rank 1

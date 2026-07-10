"""Statistical tests and trade signal for the Bloomberg NFP forecaster panel.

Recomputes, from the raw survey inputs, everything the LIVE workbook
derives with formulas - per-release consensus and dispersion, per-firm
skill metrics, the split-half persistence test - and adds formal
significance testing plus a walk-forward backtest of the Top-5 IC
agreement trade signal.
"""

__version__ = "0.1.0"

"""Core SDR parsing/compute logic, shared by the local blotter and the
BQuant notebook (which embeds a copy so it stays a single uploadable file)."""
import numpy as np
import pandas as pd

# ---------------- tenor ----------------
# Standard tenor grid: label -> years. Business-day adjustment (modified
# following) and T+2 effective dates mean the raw day count is rarely an exact
# round number, so we snap to the nearest grid point within a tolerance that
# scales with maturity.
_TENOR_GRID = (
    [(f"{m}m", m / 12.0) for m in (1, 2, 3, 4, 6, 9, 18)]
    + [(f"{y}y", float(y)) for y in list(range(1, 16)) + [20, 25, 30, 35, 40, 45, 50]]
)


def year_frac(effective, maturity):
    """ACT/365.25 year fraction between two timestamps."""
    return (maturity - effective).days / 365.25


def snap_tenor(yf):
    """Snap a raw year fraction to the standard tenor grid.

    Tolerance = max(18 calendar days, 1.5% of the tenor) so that holiday /
    modified-following adjusted dates (e.g. 10y printed as 10y + 3 business
    days) still label as the standard tenor, while genuinely broken dates
    fall through to a decimal label like '9.71y'.
    """
    if not np.isfinite(yf) or yf <= 0:
        return None
    label, gy = min(_TENOR_GRID, key=lambda t: abs(yf - t[1]))
    tol = max(0.05, 0.015 * gy)
    return label if abs(yf - gy) <= tol else f"{yf:.2f}y"


# ---------------- notional ----------------
def parse_notional(x):
    """Return (amount_in_currency_units, capped_flag).

    Handles 150000000, '150,000,000', '250MM+', '1.5BN', '750k'.
    SDR caps large notionals and appends '+' - keep that flag.
    """
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return (float(x), False) if np.isfinite(x) else (np.nan, False)
    s = str(x).strip().upper().replace(",", "").replace("€", "").replace("$", "").replace("£", "")
    if not s:
        return np.nan, False
    capped = s.endswith("+")
    s = s.rstrip("+").strip()
    mult = 1.0
    for suf, m in (("BN", 1e9), ("MM", 1e6), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if s.endswith(suf):
            mult, s = m, s[: -len(suf)]
            break
    try:
        return float(s) * mult, capped
    except ValueError:
        return np.nan, capped


# ---------------- dv01 ----------------
def approx_dv01(notional, rate_pct, tenor_years, fwd_years=0.0):
    """Par-swap DV01 approximation: notional x annuity x 1bp.

    Annuity = (1 - (1+r)^-T)/r with the traded fixed rate as a flat discount
    proxy; forward-starting swaps get discounted by (1+r)^-t_fwd. Good to a
    few % of a proper curve-based DV01 - fine for ranking trade sizes.
    """
    if not (np.isfinite(notional) and np.isfinite(tenor_years)) or tenor_years <= 0:
        return np.nan
    r = (rate_pct or 0.0) / 100.0
    if not np.isfinite(r):
        r = 0.0
    ann = tenor_years if abs(r) < 1e-6 else (1.0 - (1.0 + r) ** -tenor_years) / r
    disc = (1.0 + r) ** -max(fwd_years, 0.0) if r > -1 else 1.0
    return notional * ann * disc * 1e-4


# ---------------- column normalisation ----------------
# SDR column headers differ between the SDR <GO> tabs, CSV exports and feeds.
# Map whatever arrives onto canonical names; first synonym found wins.
_COL_SYNONYMS = {
    "time":      ["trade time", "execution timestamp", "execution time", "exec time",
                  "timestamp", "time"],
    "effective": ["effective date", "effective", "start date", "eff date", "eff"],
    "maturity":  ["expiration date", "maturity date", "end date", "maturity",
                  "expiration", "expiry", "mat date"],
    "currency":  ["currency", "curr", "ccy", "notional currency", "notional currency 1"],
    "rate":      ["rate", "fixed rate", "fixed rate 1", "price", "strike", "coupon"],
    "notional":  ["notional", "notional amount", "notional amount 1", "notional 1", "amount"],
    "index":     ["index", "underlying", "floating rate index", "leg 2 index",
                  "reference rate", "underlier id", "und"],
    "platform":  ["platform", "sef", "exec venue", "execution venue", "venue",
                  "dissemination venue", "source", "dissem"],
    "product":   ["product", "type", "taxonomy", "asset class", "contract type",
                  "instrument"],
}

# Vanilla tab = fixed-float IRS (incl. ESTR OIS). Everything else out.
_PRODUCT_EXCLUDE = ("SWAPTION", "CAP", "FLOOR", "FRA", "XCCY", "CROSS",
                    "BASIS", "INFLATION", "ZC", "EXOTIC", "CDS")


def normalize_columns(raw):
    """Rename incoming columns to canonical names; leave unknown columns as-is."""
    df = raw.copy()
    lower = {str(c).strip().lower(): c for c in df.columns}
    ren = {}
    for canon, syns in _COL_SYNONYMS.items():
        for s in syns:
            if s in lower and lower[s] not in ren:
                ren[lower[s]] = canon
                break
    return df.rename(columns=ren)


# ---------------- pipeline ----------------
def build_table(raw, ccy="EUR", exclude_platforms=("TWSF", "TREU", "BBSF"),
                now=None):
    """raw trades DataFrame -> display table.

    Steps: normalise columns -> vanilla product filter -> currency filter ->
    platform exclusion -> parse American-format dates -> tenor / DV01 ->
    sort by time (newest first) -> tag same-timestamp trades as related
    packages (P1, P2, ...).
    """
    df = normalize_columns(raw)
    required = ["time", "effective", "maturity", "currency", "rate", "notional"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"SDR data is missing columns {missing}; got {list(raw.columns)}")

    for opt in ("index", "platform", "product"):
        if opt not in df.columns:
            df[opt] = ""

    # product filter (the Vanilla tab is already vanilla-only; this guards feeds
    # that deliver the whole Rates asset class)
    prod = df["product"].astype(str).str.upper()
    df = df[~prod.str.contains("|".join(_PRODUCT_EXCLUDE), na=False)]

    # currency + platform filters
    df = df[df["currency"].astype(str).str.strip().str.upper() == ccy.upper()]
    excl = {p.strip().upper() for p in exclude_platforms}
    df = df[~df["platform"].astype(str).str.strip().str.upper().isin(excl)]
    if df.empty:
        return pd.DataFrame(columns=["tenor", "rate", "notional", "dv01",
                                     "index", "time", "platform", "related"])

    # American-format dates (MM/DD/YYYY), tolerant of ISO too
    for c in ("effective", "maturity"):
        df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=False)
    df["time"] = pd.to_datetime(df["time"], errors="coerce", dayfirst=False)
    df = df.dropna(subset=["time", "effective", "maturity"])

    df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
    parsed = df["notional"].map(parse_notional)
    df["notional_val"] = [p[0] for p in parsed]
    df["capped"] = [p[1] for p in parsed]

    now = now or pd.Timestamp.now()
    df["yf"] = [year_frac(e, m) for e, m in zip(df["effective"], df["maturity"])]
    df["fwd_years"] = ((df["effective"] - now).dt.days / 365.25).clip(lower=0.0)
    df["tenor"] = df["yf"].map(snap_tenor)
    df["dv01"] = [approx_dv01(n, r, t, f) for n, r, t, f in
                  zip(df["notional_val"], df["rate"], df["yf"], df["fwd_years"])]

    df = df.sort_values("time", ascending=False, kind="mergesort")

    # trades sharing an exact execution timestamp are related (package trades)
    sizes = df.groupby("time")["time"].transform("size")
    pkg_times = df.loc[sizes > 1, "time"].drop_duplicates().sort_values(ascending=False)
    pkg_id = {t: f"P{i + 1}" for i, t in enumerate(pkg_times)}
    df["related"] = df["time"].map(pkg_id).fillna("")

    return df[["tenor", "rate", "notional_val", "capped", "dv01", "index",
               "time", "platform", "related"]].rename(columns={"notional_val": "notional"})


def demo_trades(n_prints=40, seed=None):
    """Synthetic SDR-shaped prints (EUR-heavy, some packages, all venues)."""
    rng = np.random.default_rng(seed)
    now = pd.Timestamp.now().floor("s")
    curve = {"2y": 2.05, "3y": 2.10, "5y": 2.22, "7y": 2.35, "10y": 2.51,
             "15y": 2.65, "20y": 2.69, "30y": 2.60}
    platforms = ["DWSF", "TPSF", "ICAP", "CMSF", "OFF", "TWSF", "TREU", "BBSF"]
    rows, t = [], now
    for _ in range(n_prints):
        t = t - pd.Timedelta(seconds=int(rng.integers(20, 400)))
        legs = [rng.choice(list(curve))]
        if rng.random() < 0.25:                 # package: 2-3 legs, same stamp
            legs += list(rng.choice(list(curve), size=int(rng.integers(1, 3))))
        # one venue / one currency per print - package legs stay together
        ccy = rng.choice(["EUR"] * 8 + ["USD", "GBP"])
        venue = rng.choice(platforms)
        for tenor in legs:
            yrs = int(tenor[:-1])
            eff = (now + pd.Timedelta(days=2)).normalize()
            mat = eff + pd.Timedelta(days=round(yrs * 365.25) + int(rng.integers(0, 4)))
            notional = float(rng.choice([25, 50, 75, 100, 150, 250])) * 1e6
            rows.append({
                "Trade Time": t.strftime("%m/%d/%Y %H:%M:%S"),
                "Effective Date": eff.strftime("%m/%d/%Y"),
                "Expiration Date": mat.strftime("%m/%d/%Y"),
                "Curr": ccy,
                "Fixed Rate": round(curve[tenor] + rng.normal(0, 0.02)
                                    + (1.4 if ccy == "USD" else 0), 4),
                "Notional Amount 1": f"{notional:,.0f}" + ("+" if notional >= 250e6 else ""),
                "Index": rng.choice(["ESTR"] * 7 + ["EURIBOR 6M"] * 3) if ccy == "EUR"
                         else ("SOFR" if ccy == "USD" else "SONIA"),
                "SEF": venue,
                "Product": "IRSwap:FixedFloat",
            })
    return pd.DataFrame(rows)


def read_sdr_export(path):
    """Read an SDR <GO> export in whatever format the terminal produced.

    Bloomberg grid exports (C:/blp/data/grid.*) can be CSV/TXT or Excel;
    both may carry title/timestamp preamble rows above the real header.
    """
    from pathlib import Path
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".xls", ".xlsx", ".xlsm"):
        try:
            probe = pd.read_excel(path, header=None, nrows=30)
        except ImportError:
            raise RuntimeError(
                "reading Excel exports needs an engine: pip install openpyxl "
                "(for .xlsx) or xlrd (for .xls)")
        for i, row in probe.iterrows():
            if _looks_like_sdr(row.dropna().tolist()):
                return pd.read_excel(path, skiprows=i)
        raise ValueError(
            f"could not find an SDR header row in '{path}' - expected columns "
            "like 'Trade Time' / 'Effective Date' / 'Expiration Date'")
    if suf == ".wk1":
        raise ValueError(
            f"'{path}' is a Lotus WK1 file - in the SDR export dialog choose "
            "CSV or Excel output instead")
    return read_sdr_csv(path)


def read_sdr_csv(path):
    """Read an SDR <GO> export, tolerating preamble rows and odd delimiters.

    Bloomberg exports sometimes carry title/timestamp lines above the real
    header; scan the first rows for one containing recognisable SDR columns
    and re-read from there. Delimiter is sniffed (comma / tab / semicolon).
    """
    try:
        df = pd.read_csv(path, sep=None, engine="python")
        if _looks_like_sdr(df.columns):
            return df
    except Exception:
        pass  # ragged preamble rows - fall through to the line scan
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        head = [fh.readline() for _ in range(30)]
    for i, line in enumerate(head):
        for sep in (",", "\t", ";"):
            if _looks_like_sdr(line.split(sep)):
                return pd.read_csv(path, sep=sep, skiprows=i)
    raise ValueError(
        f"could not find an SDR header row in '{path}' - expected columns "
        "like 'Trade Time' / 'Effective Date' / 'Expiration Date'")


def _looks_like_sdr(cols):
    low = {str(c).strip().lower() for c in cols}
    hits = sum(any(s in low for s in syns) for syns in
               (_COL_SYNONYMS["time"], _COL_SYNONYMS["effective"],
                _COL_SYNONYMS["maturity"], _COL_SYNONYMS["notional"]))
    return hits >= 3


def format_table(df):
    """Numeric table -> display strings for the grid."""
    out = pd.DataFrame(index=df.index)
    out["tenor"] = df["tenor"]
    out["rate"] = df["rate"].map(lambda r: f"{r:.4f}" if pd.notna(r) else "")
    out["notional"] = [
        (f"{n / 1e6:,.0f}mm" if n >= 1e6 else f"{n:,.0f}") + ("+" if c else "")
        if pd.notna(n) else ""
        for n, c in zip(df["notional"], df["capped"])
    ]
    out["dv01"] = df["dv01"].map(lambda d: f"{d:,.0f}" if pd.notna(d) else "")
    out["index"] = df["index"]
    same_day = df["time"].dt.normalize().nunique() <= 1
    fmt = "%H:%M:%S" if same_day else "%m/%d %H:%M:%S"
    out["time"] = df["time"].dt.strftime(fmt)
    out["platform"] = df["platform"]
    out["related"] = df["related"]
    return out.reset_index(drop=True)

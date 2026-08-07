"""Core SDR parsing/compute logic, shared by the local blotter and the
BQuant notebook (which embeds a copy so it stays a single uploadable file)."""
import re

import numpy as np
import pandas as pd

__version__ = "5"

# venue codes -> display names (applied after the exclusion filter,
# which always works on the raw codes)
DEFAULT_PLATFORM_RENAME = {"BGCD": "BGC", "TPSE": "TP/ICAP",
                           "TSEF": "TRADS", "GSEF": "GFI"}
DEFAULT_EXCLUDE_PLATFORMS = ("TWSF", "TREU", "BBSF", "BMTF")

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
    "rate":      ["rate", "fixed rate", "fixed rate 1", "rate 1", "rate1",
                  "price", "strike", "coupon"],
    "rate2":     ["rate 2", "rate2", "fixed rate 2", "spread"],
    "notional":  ["notional", "notional amount", "notional amount 1", "notional 1",
                  "not.", "not", "amount", "amt"],
    "index":     ["index", "underlying", "floating rate index", "leg 2 index",
                  "reference rate", "underlier id", "und"],
    "platform":  ["platform", "platform id", "sef", "exec venue", "execution venue",
                  "venue", "dissemination venue", "source", "dissem"],
    "product":   ["product", "type", "taxonomy", "asset class", "contract type",
                  "instrument"],
    "dv01":      ["dv01", "dv01 (usd)", "dv01(usd)", "risk"],
    "leg2":      ["leg 2", "leg2", "underlier id 2"],
}

def _pretty_index(code, leg2=""):
    """Readable floating-index label: EUR006M -> 'EURIBOR 6M'; fall back to
    the Leg 2 description when the index code cell is empty."""
    s = "" if code is None or (isinstance(code, float) and np.isnan(code)) \
        else str(code).strip().upper()
    m = re.fullmatch(r"EUR(\d{3})M", s)
    if m:
        return f"EURIBOR {int(m.group(1))}M"
    if "ESTR" in s or "EUROSTR" in s:
        return "ESTR"
    if s and s != "NAN":
        return s
    l2 = str(leg2).upper()
    if "EURIBOR" in l2:
        return "EURIBOR"
    if "STR" in l2:
        return "ESTR"
    return ""


def _rate_decimals(r):
    """Decimal places a rate was quoted to (fp noise stripped)."""
    if r is None or not np.isfinite(r):
        return 0
    s = f"{r:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _gadget_note(rate, yf):
    """Rates printed to 5dp are gadget prints: 5Y bucket up to 6y, else 10Y."""
    if _rate_decimals(rate) >= 5:
        return "5Y GADGET" if (np.isfinite(yf) and yf <= 6.05) else "10Y GADGET"
    return ""


def _index_months(label):
    """EURIBOR 6M -> 6, ESTR -> 0 (shortest), unknown -> -1."""
    s = str(label).upper()
    m = re.search(r"(\d+)\s*M\b", s)
    if m:
        return int(m.group(1))
    if "ESTR" in s:
        return 0
    return -1


_BASIS_NAMES = {(1, 3): "3s1s", (3, 6): "3s6s", (6, 12): "6s12s"}


def _basis_name(lo, hi):
    if lo >= 0 and hi > 0:
        return _BASIS_NAMES.get((lo, hi), f"{lo}s{hi}s")
    return ""


def _curve_name(labels):
    """['10y','30y'] -> '10s30s'; ['7y','8y','9y'] -> '7s8s9s'.
    Non-integer-year tenors fall back to a '/'-joined label."""
    nums = []
    for lab in labels:
        m = re.fullmatch(r"(\d+)y", str(lab))
        if not m:
            return "/".join(str(x) for x in labels)
        nums.append(m.group(1))
    return "".join(f"{n}s" for n in nums)


def _combine_group(recs):
    """Same-timestamp legs -> one reported trade, or None if unclassifiable.

    2 legs, same tenor + same notional: different index -> index basis
    (level = higher index rate - lower); same index -> eurex/lch CCP basis
    (level = |rate difference|). 2 legs, different tenors -> curve trade
    (level = long - short, reported size/dv01 of the longer leg). 3 legs
    with distinct tenors -> fly (level = 2*belly - wings, belly size/dv01).
    """
    recs = sorted(recs, key=lambda r: r["yf"] if np.isfinite(r["yf"]) else 0.0)
    tenors = [r["tenor"] for r in recs]
    if len(recs) == 2:
        a, b = recs
        same_tenor = tenors[0] == tenors[1]
        same_ntl = (np.isfinite(a["notional_val"]) and np.isfinite(b["notional_val"])
                    and np.isclose(a["notional_val"], b["notional_val"]))
        if same_tenor and same_ntl:
            if a["index"] != b["index"]:
                lo, hi = sorted((a, b), key=lambda r: _index_months(r["index"]))
                name = _basis_name(_index_months(lo["index"]),
                                   _index_months(hi["index"]))
                return {**a, "rate": hi["rate"] - lo["rate"], "is_spread": True,
                        "index": f"{lo['index']} / {hi['index']}",
                        "note": f"{name} basis".strip()}
            return {**a, "rate": abs(a["rate"] - b["rate"]), "is_spread": True,
                    "note": "eurex/lch"}
        if not same_tenor:
            s, l = recs
            return {**l, "tenor": _curve_name(tenors),
                    "rate": l["rate"] - s["rate"], "is_spread": True,
                    "note": "curve"}
        return None
    if len(recs) == 3 and len(set(tenors)) == 3:
        s, m, l = recs
        return {**m, "tenor": _curve_name(tenors),
                "rate": 2.0 * m["rate"] - s["rate"] - l["rate"],
                "is_spread": True, "note": "fly"}
    return None


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
def build_table(raw, ccy="EUR", exclude_platforms=DEFAULT_EXCLUDE_PLATFORMS,
                now=None, platform_rename=None):
    """raw trades DataFrame -> display table.

    Steps: normalise columns -> vanilla product filter -> currency filter ->
    platform exclusion + rename -> parse American-format dates -> tenor /
    DV01 -> collapse same-timestamp legs into basis / eurex-lch / curve /
    fly rows -> gadget notes on 5dp prints -> sort by time (newest first).
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
    ren = DEFAULT_PLATFORM_RENAME if platform_rename is None else platform_rename
    df["platform"] = df["platform"].astype(str).str.strip().map(
        lambda p: ren.get(p.upper(), p))
    if df.empty:
        return pd.DataFrame(columns=["tenor", "rate", "is_spread", "notional",
                                     "capped", "dv01", "index", "time",
                                     "platform", "note"])

    # American-format dates (MM/DD/YYYY), tolerant of ISO and Excel serials
    for c in ("effective", "maturity", "time"):
        df[c] = _to_datetime_smart(df[c])
    df = df.dropna(subset=["time", "effective", "maturity"])

    # the fixed rate sits in whichever leg is fixed - coalesce Rate 1 / Rate 2
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
    if "rate2" in df.columns:
        df["rate"] = df["rate"].fillna(pd.to_numeric(df["rate2"], errors="coerce"))

    parsed = df["notional"].map(parse_notional)
    df["notional_val"] = [p[0] for p in parsed]
    df["capped"] = [p[1] for p in parsed]

    # some grid layouts put the capped marker in its own (unnamed) column of '+'
    for c in df.columns:
        if c in _COL_SYNONYMS or c in ("notional_val", "capped"):
            continue
        vals = df[c].dropna().astype(str).str.strip()
        vals = vals[vals != ""]
        if len(vals) and (vals == "+").all():
            df["capped"] = df["capped"] | (df[c].astype(str).str.strip() == "+")

    # grids may quote notional in millions ('150') or thousands ('150,000');
    # a real swap-blotter median below these cutoffs is implausible
    med = df["notional_val"].median()
    if pd.notna(med) and med > 0:
        if med < 10_000:
            df["notional_val"] = df["notional_val"] * 1e6
        elif med < 1_000_000:
            df["notional_val"] = df["notional_val"] * 1e3

    now = now or pd.Timestamp.now()
    df["yf"] = [year_frac(e, m) for e, m in zip(df["effective"], df["maturity"])]
    df["fwd_years"] = ((df["effective"] - now).dt.days / 365.25).clip(lower=0.0)
    df["tenor"] = df["yf"].map(snap_tenor)

    # prefer the feed's own DV01 where present; approximate the gaps
    approx = pd.Series([approx_dv01(n, r, t, f) for n, r, t, f in
                        zip(df["notional_val"], df["rate"], df["yf"],
                            df["fwd_years"])], index=df.index)
    if "dv01" in df.columns:
        feed = pd.to_numeric(
            df["dv01"].astype(str).str.replace(",", "", regex=False),
            errors="coerce").abs()
        df["dv01"] = feed.where(feed.notna(), approx)
    else:
        df["dv01"] = approx

    leg2 = df["leg2"] if "leg2" in df.columns else pd.Series("", index=df.index)
    df["index"] = [_pretty_index(i, l) for i, l in zip(df["index"], leg2)]

    df = df.sort_values("time", ascending=False, kind="mergesort")

    # same execution timestamp = one risk transfer: collapse to a single row
    # (basis / eurex-lch / curve / fly); unclassifiable groups keep P-tags
    rows, pkg_n = [], 0
    for _, g in df.groupby("time", sort=False):
        recs = g.to_dict("records")
        combined = _combine_group(recs) if 2 <= len(recs) <= 3 else None
        if combined is not None:
            rows.append(combined)
        elif len(recs) == 1:
            r = recs[0]
            r["note"] = _gadget_note(r["rate"], r["yf"])
            r["is_spread"] = False
            rows.append(r)
        else:
            pkg_n += 1
            for r in recs:
                r["note"] = f"P{pkg_n}"
                r["is_spread"] = False
                rows.append(r)

    out = pd.DataFrame(rows).sort_values("time", ascending=False,
                                         kind="mergesort")
    return out[["tenor", "rate", "is_spread", "notional_val", "capped", "dv01",
                "index", "time", "platform", "note"]].rename(
                    columns={"notional_val": "notional"})


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

    Bloomberg grid exports (C:/blp/data/grid.*) can be CSV/TXT, Excel, a zip
    container wrapping either, or plain text/HTML wearing an Excel extension.
    All variants may carry title/timestamp preamble rows above the header.
    """
    from pathlib import Path
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".wk1":
        raise ValueError(
            f"'{path}' is a Lotus WK1 file - in the SDR export dialog choose "
            "CSV or Excel output instead")
    if suf in (".xls", ".xlsx", ".xlsm"):
        return _read_excelish(path)
    return read_sdr_csv(path)


def _scan_excel(src, engine=None):
    """Find the SDR header row in a workbook (path or buffer) and load it."""
    kw = {"engine": engine} if engine else {}
    probe = pd.read_excel(src, header=None, nrows=30, **kw)
    for i, row in probe.iterrows():
        if _looks_like_sdr(row.dropna().tolist()):
            if hasattr(src, "seek"):
                src.seek(0)
            return pd.read_excel(src, skiprows=i, **kw)
    raise ValueError("no SDR header row found in Excel sheet")


def _read_excelish(path):
    """A file with an Excel extension that may not be a real workbook."""
    import io
    import zipfile
    try:
        return _scan_excel(path)
    except ImportError:
        raise RuntimeError(
            "reading Excel exports needs an engine: pip install openpyxl "
            "(for .xlsx) or xlrd (for .xls)")
    except Exception as first_err:
        # extension lies about the contents - try what a grid file really is
        if zipfile.is_zipfile(path):
            # Bloomberg workbooks can use nonstandard member names
            # (xl/workbook2.xml), which defeats pandas' format sniffing but
            # not openpyxl itself - force the engine to skip the sniff
            try:
                return _scan_excel(path, engine="openpyxl")
            except ImportError:
                raise RuntimeError(
                    "this export is an Excel workbook - pip install openpyxl")
            except Exception:
                pass
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                for n in names:
                    low = n.lower()
                    try:
                        if low.endswith((".csv", ".txt")):
                            text = zf.read(n).decode("utf-8-sig", errors="replace")
                            return _read_sdr_text(text)
                        if low.endswith((".xlsx", ".xlsm", ".xls")):
                            return _scan_excel(io.BytesIO(zf.read(n)))
                    except Exception:
                        continue
            try:
                return _parse_xlsx_xml(path)
            except Exception:
                pass
            raise ValueError(
                f"'{path}' is a zip container with no readable table inside "
                f"(contents: {names[:8]}) - re-export from SDR <GO> choosing "
                "CSV output")
        try:  # plain text / csv with a misleading extension
            return read_sdr_csv(path)
        except Exception:
            pass
        try:  # html table with a misleading extension
            for t in pd.read_html(path, header=0):
                if _looks_like_sdr(t.columns):
                    return t
        except Exception:
            pass
        raise ValueError(
            f"could not parse '{path}' as an Excel workbook ({first_err}) - "
            "re-export from SDR <GO> choosing CSV output")


def _parse_xlsx_xml(path):
    """Last resort: read the worksheet XML straight out of the zip.

    Copes with workbooks whose internal member names/relationships are
    nonstandard enough that both pandas and openpyxl refuse them, as long
    as a worksheets/*.xml part and (optionally) sharedStrings.xml exist.
    """
    import re
    import zipfile
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        shared = []
        for n in zf.namelist():
            if n.lower().endswith("sharedstrings.xml"):
                root = ET.fromstring(zf.read(n))
                shared = ["".join(t.text or "" for t in si.iter(ns + "t"))
                          for si in root.iter(ns + "si")]
                break
        sheets = sorted(n for n in zf.namelist()
                        if re.search(r"worksheets/[^/]+\.xml$", n, re.I))
        if not sheets:
            raise ValueError("no worksheet xml inside container")
        root = ET.fromstring(zf.read(sheets[0]))

    rows = []
    for row in root.iter(ns + "row"):
        cells = {}
        for c in row.iter(ns + "c"):
            m = re.match(r"[A-Z]+", c.get("r", ""))
            if m:
                idx = 0
                for ch in m.group():
                    idx = idx * 26 + ord(ch) - 64
                idx -= 1
            else:
                idx = len(cells)
            v = c.find(ns + "v")
            if c.get("t") == "s" and v is not None:
                si = int(v.text)
                cells[idx] = shared[si] if si < len(shared) else None
            elif c.get("t") == "inlineStr":
                is_el = c.find(ns + "is")
                cells[idx] = ("".join(t.text or "" for t in is_el.iter(ns + "t"))
                              if is_el is not None else None)
            else:
                cells[idx] = v.text if v is not None else None
        if cells:
            rows.append([cells.get(i) for i in range(max(cells) + 1)])
    if not rows:
        raise ValueError("worksheet xml holds no rows")

    width = max(len(r) for r in rows)
    rows = [r + [None] * (width - len(r)) for r in rows]
    for i, r in enumerate(rows[:30]):
        if _looks_like_sdr([x for x in r if x]):
            cols = [str(x) if x is not None else f"col{j}"
                    for j, x in enumerate(r)]
            return pd.DataFrame(rows[i + 1:], columns=cols)
    raise ValueError("no SDR header row found in worksheet xml")


def _to_datetime_smart(s):
    """Datetimes from strings OR Excel serial numbers (workbook cells store
    dates as day counts from 1899-12-30; times are the fraction)."""
    num = pd.to_numeric(s, errors="coerce")
    if num.between(20000, 90000).mean() > 0.8:      # ~1954..2146
        return pd.to_datetime(num, unit="D", origin="1899-12-30")
    if num.between(0, 1, inclusive="neither").mean() > 0.8:
        # bare time-of-day serials: fraction of a day, assume today
        return pd.Timestamp.now().normalize() + pd.to_timedelta(num, unit="D")
    return pd.to_datetime(s, errors="coerce", dayfirst=False)


def _read_sdr_text(text):
    """SDR table from raw text: sniff delimiter, skip preamble rows."""
    import io
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
        if _looks_like_sdr(df.columns):
            return df
    except Exception:
        pass  # ragged preamble rows - fall through to the line scan
    for i, line in enumerate(text.splitlines()[:30]):
        for sep in (",", "\t", ";"):
            if _looks_like_sdr(line.split(sep)):
                return pd.read_csv(io.StringIO(text), sep=sep, skiprows=i)
    raise ValueError(
        "could not find an SDR header row - expected columns like "
        "'Trade Time' / 'Effective Date' / 'Expiration Date'")


def read_sdr_csv(path):
    """Read a delimited SDR <GO> export, tolerating preamble rows and odd
    delimiters (comma / tab / semicolon)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    try:
        return _read_sdr_text(text)
    except ValueError as e:
        raise ValueError(f"{e} (file: '{path}')")


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
    # combined trades (basis / curve / fly) quote a level in bp, not a rate
    out["rate"] = ["" if pd.isna(r)
                   else (f"{r * 100:.2f}bp" if sp else f"{r:.4f}")
                   for r, sp in zip(df["rate"], df["is_spread"])]
    out["notional"] = [
        (f"{n / 1e6:,.0f}mm" if n >= 1e6 else f"{n:,.0f}") + ("+" if c else "")
        if pd.notna(n) else ""
        for n, c in zip(df["notional"], df["capped"])
    ]
    out["dv01"] = df["dv01"].map(lambda d: f"{d:,.0f}" if pd.notna(d) else "")
    out["index"] = df["index"].fillna("")
    same_day = df["time"].dt.normalize().nunique() <= 1
    fmt = "%H:%M:%S" if same_day else "%m/%d %H:%M:%S"
    out["time"] = df["time"].dt.strftime(fmt)
    out["platform"] = df["platform"]
    out["note"] = df["note"].fillna("")
    return out.reset_index(drop=True)

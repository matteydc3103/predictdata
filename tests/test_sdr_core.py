"""SDR pipeline checks: tenor snapping (incl. business-day drift), notional
parsing, DV01 sanity, filters, package tagging, and export-file sniffing."""

import time

import numpy as np
import pandas as pd
import pytest

from sdr_monitor.sdr_blotter import resolve_export
from sdr_monitor.sdr_core import (approx_dv01, build_table, demo_trades,
                                  format_table, parse_notional, read_sdr_csv,
                                  read_sdr_export, snap_tenor, year_frac)


# ---------- tenor ----------
@pytest.mark.parametrize("eff,mat,label", [
    ("2026-11-08", "2036-11-08", "10y"),   # exact american 10y
    ("2026-11-10", "2036-11-12", "10y"),   # T+2 eff, mod-following maturity
    ("2026-11-08", "2027-05-10", "6m"),
])
def test_tenor_from_dates(eff, mat, label):
    assert snap_tenor(year_frac(pd.Timestamp(eff), pd.Timestamp(mat))) == label


@pytest.mark.parametrize("yf,label", [
    (1.505, "18m"), (30.02, "30y"), (13.004, "13y"), (9.71, "9.71y"),
])
def test_tenor_snapping(yf, label):
    assert snap_tenor(yf) == label


# ---------- notional ----------
@pytest.mark.parametrize("raw,val,capped", [
    (150_000_000, 150e6, False),
    ("150,000,000", 150e6, False),
    ("250MM+", 250e6, True),
    ("1.5BN", 1.5e9, False),
    ("750k", 750e3, False),
])
def test_parse_notional(raw, val, capped):
    assert parse_notional(raw) == (val, capped)


def test_parse_notional_garbage():
    v, _ = parse_notional("garbage")
    assert np.isnan(v)


# ---------- dv01 ----------
def test_dv01_sanity():
    d = approx_dv01(100e6, 2.5, 10.0)           # annuity ~8.75 -> ~87.5k
    assert 80_000 < d < 92_000
    assert abs(approx_dv01(100e6, 0.0, 10.0) - 100_000) < 1
    assert approx_dv01(100e6, 2.5, 10.0, fwd_years=5.0) < d


# ---------- pipeline ----------
def _raw(rows):
    base = {"Trade Time": "08/07/2026 09:31:24", "Effective Date": "08/11/2026",
            "Expiration Date": "08/11/2036", "Curr": "EUR", "Fixed Rate": 2.5,
            "Notional Amount 1": "100,000,000", "Index": "ESTR", "SEF": "DWSF",
            "Product": "IRSwap:FixedFloat"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_pipeline_filters_and_curve_trade():
    raw = _raw([
        {"Fixed Rate": 2.512},                                    # 10y leg
        {"Expiration Date": "08/11/2031", "Fixed Rate": 2.201,
         "Notional Amount 1": "250MM+"},                          # 5y leg, same time
        {"Trade Time": "08/07/2026 09:45:01",
         "Expiration Date": "08/13/2046", "Index": "EURIBOR 6M",
         "SEF": "TPSF"},                                          # keep, 20y
        {"Trade Time": "08/07/2026 09:50:00", "SEF": "TWSF"},     # drop venue
        {"Trade Time": "08/07/2026 09:51:00", "SEF": "BBSF"},     # drop venue
        {"Trade Time": "08/07/2026 09:51:30", "SEF": "BMTF"},     # drop venue
        {"Trade Time": "08/07/2026 09:52:00", "Curr": "USD",
         "Index": "SOFR"},                                        # drop ccy
        {"Trade Time": "08/07/2026 09:53:00",
         "Product": "Swaption"},                                  # drop product
    ])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 2                       # 20y single + collapsed curve trade
    assert t["time"].is_monotonic_decreasing
    assert t.iloc[0]["tenor"] == "20y"
    assert not t["platform"].isin(["TWSF", "TREU", "BBSF", "BMTF"]).any()

    curve = t[t["note"] == "curve"].iloc[0]
    assert curve["tenor"] == "5s10s"
    assert curve["is_spread"]
    assert abs(curve["rate"] - (2.512 - 2.201)) < 1e-9   # long minus short
    assert curve["notional"] == 100e6                    # longer leg's size

    disp = format_table(t)
    assert list(disp.columns) == ["tenor", "rate", "notional", "dv01",
                                  "index", "time", "platform", "note"]
    assert "31.10bp" in disp["rate"].values              # level shown in bp


def test_basis_and_eurex_lch():
    base = {"Trade Time": "08/07/2026 09:31:24", "Effective Date": "08/11/2026",
            "Expiration Date": "08/11/2031"}
    # same time, tenor, notional; different index -> 3s6s basis
    raw = _raw([{**base, "Fixed Rate": 2.9890, "Index": "EUR003M"},
                {**base, "Fixed Rate": 3.0508, "Index": "EUR006M"}])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 1
    row = t.iloc[0]
    assert row["note"] == "3s6s basis"
    assert row["tenor"] == "5y"
    assert abs(row["rate"] - (3.0508 - 2.9890)) < 1e-9   # 6m minus 3m
    assert row["index"] == "EURIBOR 3M / EURIBOR 6M"

    # same index too -> eurex/lch, absolute level
    raw2 = _raw([{**base, "Fixed Rate": 3.0760, "Index": "EUR006M"},
                 {**base, "Fixed Rate": 3.0750, "Index": "EUR006M"}])
    t2 = build_table(raw2, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t2) == 1
    assert t2.iloc[0]["note"] == "eurex/lch"
    assert abs(t2.iloc[0]["rate"] - 0.001) < 1e-9        # always positive


def test_fly():
    base = {"Trade Time": "08/07/2026 09:31:24", "Effective Date": "08/11/2026"}
    raw = _raw([
        {**base, "Expiration Date": "08/11/2034", "Fixed Rate": 3.10,
         "Notional Amount 1": "40,000,000"},                       # 8y wing
        {**base, "Expiration Date": "08/11/2035", "Fixed Rate": 3.15,
         "Notional Amount 1": "75,000,000"},                       # 9y belly
        {**base, "Expiration Date": "08/11/2036", "Fixed Rate": 3.18,
         "Notional Amount 1": "35,000,000"},                       # 10y wing
    ])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 1
    row = t.iloc[0]
    assert row["tenor"] == "8s9s10s" and row["note"] == "fly"
    assert abs(row["rate"] - (2 * 3.15 - 3.10 - 3.18)) < 1e-9   # 2*belly - wings
    assert row["notional"] == 75e6                               # belly size


def test_clips_merge_to_eurex_lch():
    # four 30y prints at one second: 2x25mm @3.2860 + 2x25mm @3.2815
    # -> two 50mm legs -> one eurex/lch row, 50mm at 0.45bp
    base = {"Trade Time": "08/07/2026 09:48:53", "Effective Date": "08/11/2026",
            "Expiration Date": "08/11/2056", "Index": "EUR006M",
            "Notional Amount 1": "25,000,000"}
    raw = _raw([{**base, "Fixed Rate": 3.2860}, {**base, "Fixed Rate": 3.2860},
                {**base, "Fixed Rate": 3.2815}, {**base, "Fixed Rate": 3.2815}])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 1
    row = t.iloc[0]
    assert row["note"] == "eurex/lch" and row["tenor"] == "30y"
    assert row["notional"] == 50e6
    assert abs(row["rate"] - 0.0045) < 1e-9            # 0.45bp


def test_clips_merge_single_trade():
    # two identical prints at one second are one trade in two clips
    base = {"Trade Time": "08/07/2026 09:10:00", "Fixed Rate": 2.512,
            "Notional Amount 1": "25,000,000"}
    raw = _raw([dict(base), dict(base)])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 1
    assert t.iloc[0]["notional"] == 50e6
    assert not t.iloc[0]["is_spread"]


def test_gadget_notes():
    raw = _raw([
        {"Trade Time": "08/07/2026 09:01:00", "Fixed Rate": 3.17748},  # 10y 5dp
        {"Trade Time": "08/07/2026 09:02:00", "Fixed Rate": 3.05080,
         "Expiration Date": "08/11/2031"},                             # 5y 4dp
        {"Trade Time": "08/07/2026 09:03:00", "Fixed Rate": 2.77701,
         "Expiration Date": "08/11/2031"},                             # 5y 5dp
    ])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    notes = dict(zip(t["time"].dt.strftime("%H:%M:%S"), t["note"]))
    assert notes["09:01:00"] == "10Y GADGET"    # 5dp, tenor > 6y
    assert notes["09:02:00"] == ""              # only 4dp
    assert notes["09:03:00"] == "5Y GADGET"     # 5dp, tenor <= 6y


def test_platform_rename():
    raw = _raw([{"SEF": "BGCD", "Trade Time": "08/07/2026 09:01:00"},
                {"SEF": "TPSE", "Trade Time": "08/07/2026 09:02:00"},
                {"SEF": "TSEF", "Trade Time": "08/07/2026 09:03:00"},
                {"SEF": "GSEF", "Trade Time": "08/07/2026 09:04:00"},
                {"SEF": "IOIR", "Trade Time": "08/07/2026 09:05:00"}])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert set(t["platform"]) == {"BGC", "TP/ICAP", "TRADS", "GFI", "IOIR"}


def test_pipeline_bloomberg_grid_layout():
    """Exact column layout observed in a real C:/blp/data grid.xlsx export."""
    raw = pd.DataFrame({
        "Type":        ["IRS", "IRS", "Swaption"],
        "Code":        ["A1", "A2", "A3"],
        "Effective":   ["08/11/2026"] * 3,
        "Expiration":  ["08/11/2036", "08/13/2046", "08/11/2036"],
        "Rate 1":      [2.512, None, 1.0],
        "Leg 1":       ["FIXED", "EUESTR", "X"],
        "Rate 2":      [None, 2.688, None],
        "Leg 2":       ["EUESTR", "FIXED", "X"],
        "Curr":        ["EUR", "EUR", "EUR"],
        "Not.":        ["150", "250", "75"],          # quoted in millions
        "Unnamed: 10": [None, "+", None],             # capped marker column
        "Cleared":     ["C", "C", "C"],
        "Src":         ["DTCC", "DTCC", "DTCC"],
        "Platform ID": ["DWSF", "TPSF", "DWSF"],
        "Trade Time":  ["08/07/2026 09:31:24", "08/07/2026 09:45:01",
                        "08/07/2026 09:50:00"],
        "Fixed PF 1":  ["A", "A", "A"],
        "DV01":        ["131,413", None, "10"],
        "Index":       ["ESTR", "ESTR", "ESTR"],
    })
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 2                                # swaption dropped
    r10 = t[t["tenor"] == "10y"].iloc[0]
    r20 = t[t["tenor"] == "20y"].iloc[0]
    assert r10["rate"] == 2.512                       # from Rate 1
    assert r20["rate"] == 2.688                       # coalesced from Rate 2
    assert r10["notional"] == 150e6                   # millions heuristic
    assert r20["capped"] and not r10["capped"]        # '+' marker column
    assert r10["dv01"] == 131413                      # feed DV01 preferred
    assert np.isfinite(r20["dv01"]) and r20["dv01"] > 100_000  # approximated
    assert set(t["platform"]) == {"DWSF", "TPSF"}     # Platform ID mapped
    assert (t["note"] == "").all()                    # no 5dp prints here


@pytest.mark.parametrize("code,leg2,label", [
    ("EUR006M", "", "EURIBOR 6M"),
    ("EUR003M", "", "EURIBOR 3M"),
    ("EUR012M", "", "EURIBOR 12M"),
    (float("nan"), "EUR-EURIBOR-Reuters", "EURIBOR"),
    (float("nan"), "EUR-EuroSTR-COMPOUND", "ESTR"),
    ("ESTR", "", "ESTR"),
    ("SOFR", "", "SOFR"),
    (float("nan"), "", ""),
])
def test_pretty_index(code, leg2, label):
    from sdr_monitor.sdr_core import _pretty_index
    assert _pretty_index(code, leg2) == label


def test_demo_roundtrip():
    t = build_table(demo_trades(seed=7))
    assert len(t) > 0
    assert (t["tenor"].str.len() > 0).all()


# ---------- csv sniffing ----------
def test_read_sdr_csv_plain(tmp_path):
    p = tmp_path / "plain.csv"
    demo_trades(n_prints=5, seed=1).to_csv(p, index=False)
    assert len(read_sdr_csv(p)) > 0


def test_read_sdr_csv_with_preamble(tmp_path):
    p = tmp_path / "export.csv"
    body = demo_trades(n_prints=5, seed=1).to_csv(index=False)
    p.write_text("SDR - Swap Data Repository\nAs of 08/07/2026\n\n" + body)
    df = read_sdr_csv(p)
    assert "Trade Time" in df.columns and len(df) > 0


def test_read_sdr_csv_no_header(tmp_path):
    p = tmp_path / "junk.csv"
    p.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError, match="SDR header"):
        read_sdr_csv(p)


def test_read_sdr_export_excel_with_preamble(tmp_path):
    pytest.importorskip("openpyxl")
    p = tmp_path / "grid.xlsx"
    body = demo_trades(n_prints=5, seed=1)
    with pd.ExcelWriter(p) as xw:
        pd.DataFrame([["SDR - Swap Data Repository"], ["As of 08/07/2026"]]) \
            .to_excel(xw, header=False, index=False, startrow=0)
        body.to_excel(xw, index=False, startrow=3)
    df = read_sdr_export(p)
    assert "Trade Time" in df.columns and len(df) == len(body)


def test_read_sdr_export_zip_wrapping_csv(tmp_path):
    # Bloomberg sometimes writes a zip container with an Excel extension
    import zipfile
    p = tmp_path / "grid.xlsx"
    body = demo_trades(n_prints=5, seed=1).to_csv(index=False)
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("grid.csv", "SDR - Swap Data Repository\n\n" + body)
    df = read_sdr_export(p)
    assert "Trade Time" in df.columns and len(df) == 5


def test_read_sdr_export_csv_named_xls(tmp_path):
    p = tmp_path / "grid.xls"
    p.write_text(demo_trades(n_prints=5, seed=1).to_csv(index=False))
    df = read_sdr_export(p)
    assert "Trade Time" in df.columns and len(df) == 5


def test_read_sdr_export_nonstandard_workbook_members(tmp_path):
    # Bloomberg grid.xlsx observed in the wild: real workbook, but internals
    # named xl/workbook2.xml / sheet2.xml, which defeats pandas' sniffing
    import zipfile
    pytest.importorskip("openpyxl")
    normal = tmp_path / "normal.xlsx"
    body = demo_trades(n_prints=5, seed=1)
    with pd.ExcelWriter(normal) as xw:
        body.to_excel(xw, index=False)
    weird = tmp_path / "grid.xlsx"
    ren = {"xl/workbook.xml": "xl/workbook2.xml",
           "xl/_rels/workbook.xml.rels": "xl/_rels/workbook2.xml.rels",
           "xl/worksheets/sheet1.xml": "xl/worksheets/sheet2.xml"}
    with zipfile.ZipFile(normal) as zin, zipfile.ZipFile(weird, "w") as zout:
        for item in zin.namelist():
            zout.writestr(ren.get(item, item), zin.read(item))
    df = read_sdr_export(weird)
    assert "Trade Time" in df.columns and len(df) == len(body)


def test_to_datetime_smart_serials():
    from sdr_monitor.sdr_core import _to_datetime_smart
    s = pd.Series(["46245.39683", "46245", "46260"])   # excel day serials
    out = _to_datetime_smart(s)
    assert out.iloc[1] == pd.Timestamp("2026-08-11")
    assert out.iloc[0].strftime("%H:%M") == "09:31"
    plain = _to_datetime_smart(pd.Series(["08/11/2026", "08/12/2026"]))
    assert plain.iloc[0] == pd.Timestamp("2026-08-11")


def test_read_sdr_export_wk1_rejected(tmp_path):
    p = tmp_path / "grid.wk1"
    p.write_bytes(b"\x00\x00")
    with pytest.raises(ValueError, match="WK1"):
        read_sdr_export(p)


# ---------- export resolution (newest grid* wins) ----------
def test_resolve_export_newest_grid(tmp_path):
    import os
    old = tmp_path / "grid.csv"
    new = tmp_path / "grid(1).csv"
    old.write_text("x")
    new.write_text("y")
    past = time.time() - 100
    os.utime(old, (past, past))
    # stem -> newest grid* match
    assert resolve_export(str(tmp_path / "grid")) == new
    # folder -> newest file inside
    assert resolve_export(str(tmp_path)) == new
    # exact file -> that file, even if older
    assert resolve_export(str(old)) == old
    # missing -> None
    assert resolve_export(str(tmp_path / "nope" / "grid")) is None

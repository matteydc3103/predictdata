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


def test_pipeline_filters_and_packages():
    raw = _raw([
        {},                                                       # keep (P1 leg)
        {"Expiration Date": "08/11/2031", "Fixed Rate": 2.201,
         "Notional Amount 1": "250MM+"},                          # keep (P1 leg)
        {"Trade Time": "08/07/2026 09:45:01",
         "Expiration Date": "08/13/2046", "Index": "EURIBOR 6M",
         "SEF": "TPSF"},                                          # keep, 20y
        {"Trade Time": "08/07/2026 09:50:00", "SEF": "TWSF"},     # drop venue
        {"Trade Time": "08/07/2026 09:51:00", "SEF": "BBSF"},     # drop venue
        {"Trade Time": "08/07/2026 09:52:00", "Curr": "USD",
         "Index": "SOFR"},                                        # drop ccy
        {"Trade Time": "08/07/2026 09:53:00",
         "Product": "Swaption"},                                  # drop product
    ])
    t = build_table(raw, ccy="EUR", now=pd.Timestamp("2026-08-07 10:00"))
    assert len(t) == 3
    assert t["time"].is_monotonic_decreasing
    assert t.iloc[0]["tenor"] == "20y"
    assert not t["platform"].isin(["TWSF", "TREU", "BBSF"]).any()
    pkg = t[t["related"] != ""]
    assert len(pkg) == 2 and set(pkg["related"]) == {"P1"}

    disp = format_table(t)
    assert list(disp.columns) == ["tenor", "rate", "notional", "dv01",
                                  "index", "time", "platform", "related"]
    assert "250mm+" in disp["notional"].values


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

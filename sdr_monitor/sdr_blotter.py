"""Local SDR trade blotter -> self-refreshing HTML page.

Runs on your own machine (no Bloomberg libraries needed). Point it at an
SDR <GO> CSV export; it renders the filtered EUR vanilla blotter to an HTML
file and, in --watch mode, keeps regenerating whenever the export file
changes - so re-exporting from the terminal to the same path ticks the page.

Usage:
    python sdr_blotter.py --csv "C:/Users/you/Downloads/sdr_export.csv" --watch
    python sdr_blotter.py --demo            # synthetic data, no export needed

The page auto-reloads itself every --reload seconds (default 15).
"""
import argparse
import html
import os
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdr_core import build_table, demo_trades, format_table, read_sdr_export

DEFAULT_EXCLUDE = "TWSF,TREU,BBSF"
# Bloomberg's terminal export drop folder; grid exports land here as
# grid.csv / grid.xls / grid(1).csv ... - we always take the newest.
BLP_DATA_DIR = Path("C:/blp/data")
BLP_PATTERN = "grid*"


def _newest(folder, pattern):
    files = [p for p in folder.glob(pattern) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def resolve_export(csv_arg):
    """Work out which export file to read *right now* (newest wins).

    Accepts a file, a folder (newest file inside), or a name stem like
    C:/blp/data/grid (newest grid* match). With no --csv at all, tries
    ./sdr_export.csv, then C:/blp/data/grid*, then ~/Downloads/sdr_export.csv.
    Re-run every poll so numbered re-exports are picked up automatically.
    """
    if csv_arg:
        p = Path(csv_arg)
        if p.is_file():
            return p
        if p.is_dir():
            return _newest(p, "*")
        if p.parent.is_dir():
            return _newest(p.parent, p.name + "*")
        return None
    local = Path("sdr_export.csv")
    if local.is_file():
        return local
    if BLP_DATA_DIR.is_dir():
        hit = _newest(BLP_DATA_DIR, BLP_PATTERN)
        if hit:
            return hit
    dl = Path.home() / "Downloads" / "sdr_export.csv"
    return dl if dl.is_file() else None
PKG_COLORS = ["#e8b339", "#5aa9e6", "#7fc97f", "#e78ac3", "#b39ddb"]


def render_html(table, disp, meta):
    """Numeric table + formatted table + metadata -> full HTML document."""
    pkg_ids = [p for p in table["related"].drop_duplicates() if p]
    pkg_color = {p: PKG_COLORS[i % len(PKG_COLORS)] for i, p in enumerate(pkg_ids)}

    body_rows = []
    for (_, num), (_, row) in zip(table.iterrows(), disp.iterrows()):
        pkg = num["related"]
        style = ""
        if pkg:
            c = pkg_color[pkg]
            style = f' style="box-shadow: inset 3px 0 0 {c}; background:{c}14"'
        cells = "".join(
            f'<td class="{cls}">{html.escape(str(row[col]))}</td>'
            for col, cls in (("tenor", "tenor"), ("rate", "num"),
                             ("notional", "num"), ("dv01", "num"),
                             ("index", "txt"), ("time", "time"),
                             ("platform", "txt"), ("related", "pkg")))
        body_rows.append(f"<tr{style}>{cells}</tr>")

    tot_ntl = np.nansum(table["notional"].to_numpy(dtype=float))
    tot_dv01 = np.nansum(table["dv01"].to_numpy(dtype=float))
    demo_badge = '<span class="badge demo">DEMO DATA</span>' if meta["demo"] else ""
    reload_tag = (f'<meta http-equiv="refresh" content="{meta["reload"]}">'
                  if meta["reload"] > 0 else "")
    src_line = html.escape(meta["source"])

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
{reload_tag}
<title>SDR Blotter — {meta['ccy']} Vanilla IRS</title>
<style>
  :root {{ --bg:#101317; --panel:#171b21; --line:#262c35; --txt:#d7dce3;
           --dim:#8b93a1; --hd:#aab3c0; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--bg); color:var(--txt);
         font:13px/1.45 "Consolas","SF Mono","Menlo",monospace }}
  header {{ position:sticky; top:0; background:var(--panel);
            border-bottom:1px solid var(--line); padding:10px 16px; z-index:2 }}
  h1 {{ margin:0 0 4px; font-size:15px; letter-spacing:.06em; color:#fff }}
  .sub {{ color:var(--dim); font-size:12px }}
  .sub b {{ color:var(--txt); font-weight:600 }}
  .badge {{ display:inline-block; padding:1px 7px; border-radius:3px;
            font-size:11px; margin-left:8px; vertical-align:1px }}
  .demo {{ background:#5a4a12; color:#ffd867 }}
  table {{ border-collapse:collapse; width:100%; }}
  thead th {{ position:sticky; top:0; background:var(--panel); color:var(--hd);
              text-align:left; font-size:11px; letter-spacing:.08em;
              text-transform:uppercase; padding:8px 14px 6px;
              border-bottom:1px solid var(--line) }}
  thead th.num {{ text-align:right }}
  td {{ padding:5px 14px; border-bottom:1px solid var(--line);
        white-space:nowrap; font-variant-numeric:tabular-nums }}
  td.num {{ text-align:right }}
  td.tenor {{ font-weight:700; color:#fff }}
  td.time {{ color:var(--dim) }}
  td.pkg {{ font-weight:700 }}
  tr:hover td {{ background:#1d232c }}
  footer {{ color:var(--dim); padding:10px 16px; font-size:11px }}
</style>
</head><body>
<header>
  <h1>SDR BLOTTER — {meta['ccy']} VANILLA IRS{demo_badge}</h1>
  <div class="sub"><b>{len(table)}</b> prints &nbsp;·&nbsp;
    Σnotional <b>{tot_ntl / 1e9:,.2f}bn</b> &nbsp;·&nbsp;
    ΣDV01 <b>{tot_dv01 / 1e3:,.0f}k</b> &nbsp;·&nbsp;
    <b>{len(pkg_ids)}</b> packages &nbsp;·&nbsp;
    excl {html.escape(meta['exclude'])} &nbsp;·&nbsp;
    generated {meta['generated']}</div>
</header>
<table>
<thead><tr>
  <th>tenor</th><th class="num">rate</th><th class="num">notional</th>
  <th class="num">dv01</th><th>index</th><th>time</th><th>platform</th>
  <th>related</th>
</tr></thead>
<tbody>
{chr(10).join(body_rows)}
</tbody>
</table>
<footer>source: {src_line} · same-timestamp prints share a package tag (P1, P2, …)
 · dv01 is a par-annuity approximation · page reloads every {meta['reload']}s</footer>
<script>
  addEventListener('beforeunload', () => sessionStorage.setItem('sdrScroll', scrollY));
  addEventListener('load', () => {{
    const y = sessionStorage.getItem('sdrScroll');
    if (y) scrollTo(0, +y);
  }});
</script>
</body></html>"""


def generate(args, csv_path):
    if args.demo:
        raw, source = demo_trades(), "demo generator"
    else:
        raw = read_sdr_export(csv_path)
        mtime = datetime.fromtimestamp(csv_path.stat().st_mtime).strftime("%H:%M:%S")
        source = f"{csv_path} (exported {mtime})"
    table = build_table(raw, ccy=args.ccy,
                        exclude_platforms=args.exclude.split(",")).head(args.max_rows)
    disp = format_table(table)
    doc = render_html(table, disp, {
        "ccy": args.ccy.upper(), "exclude": args.exclude.replace(",", "/"),
        "reload": args.reload, "demo": args.demo, "source": source,
        "generated": datetime.now().strftime("%H:%M:%S"),
    })
    out = Path(args.out)
    tmp = out.with_suffix(".tmp")          # atomic swap so the browser never
    tmp.write_text(doc, encoding="utf-8")  # reads a half-written page
    os.replace(tmp, out)
    return out, len(table)


def main():
    ap = argparse.ArgumentParser(description="SDR CSV export -> live HTML trade blotter")
    ap.add_argument("--csv", help="path to the SDR <GO> export - a file, a "
                    "folder, or a stem like C:/blp/data/grid (newest match "
                    "wins). Default: ./sdr_export.csv, then C:/blp/data/grid*, "
                    "then ~/Downloads/sdr_export.csv")
    ap.add_argument("--demo", action="store_true", help="use synthetic demo trades")
    ap.add_argument("--ccy", default="EUR", help="currency filter (default EUR)")
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE,
                    help=f"comma-separated venues to drop (default {DEFAULT_EXCLUDE})")
    ap.add_argument("--out", default="sdr_blotter.html", help="output HTML file")
    ap.add_argument("--reload", type=int, default=15,
                    help="page auto-reload seconds, 0 disables (default 15)")
    ap.add_argument("--watch", action="store_true",
                    help="keep running; regenerate whenever the CSV changes")
    ap.add_argument("--poll", type=int, default=5,
                    help="seconds between CSV change checks in --watch (default 5)")
    ap.add_argument("--max-rows", type=int, default=500)
    ap.add_argument("--no-open", action="store_true",
                    help="don't open the page in the browser")
    args = ap.parse_args()

    csv_path = None
    if not args.demo:
        csv_path = resolve_export(args.csv)
        if csv_path is None:
            where = args.csv or (f"./sdr_export.csv, {BLP_DATA_DIR}\\{BLP_PATTERN}, "
                                 f"{Path.home() / 'Downloads' / 'sdr_export.csv'}")
            sys.exit(f"No SDR export found (looked for: {where}).\n"
                     "Export from SDR <GO> (Rates / Vanilla tab, Actions > Export)\n"
                     "and pass its location with --csv, or run with --demo.")
        print(f"using export: {csv_path}")

    out, n = generate(args, csv_path)
    print(f"wrote {out.resolve()} ({n} prints)")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    if not args.watch:
        return

    if args.demo:
        watching = "demo"
    elif args.csv:
        watching = args.csv
    elif csv_path.parent == BLP_DATA_DIR:
        watching = str(BLP_DATA_DIR / BLP_PATTERN)
    else:
        watching = str(csv_path)
    print(f"watching {watching} - Ctrl-C to stop")
    last = (csv_path, csv_path.stat().st_mtime) if csv_path else None
    try:
        while True:
            time.sleep(max(args.poll, 1))
            if args.demo:                      # demo mode: refresh every poll
                generate(args, None)
                continue
            # re-resolve so a new grid(2).csv beats the old grid.csv
            cur = resolve_export(args.csv)
            if cur is None or not cur.exists():
                continue                       # mid-overwrite; catch it next poll
            state = (cur, cur.stat().st_mtime)
            if state != last:
                last = state
                time.sleep(0.5)                # let the export finish writing
                try:
                    _, n = generate(args, cur)
                    print(f"{datetime.now():%H:%M:%S} refreshed from "
                          f"{cur.name} ({n} prints)")
                except Exception as e:
                    print(f"{datetime.now():%H:%M:%S} refresh failed: {e}")
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()

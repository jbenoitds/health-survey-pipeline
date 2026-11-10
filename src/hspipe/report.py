"""Excel workbook and self-contained HTML report."""
from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import pandas as pd

from .context import Context


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def write_excel(ctx: Context, path: Path) -> Path:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    t = ctx.tables
    sheets = [
        ("Overview", _overview_frame(ctx)),
        ("Table 1", t.get("table1")),
        ("Scales", t.get("scales")),
        ("Subscales", t.get("subscales")),
        ("Timepoint change", t.get("timepoints")),
        ("Continuous", t.get("continuous")),
        ("Categorical", t.get("categorical")),
        ("Missingness", t.get("missingness")),
        ("Issue summary", _issue_summary(t.get("issues"))),
        ("Validation issues", t.get("issues")),
        ("Codebook", t.get("codebook")),
        ("Processing log", pd.DataFrame(ctx.log)),
    ]
    if ctx.df.size <= 2_000_000:
        sheets.insert(1, ("Cleaned data", ctx.df))
    path.parent.mkdir(parents=True, exist_ok=True)
    head_fill = PatternFill("solid", fgColor="1F4E79")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, frame in sheets:
            if frame is None or frame.empty:
                continue
            frame = frame.copy()
            for c in frame.columns:
                if pd.api.types.is_datetime64_any_dtype(frame[c]):
                    frame[c] = frame[c].dt.date
            frame.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = head_fill
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            ws.freeze_panes = "B2" if name in {"Cleaned data", "Validation issues"} else "A2"
            for i, col in enumerate(frame.columns, start=1):
                sample = frame[col].head(200).map(lambda v: len(str(v)))
                width = min(60, max(10, len(str(col)) + 2, int(sample.max() if len(sample) else 0) + 2))
                ws.column_dimensions[get_column_letter(i)].width = width
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.000" if abs(cell.value) < 1 else "0.00"
    return path


def _overview_frame(ctx: Context) -> pd.DataFrame:
    issues = ctx.tables.get("issues", pd.DataFrame())
    rows = [
        ("Project", ctx.config["project"]["name"]),
        ("Run started", ctx.started),
        ("Input file", Path(ctx.config["input"]["data"]).name),
        ("Data dictionary", Path(ctx.config["input"]["data_dictionary"]).name if ctx.config["input"].get("data_dictionary") else "(none – types inferred)"),
        ("Raw rows x columns", f"{ctx.raw_shape[0]} x {ctx.raw_shape[1]}"),
        ("Clean rows x columns", f"{ctx.df.shape[0]} x {ctx.df.shape[1]}"),
        ("Validation errors", int((issues["severity"] == "error").sum()) if not issues.empty else 0),
        ("Validation warnings", int((issues["severity"] == "warning").sum()) if not issues.empty else 0),
        ("Scales scored", len(ctx.scales)),
    ]
    return pd.DataFrame(rows, columns=["item", "value"])


def _issue_summary(issues):
    if issues is None or issues.empty:
        return pd.DataFrame(columns=["rule", "severity", "variable", "n"])
    return (issues.groupby(["rule", "severity", "variable"]).size().rename("n").reset_index()
            .sort_values("n", ascending=False))


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--accent:#2a78d6;--track:#eeede8;--good:#0ca30c;--warn:#b97700;--crit:#d03a2f;--code:#f1f0ec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--accent:#3987e5;--track:#262624;--good:#2fbf2f;
--warn:#fab219;--crit:#f0645a;--code:#232321}}
:root[data-theme="dark"]{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;
--axis:#383835;--accent:#3987e5;--track:#262624;--good:#2fbf2f;--warn:#fab219;--crit:#f0645a;--code:#232321}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}
header h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}header p{margin:0;color:var(--ink2)}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:18px 0 8px;font-size:14px}nav a{color:var(--accent);text-decoration:none}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:20px;margin-top:20px}
h2{font-size:19px;margin:0 0 4px}h3{font-size:15px;margin:18px 0 6px}.sub{color:var(--ink2);margin:0 0 14px;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}.tile .k{color:var(--ink2);font-size:13px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--grid);vertical-align:top;white-space:nowrap}
th{color:var(--ink2);font-weight:600;border-bottom:1px solid var(--axis);position:sticky;top:0;background:var(--surface)}
td.wrap,th.wrap{white-space:normal;min-width:220px}td.num{text-align:right}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;padding:1px 8px;border-radius:99px;
border:1px solid currentColor}.b-error{color:var(--crit)}.b-warning{color:var(--warn)}.b-ok{color:var(--good)}
.callout{border-left:4px solid var(--crit);padding:10px 14px;background:var(--code);border-radius:6px;margin:12px 0}
.bars{display:grid;grid-template-columns:minmax(120px,240px) 1fr 56px;gap:6px 10px;align-items:center;font-size:13px}
.bars .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink2)}
.bars .track{height:10px;background:var(--track);border-radius:4px;position:relative}
.bars .fill{height:10px;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}
.bars .val{text-align:right;font-variant-numeric:tabular-nums}
.bars .row{display:contents}.bars .row:hover .name{color:var(--ink)}
details{margin-top:12px}summary{cursor:pointer;color:var(--accent);font-weight:600}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:12.5px}
footer{color:var(--muted);font-size:12.5px;margin-top:28px}
.toggle{float:right;font:inherit;font-size:13px;background:none;border:1px solid var(--grid);color:var(--ink2);
border-radius:6px;padding:4px 10px;cursor:pointer}
@media (max-width:640px){.bars{grid-template-columns:110px 1fr 48px}header h1{font-size:23px}}
"""

JS = """
(function(){var b=document.getElementById('theme');if(!b)return;var r=document.documentElement;
b.onclick=function(){var dark=r.getAttribute('data-theme')==='dark'||(!r.getAttribute('data-theme')&&
matchMedia('(prefers-color-scheme: dark)').matches);r.setAttribute('data-theme',dark?'light':'dark');};})();
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return ""
        if float(v).is_integer() and abs(v) < 1e12:
            return f"{int(v)}"
        return f"{v:.3f}" if abs(v) < 1 else f"{v:.2f}"
    if isinstance(v, pd.Timestamp):
        return "" if pd.isna(v) else str(v.date())
    return str(v)


def _table(df: pd.DataFrame, max_rows: int = 500, wrap=("label", "message", "Characteristic", "detail",
                                                       "value_labels")) -> str:
    if df is None or df.empty:
        return "<p class='sub'>Nothing to show.</p>"
    df = df.head(max_rows)
    head = "".join(f"<th class='{'wrap' if c in wrap else ''}'>{_e(c)}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            cls = "wrap" if c in wrap else ("num" if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) else "")
            txt = _fmt(v)
            if c == "severity" and txt in {"error", "warning"}:
                txt = f"<span class='badge b-{txt}'>{'&#9888;' if txt == 'warning' else '&#10006;'} {txt}</span>"
                cells.append(f"<td>{txt}</td>")
                continue
            cells.append(f"<td class='{cls}'>{_e(txt)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _bars(miss: pd.DataFrame, n: int = 20) -> str:
    if miss is None or miss.empty:
        return ""
    top = miss[(miss["pct_item_nonresponse"] > 0) & (miss["source"] == "raw")].head(n)
    if top.empty:
        return "<p class='sub'><span class='badge b-ok'>&#10004; complete</span> No item non-response.</p>"
    rows = []
    for _, r in top.iterrows():
        p = float(r["pct_item_nonresponse"])
        shown = int(r["n_rows"]) - int(r["skipped_by_design"]) - int(r["form_not_started"])
        n_miss = int(r["n_missing"]) - int(r["skipped_by_design"]) - int(r["form_not_started"])
        tip = (f"{r['variable']}: {n_miss} of {shown} unanswered ({p:.1f}%). Also {int(r['skipped_by_design'])} "
               f"skipped by design, {int(r['form_not_started'])} form not started.")
        rows.append(f"<div class='row' title='{_e(tip)}'><div class='name'>{_e(r['variable'])}</div>"
                    f"<div class='track'><div class='fill' style='width:{max(p, 0.5):.1f}%'></div></div>"
                    f"<div class='val'>{p:.1f}%</div></div>")
    return ("<div class='bars' role='img' aria-label='Item non-response, top variables'>"
            + "".join(rows) + "</div>")


def write_html(ctx: Context, path: Path) -> Path:
    t = ctx.tables
    issues = t.get("issues", pd.DataFrame())
    n_err = int((issues["severity"] == "error").sum()) if not issues.empty else 0
    n_warn = int((issues["severity"] == "warning").sum()) if not issues.empty else 0
    n_records = ctx.df.shape[0]
    name = ctx.config["project"]["name"]

    tiles = [
        (f"{ctx.raw_shape[0]:,}", "raw rows"), (f"{n_records:,}", "clean records"),
        (f"{ctx.df.shape[1]:,}", "variables out"), (f"{n_err:,}", "validation errors"),
        (f"{n_warn:,}", "warnings"), (str(len(ctx.scales)), "scales scored"),
    ]
    tiles_html = "".join(f"<div class='tile'><div class='v'>{_e(v)}</div><div class='k'>{_e(k)}</div></div>"
                         for v, k in tiles)

    safety = ""
    flags = t.get("flags")
    if flags is not None and not flags.empty and flags["n_flagged"].sum() > 0:
        items = "".join(f"<li><b>{int(r.n_flagged)}</b> record(s): {_e(r.label)} (<code>{_e(r.variable)}</code>)</li>"
                        for r in flags.itertuples())
        where = "output/restricted/safety_flags.csv" if (ctx.config["deidentify"].get("hash_id") or {}).get("enabled") \
            else "output/safety_flags.csv"
        safety = (f"<div class='callout'><b>&#9888; Safety items endorsed.</b> Follow your study's clinical "
                  f"safety protocol. Record IDs are in <code>{where}</code>.<ul>{items}</ul></div>")

    scales = t.get("scales")
    scales_html = ""
    if scales is not None and not scales.empty:
        show = scales[["scale", "label", "n_items", "method", "min_items", "reverse_items", "n_scored",
                       "n_insufficient", "mean", "sd", "min", "max", "alpha", "alpha_n"]].copy()
        scales_html = _table(show)
        low = scales[(scales["alpha"] < 0.7)]
        if not low.empty:
            scales_html += ("<p class='sub'><span class='badge b-warning'>&#9888; check</span> Cronbach's alpha below "
                            "0.70: " + ", ".join(_e(s) for s in low["scale"]) + ". Verify reverse-keyed items and "
                            "item coding.</p>")
        subs = t.get("subscales")
        if subs is not None and not subs.empty:
            scales_html += "<h3>Subscales</h3>" + _table(subs)

    issue_sum = _issue_summary(issues)
    sec = []
    sec.append(("overview", "Data quality", f"""
      <p class='sub'>{n_err} errors and {n_warn} warnings. With <code>on_invalid: {ctx.config['validation']['on_invalid']}</code>,
      invalid values were {'removed (set to missing)' if ctx.config['validation']['on_invalid'] == 'set_missing' else 'kept and only flagged'}.
      Every issue, with its record ID and row number in the raw file, is in <code>validation_issues.csv</code>.</p>
      {safety}{_table(issue_sum, 40)}
      <details><summary>First 200 individual issues</summary>{_table(issues, 200)}</details>"""))
    sec.append(("missing", "Missing data", f"""
      <p class='sub'><b>Item non-response</b>: unanswered items among participants who started the form and were
      shown the question. Answers hidden by branching logic (skipped by design) and forms never started (e.g. loss to
      follow-up) are counted separately in the full table. Hover a bar for counts.</p>{_bars(t.get('missingness'))}
      <details><summary>Full missingness table (all variables, by reason)</summary>{_table(t.get('missingness'))}</details>"""))
    if scales_html:
        sec.append(("scales", "Scale scores &amp; reliability",
                    "<p class='sub'>Totals use the minimum-valid-items rule shown; prorated sums are the item mean "
                    "times the number of items.</p>" + scales_html))
    grp = ctx.config["describe"].get("group_by")
    sec.append(("table1", "Table 1" + (f" by {_e(grp)}" if grp else ""),
                "<p class='sub'>Continuous: mean (SD), Welch t-test or one-way ANOVA. Categorical: n (% of non-missing), "
                "chi-square or Fisher exact. Unadjusted p-values, for description only.</p>" + _table(t.get("table1"))))
    tp = t.get("timepoints")
    if tp is not None and not tp.empty:
        sec.append(("change", "Change across timepoints",
                    "<p class='sub'>Complete pairs only. Paired t-test, Wilcoxon signed-rank, and Cohen's d<sub>z</sub>."
                    "</p>" + _table(tp)))
    sec.append(("descriptives", "Descriptive statistics",
                "<h3>Continuous</h3>" + _table(t.get("continuous")) +
                "<details><summary>Categorical frequencies</summary>" + _table(t.get("categorical"), 2000) + "</details>"))
    sec.append(("log", "Processing log",
                "<p class='sub'>Every step that touched the data, in order (also saved as <code>run_log.json</code>).</p>" +
                _table(_log_frame(ctx), 200)))
    sec.append(("codebook", "Codebook",
                "<details><summary>Show all variables</summary>" + _table(t.get("codebook"), 5000) + "</details>"))

    nav = "".join(f"<a href='#{sid}'>{title}</a>" for sid, title, _ in sec)
    body = "".join(f"<section id='{sid}'><h2>{title}</h2>{content}</section>" for sid, title, content in sec)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(name)} – Data Report</title>
<style>{CSS}</style></head><body><main>
<header><button class="toggle" id="theme" type="button">Theme</button><h1>{_e(name)}</h1>
<p>Data cleaning &amp; descriptive report · generated {_e(ctx.started.replace('T', ' '))} ·
input <code>{_e(Path(ctx.config['input']['data']).name)}</code></p></header>
<div class="tiles">{tiles_html}</div><nav>{nav}</nav>{body}
<footer>Generated by health-survey-pipeline. Descriptive output only – review before use in publications.</footer>
</main><script>{JS}</script></body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path


def _log_frame(ctx: Context) -> pd.DataFrame:
    rows = []
    for e in ctx.log:
        extra = "; ".join(f"{k}={v}" for k, v in e.items() if k not in {"step", "detail"} and v not in (None, "", 0))
        rows.append({"step": e["step"], "detail": e["detail"], "counts": extra})
    return pd.DataFrame(rows)

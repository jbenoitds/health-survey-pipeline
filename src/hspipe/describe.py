"""Descriptive statistics, missingness, group comparisons ("Table 1"),
timepoint (pre/post) comparisons and the codebook."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from ._compat import pvalue
from .config import expand_list
from .context import REASON_BLANK, REASON_FORM, REASON_SKIPPED, Context

EXCLUDE_SUFFIXES = ("_nvalid", "_complete")


def _fmt_p(p: float) -> str:
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return ""
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _label(ctx: Context, col: str) -> str:
    m = ctx.meta.get(col)
    return m.label if m and m.label else col


def select_variables(ctx: Context) -> Dict[str, List[str]]:
    dcfg = ctx.config["describe"]
    cont = expand_list(dcfg.get("continuous"), ctx.timepoints)
    cat = expand_list(dcfg.get("categorical"), ctx.timepoints)
    if dcfg.get("variables", "auto") == "auto":
        for c in ctx.df.columns:
            if c in {ctx.id_col, ctx.event_col} or c in cont or c in cat or c.endswith(EXCLUDE_SUFFIXES):
                continue
            m = ctx.meta.get(c)
            if m is None:
                continue
            if m.kind == "numeric":
                cont.append(c)
            elif m.kind == "categorical":
                if ctx.df[c].nunique(dropna=True) <= int(dcfg.get("max_categories", 15)):
                    cat.append(c)
    else:
        for c in expand_list(dcfg["variables"], ctx.timepoints):
            if c not in ctx.df.columns or c in cont or c in cat:
                continue
            (cat if ctx.meta.get(c) and ctx.meta[c].kind == "categorical" else cont).append(c)
    return {"continuous": [c for c in cont if c in ctx.df.columns],
            "categorical": [c for c in cat if c in ctx.df.columns]}


def continuous_summary(ctx: Context, cols: List[str]) -> pd.DataFrame:
    rows = []
    for c in cols:
        x = pd.to_numeric(ctx.df[c], errors="coerce")
        n = int(x.notna().sum())
        rows.append({
            "variable": c, "label": _label(ctx, c), "n": n, "missing": int(x.isna().sum()),
            "mean": x.mean(), "sd": x.std(ddof=1), "median": x.median(),
            "q1": x.quantile(0.25), "q3": x.quantile(0.75), "min": x.min(), "max": x.max(),
            "skew": x.skew() if n > 2 else np.nan,
        })
    return pd.DataFrame(rows)


def categorical_summary(ctx: Context, cols: List[str]) -> pd.DataFrame:
    rows = []
    for c in cols:
        s = ctx.df[c]
        labels = ctx.meta[c].value_labels if c in ctx.meta else {}
        valid = s.dropna()
        counts = valid.value_counts().sort_index()
        levels = list(labels) if labels else list(counts.index)
        for lev in list(dict.fromkeys(levels + [v for v in counts.index if v not in levels])):
            n = int(counts.get(lev, 0))
            rows.append({"variable": c, "label": _label(ctx, c), "code": lev,
                         "level": labels.get(lev, str(lev)) if labels else str(lev),
                         "n": n, "percent_valid": 100 * n / len(valid) if len(valid) else np.nan})
        rows.append({"variable": c, "label": _label(ctx, c), "code": "", "level": "(missing)",
                     "n": int(s.isna().sum()), "percent_valid": np.nan})
    return pd.DataFrame(rows)


def missingness(ctx: Context) -> pd.DataFrame:
    df, r = ctx.df, ctx.reasons
    rows = []
    n = len(df)
    for c in df.columns:
        if c == ctx.id_col:
            continue
        miss = df[c].isna() | (df[c].astype(str) == "") if df[c].dtype == object else df[c].isna()
        reasons = r[c][miss] if c in r.columns else pd.Series(dtype=object)
        reasons = reasons.replace("", REASON_BLANK)
        vc = reasons.value_counts()
        skipped = int(vc.get(REASON_SKIPPED, 0))
        not_started = int(vc.get(REASON_FORM, 0))
        applicable = n - skipped
        unexpected = int(miss.sum()) - skipped
        row = {"variable": c, "label": _label(ctx, c),
               "source": ctx.meta[c].source if c in ctx.meta else "raw", "n_rows": n, "n_present": int((~miss).sum()),
               "n_missing": int(miss.sum()), "skipped_by_design": skipped, "form_not_started": not_started,
               "pct_missing_of_applicable": (100 * unexpected / applicable) if applicable else np.nan,
               # item non-response: among rows where the form was started and the item was shown
               "pct_item_nonresponse": (100 * (unexpected - not_started) / (applicable - not_started))
               if applicable - not_started > 0 else np.nan}
        for reason, k in vc.items():
            if reason not in {REASON_SKIPPED, REASON_FORM}:
                row[reason] = int(k)
        rows.append(row)
    out = pd.DataFrame(rows).fillna({k: 0 for k in []})
    for col in out.columns:
        if col not in {"variable", "label", "source", "pct_missing_of_applicable", "pct_item_nonresponse"}:
            out[col] = out[col].fillna(0).astype(int)
    return out.sort_values(["pct_item_nonresponse", "pct_missing_of_applicable"], ascending=False, kind="stable").reset_index(drop=True)


def table_one(ctx: Context, sel: Dict[str, List[str]], group: Optional[str]) -> pd.DataFrame:
    df = ctx.df
    if group and group not in df.columns:
        ctx.step("table1_warning", f"group_by '{group}' not found - Table 1 is ungrouped")
        group = None
    g_labels = (ctx.meta[group].value_labels if group and group in ctx.meta else {}) or {}
    groups = sorted(df[group].dropna().unique()) if group else []
    gname = {g: g_labels.get(g, str(g)) for g in groups}
    heads = ["Overall"] + [gname[g] for g in groups]
    n_row = {"Characteristic": "N", "Overall": str(len(df))}
    for g in groups:
        n_row[gname[g]] = str(int((df[group] == g).sum()))
    rows = [n_row]
    do_tests = bool(ctx.config["describe"].get("tests", True)) and len(groups) >= 2

    for c in sel["continuous"]:
        if c == group:
            continue
        x = pd.to_numeric(df[c], errors="coerce")
        row = {"Characteristic": f"{_label(ctx, c)}, mean (SD)", "Overall": _msd(x)}
        samples = []
        for g in groups:
            xs = x[df[group] == g].dropna()
            row[gname[g]] = _msd(xs)
            samples.append(xs)
        if do_tests and all(len(s) >= 2 for s in samples):
            if len(samples) == 2:
                p = pvalue(stats.ttest_ind(samples[0], samples[1], equal_var=False))
                row["test"] = "Welch t-test"
            else:
                p = pvalue(stats.f_oneway(*samples))
                row["test"] = "One-way ANOVA"
            row["p"] = _fmt_p(p)
        row["n (non-missing)"] = str(int(x.notna().sum()))
        rows.append(row)

    for c in sel["categorical"]:
        if c == group:
            continue
        s = df[c]
        labels = ctx.meta[c].value_labels if c in ctx.meta else {}
        head = {"Characteristic": f"{_label(ctx, c)}, n (%)", "n (non-missing)": str(int(s.notna().sum()))}
        if do_tests:
            sub = df[[c, group]].dropna()
            ct = pd.crosstab(sub[c], sub[group])
            if ct.shape[0] >= 2 and ct.shape[1] >= 2:
                expected = stats.contingency.expected_freq(ct.values)
                if ct.shape == (2, 2) and (expected < 5).any():
                    p = pvalue(stats.fisher_exact(ct.values))
                    head["test"] = "Fisher exact"
                else:
                    p = pvalue(stats.chi2_contingency(ct.values, correction=False))
                    head["test"] = "Chi-square" + (" (some expected <5)" if (expected < 5).any() else "")
                head["p"] = _fmt_p(p)
        rows.append(head)
        levels = list(labels) if labels else sorted(s.dropna().unique())
        for lev in levels:
            row = {"Characteristic": f"    {labels.get(lev, lev) if labels else lev}",
                   "Overall": _npct(s, lev)}
            for g in groups:
                row[gname[g]] = _npct(s[df[group] == g], lev)
            rows.append(row)
    out = pd.DataFrame(rows)
    cols = ["Characteristic"] + heads + [c for c in ("test", "p", "n (non-missing)") if c in out.columns]
    return out.reindex(columns=cols).fillna("")


def _msd(x: pd.Series) -> str:
    x = x.dropna()
    if x.empty:
        return ""
    return f"{x.mean():.2f} ({x.std(ddof=1):.2f})" if len(x) > 1 else f"{x.mean():.2f}"


def _npct(s: pd.Series, lev) -> str:
    valid = s.dropna()
    if valid.empty:
        return ""
    n = int((valid == lev).sum())
    return f"{n} ({100 * n / len(valid):.1f}%)"


def timepoint_comparisons(ctx: Context) -> pd.DataFrame:
    tps = ctx.timepoints
    dcfg = ctx.config["describe"]
    if len(tps) < 2 or not dcfg.get("compare_timepoints", True):
        return pd.DataFrame()
    templates = list(dcfg.get("compare") or [])
    if not templates:
        templates = [sc["name"] for sc in ctx.config.get("scales") or [] if "{t}" in sc["name"]]
        templates += [d["name"] for d in ctx.config.get("derive") or [] if "{t}" in d.get("name", "")]
    rows = []
    first = tps[0]
    for tpl in templates:
        a = tpl.replace("{t}", first)
        for later in tps[1:]:
            b = tpl.replace("{t}", later)
            if a not in ctx.df.columns or b not in ctx.df.columns:
                continue
            pair = ctx.df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
            row = {"variable": tpl.replace("{t}", "").strip("_"), "comparison": f"{first} vs {later}",
                   "n_pairs": len(pair)}
            if len(pair) >= 3:
                d = pair[b] - pair[a]
                t = stats.ttest_rel(pair[b], pair[a])
                try:
                    w = pvalue(stats.wilcoxon(pair[b], pair[a])) if (d != 0).any() else np.nan
                except ValueError:
                    w = np.nan
                row.update({f"mean_{first}": pair[a].mean(), f"mean_{later}": pair[b].mean(),
                            "mean_change": d.mean(), "sd_change": d.std(ddof=1), "t": t.statistic,
                            "p_paired_t": pvalue(t), "p_wilcoxon": w,
                            "cohens_dz": d.mean() / d.std(ddof=1) if d.std(ddof=1) > 0 else np.nan})
            rows.append(row)
    return pd.DataFrame(rows)


def codebook(ctx: Context) -> pd.DataFrame:
    rows = []
    for c in ctx.df.columns:
        m = ctx.meta.get(c)
        s = ctx.df[c]
        vl = m.value_labels if m else {}
        rows.append({
            "variable": c, "label": m.label if m else "", "type": m.kind if m else str(s.dtype),
            "source": m.source if m else "", "n_non_missing": int(s.notna().sum()),
            "n_unique": int(s.nunique(dropna=True)),
            "value_labels": " | ".join(f"{_code(k)} = {v}" for k, v in vl.items()) if vl else "",
            "min": _minmax(s, "min"), "max": _minmax(s, "max"),
        })
    return pd.DataFrame(rows)


def _code(k):
    try:
        f = float(k)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(k)


def _minmax(s: pd.Series, how: str):
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
        v = getattr(s, how)()
        if pd.isna(v):
            return ""
        return str(v.date()) if hasattr(v, "date") else (f"{v:g}" if isinstance(v, (float, int, np.floating)) else v)
    return ""


def describe_all(ctx: Context) -> None:
    sel = select_variables(ctx)
    ctx.tables["continuous"] = continuous_summary(ctx, sel["continuous"])
    ctx.tables["categorical"] = categorical_summary(ctx, sel["categorical"])
    ctx.tables["missingness"] = missingness(ctx)
    ctx.tables["table1"] = table_one(ctx, sel, ctx.config["describe"].get("group_by"))
    ctx.tables["timepoints"] = timepoint_comparisons(ctx)
    ctx.tables["codebook"] = codebook(ctx)
    ctx.tables["issues"] = ctx.issues_frame()
    sc = pd.DataFrame([{k: v for k, v in s.items() if k not in {"subscales", "flags"}} for s in ctx.scales])
    ctx.tables["scales"] = sc
    subs = [{"scale": s["scale"], "subscale": k, **v} for s in ctx.scales for k, v in s["subscales"].items()]
    ctx.tables["subscales"] = pd.DataFrame(subs)
    flags = [{"scale": s["scale"], **f} for s in ctx.scales for f in s["flags"]]
    ctx.tables["flags"] = pd.DataFrame(flags)
    ctx.step("describe", "Computed descriptive tables", continuous=len(sel["continuous"]),
             categorical=len(sel["categorical"]))

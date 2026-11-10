"""Scale / questionnaire scoring with reverse items, minimum-valid-item rules,
prorating, subscales, cut-off categories, clinical safety flags and Cronbach's alpha.

Built-in instrument definitions contain only scoring metadata (number of items,
response range, reverse-keyed items, published cut-offs) – never item wording.
Always confirm the scoring rules against the manual/version you administered.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .config import ConfigError
from .context import REASON_FORM, REASON_INVALID, Context

INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "phq9": {
        "label": "PHQ-9 depression severity", "n_items": 9, "item_range": [0, 3], "method": "sum",
        "min_valid": 8, "prorate": True,
        "cutoffs": [[0, "Minimal"], [5, "Mild"], [10, "Moderate"], [15, "Moderately severe"], [20, "Severe"]],
        "flags": [{"item": 9, "op": ">", "value": 0, "suffix": "item9_flag",
                   "label": "PHQ-9 item 9 endorsed (thoughts of self-harm) - follow safety protocol"}],
    },
    "phq2": {"label": "PHQ-2 depression screen", "n_items": 2, "item_range": [0, 3], "method": "sum",
             "min_valid": 2, "cutoffs": [[0, "Negative"], [3, "Positive"]]},
    "gad7": {
        "label": "GAD-7 anxiety severity", "n_items": 7, "item_range": [0, 3], "method": "sum",
        "min_valid": 6, "prorate": True,
        "cutoffs": [[0, "Minimal"], [5, "Mild"], [10, "Moderate"], [15, "Severe"]],
    },
    "pss10": {
        "label": "Perceived Stress Scale (10-item)", "n_items": 10, "item_range": [0, 4], "method": "sum",
        "reverse": [4, 5, 7, 8], "min_valid": 9, "prorate": True,
        "cutoffs": [[0, "Low"], [14, "Moderate"], [27, "High"]],
    },
    "epds": {
        "label": "Edinburgh Postnatal Depression Scale", "n_items": 10, "item_range": [0, 3], "method": "sum",
        # Assumes each item is coded 0-3 in the order the options appear on the form.
        # If your export already stores item scores, set `reverse: []` in your config.
        "reverse": [3, 5, 6, 7, 8, 9, 10], "min_valid": 9, "prorate": True,
        "cutoffs": [[0, "Below threshold"], [13, "Probable depression"]],
        "flags": [{"item": 10, "op": ">", "value": 0, "suffix": "item10_flag",
                   "label": "EPDS item 10 endorsed (self-harm) - follow safety protocol"}],
    },
    "who5": {
        "label": "WHO-5 Well-Being Index (0-100)", "n_items": 5, "item_range": [0, 5], "method": "sum",
        "multiplier": 4, "min_valid": 5, "cutoffs": [[0, "Poor well-being"], [52, "Adequate"]],
    },
    "k10": {
        "label": "Kessler Psychological Distress Scale (K10)", "n_items": 10, "item_range": [1, 5],
        "method": "sum", "min_valid": 9, "prorate": True,
        "cutoffs": [[10, "Low"], [16, "Moderate"], [22, "High"], [30, "Very high"]],
    },
}


def cronbach_alpha(items: pd.DataFrame) -> tuple:
    x = items.apply(pd.to_numeric, errors="coerce").dropna()
    k = x.shape[1]
    if k < 2 or len(x) < 3:
        return (np.nan, len(x))
    item_var = x.var(axis=0, ddof=1).sum()
    total_var = x.sum(axis=1).var(ddof=1)
    if total_var == 0:
        return (np.nan, len(x))
    return (float(k / (k - 1) * (1 - item_var / total_var)), len(x))


def _resolve_items(sc: Dict[str, Any], n_items: Optional[int], tp: Optional[str]) -> List[str]:
    items = sc.get("items")
    if isinstance(items, str):
        if "{i}" not in items:
            raise ConfigError(f"scale '{sc['name']}': items pattern must contain {{i}} (e.g. phq9_{{i}}_{{t}})")
        if not n_items:
            raise ConfigError(f"scale '{sc['name']}': set n_items to use an items pattern")
        start = int(sc.get("item_start", 1))
        items = [items.replace("{i}", str(i)) for i in range(start, start + n_items)]
    if not items:
        raise ConfigError(f"scale '{sc['name']}' has no items")
    if tp is not None:
        items = [str(it).replace("{t}", tp) for it in items]
    elif any("{t}" in str(it) for it in items):
        raise ConfigError(f"scale '{sc['name']}': items use {{t}} so the name must too (e.g. phq9_{{t}})")
    return [str(it) for it in items]


def _positions(spec, items: List[str], key: str) -> List[str]:
    out = []
    for r in spec or []:
        if isinstance(r, int) or (isinstance(r, str) and r.isdigit()):
            idx = int(r) - 1
            if idx < 0 or idx >= len(items):
                raise ConfigError(f"{key}: item number {r} out of range 1..{len(items)}")
            out.append(items[idx])
        else:
            out.append(str(r))
    return out


def _score(df: pd.DataFrame, items: List[str], method: str, min_valid, prorate: bool,
           multiplier: float = 1.0):
    block = df[items]
    nvalid = block.notna().sum(axis=1)
    k = len(items)
    need = int(np.ceil(min_valid * k)) if isinstance(min_valid, float) and min_valid <= 1 else int(min_valid or k)
    if method == "mean":
        score = block.mean(axis=1)
    elif method == "sum":
        score = block.mean(axis=1) * k if prorate else block.sum(axis=1, min_count=1)
        if not prorate:
            score = score.where(nvalid == k)  # strict SPSS-style "a + b + c": any missing -> missing
            need = k
    else:
        raise ConfigError(f"Unknown scoring method '{method}' (use sum or mean)")
    score = score.where(nvalid >= need) * multiplier
    return score, nvalid, need


def _categorise(score: pd.Series, cutoffs) -> tuple:
    cutoffs = sorted([(float(c[0]), str(c[1])) for c in cutoffs], key=lambda t: t[0])
    cat = pd.Series(np.nan, index=score.index)
    for code, (lo, _lab) in enumerate(cutoffs, start=1):
        cat = cat.where(~(score >= lo), code)
    labels = {float(code): lab for code, (_lo, lab) in enumerate(cutoffs, start=1)}
    return cat, labels


def score_scales(ctx: Context) -> None:
    for sc in ctx.config.get("scales") or []:
        base = dict(INSTRUMENTS.get(sc.get("instrument", ""), {}))
        if sc.get("instrument") and not base:
            raise ConfigError(f"Unknown instrument '{sc['instrument']}'. Built-ins: {sorted(INSTRUMENTS)}")
        spec = {**base, **sc}
        name_tpl = spec["name"]
        tps = ctx.timepoints if "{t}" in name_tpl else [None]
        for tp in tps:
            ctx.df = ctx.df.copy()  # de-fragment before adding columns
            name = name_tpl.replace("{t}", tp) if tp else name_tpl
            items = _resolve_items(spec, spec.get("n_items"), tp)
            missing = [c for c in items if c not in ctx.df.columns]
            if missing:
                if spec.get("skip_if_missing", True) and len(missing) == len(items):
                    ctx.step("scale_skipped", f"{name}: none of the items are in the data")
                    continue
                raise ConfigError(f"scale '{name}': items not found in data: {missing}")
            lo, hi = (spec.get("item_range") or [None, None])
            block = ctx.df[items].apply(pd.to_numeric, errors="coerce")
            # out-of-range items -> issue + missing (they would silently distort the total)
            if lo is not None and hi is not None:
                for c in items:
                    bad = block[c].notna() & ((block[c] < lo) | (block[c] > hi))
                    for idx in ctx.df.index[bad]:
                        ctx.issue(idx, c, block.at[idx, c], "scale:item_range",
                                  f"{name}: item outside {lo}-{hi}", "error", "set to missing")
                    block.loc[bad, c] = np.nan
                    ctx.df.loc[bad, c] = np.nan
                    ctx.reasons.loc[bad, c] = REASON_INVALID
            reverse = _positions(spec.get("reverse"), items, f"{name}.reverse")
            for c in reverse:
                if lo is None or hi is None:
                    raise ConfigError(f"scale '{name}': item_range is needed to reverse-score items")
                block[c] = (lo + hi) - block[c]
            if spec.get("keep_recoded_items"):
                for c in reverse:
                    ctx.df[f"{c}_r"] = block[c]
                    ctx.reasons[f"{c}_r"] = ctx.reasons[c]
                    ctx.set_meta(f"{c}_r", kind="numeric", label=f"{c} (reverse-scored)", source="scale")

            method = spec.get("method", "sum")
            prorate = bool(spec.get("prorate", False))
            score, nvalid, need = _score(block, items, method, spec.get("min_valid", len(items)), prorate,
                                         float(spec.get("multiplier", 1)))
            if spec.get("round") is not None:
                score = score.round(int(spec["round"]))
            ctx.df[name] = score
            item_r = ctx.reasons[items]
            all_form = (item_r == REASON_FORM).all(axis=1)
            ctx.reasons[name] = np.where(score.notna(), "",
                                         np.where(all_form, REASON_FORM,
                                                  np.where(nvalid == 0, "no items answered", "insufficient items")))
            label = spec.get("label", name) + (f" [{tp}]" if tp else "")
            ctx.set_meta(name, kind="numeric", label=label, source="scale")
            if spec.get("keep_item_count", True):
                ctx.df[f"{name}_nvalid"] = nvalid
                ctx.reasons[f"{name}_nvalid"] = ""
                ctx.set_meta(f"{name}_nvalid", kind="numeric", label=f"{label}: items answered", source="scale")

            cats_name = None
            if spec.get("cutoffs"):
                cats_name = f"{name}_cat"
                cat, labels = _categorise(ctx.df[name], spec["cutoffs"])
                ctx.df[cats_name] = cat
                ctx.reasons[cats_name] = ctx.reasons[name]
                ctx.set_meta(cats_name, kind="categorical", label=f"{label}: category", value_labels=labels,
                             source="scale")

            subs = {}
            for sub_name, sub_items in (spec.get("subscales") or {}).items():
                sub_cols = _positions(sub_items, items, f"{name}.subscales.{sub_name}")
                full = f"{name}_{sub_name}"
                sub_min = spec.get("subscale_min_valid", spec.get("min_valid", len(items)))
                if isinstance(sub_min, int) and sub_min > len(sub_cols):
                    sub_min = len(sub_cols)
                s_score, _nv, _need = _score(block, sub_cols, spec.get("subscale_method", method), sub_min,
                                             prorate)
                ctx.df[full] = s_score
                ctx.reasons[full] = np.where(s_score.isna(), "insufficient items", "")
                ctx.set_meta(full, kind="numeric", label=f"{label}: {sub_name}", source="scale")
                a, _n = cronbach_alpha(block[sub_cols])
                subs[sub_name] = {"variable": full, "n_items": len(sub_cols), "alpha": a,
                                  "n": int(s_score.notna().sum())}

            flags = []
            for fl in spec.get("flags") or []:
                col = _positions([fl["item"]], items, f"{name}.flags")[0]
                raw = block[col]  # scored (post-reverse) value
                op = fl.get("op", ">")
                val = float(fl.get("value", 0))
                hit = {">": raw > val, ">=": raw >= val, "=": raw == val, "<": raw < val, "<=": raw <= val}[op]
                fname = f"{name}_{fl.get('suffix', 'flag')}"
                ctx.df[fname] = np.where(raw.isna(), np.nan, hit.astype(float))
                ctx.reasons[fname] = ctx.reasons[col].where(raw.isna(), "")
                ctx.set_meta(fname, kind="categorical", label=fl.get("label", fname),
                             value_labels={0.0: "No", 1.0: "Yes"}, source="scale")
                flags.append({"variable": fname, "label": fl.get("label", fname), "n_flagged": int(hit.sum())})

            alpha, n_alpha = cronbach_alpha(block)
            s = ctx.df[name]
            ctx.scales.append({
                "scale": name, "label": label, "instrument": spec.get("instrument", ""), "timepoint": tp or "",
                "n_items": len(items), "method": method + (" (prorated)" if prorate and method == "sum" else ""),
                "min_items": need, "reverse_items": ", ".join(reverse), "n_scored": int(s.notna().sum()),
                "n_insufficient": int((s.isna() & (nvalid > 0)).sum()), "n_no_items": int((nvalid == 0).sum()),
                "mean": s.mean(), "sd": s.std(ddof=1), "min": s.min(), "max": s.max(),
                "alpha": alpha, "alpha_n": n_alpha, "category_var": cats_name or "",
                "subscales": subs, "flags": flags,
            })
            ctx.step("score", f"Scored {name} ({len(items)} items, {method})", n_scored=int(s.notna().sum()),
                     alpha=None if np.isnan(alpha) else round(alpha, 3))

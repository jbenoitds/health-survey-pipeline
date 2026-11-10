"""Data validation: dictionary-driven checks, skip-logic, required fields,
duplicates, custom rules, and conversion to analysis types."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ._compat import parse_dates
from .branching import BranchingError, compile_logic
from .config import expand_list
from .context import REASON_BLANK, REASON_FORM, REASON_INVALID, REASON_SKIPPED, Context
from .dictionary import VariableSpec
from .missing import _canon

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
REDCAP_SYSTEM = {"redcap_event_name", "redcap_repeat_instrument", "redcap_repeat_instance",
                 "redcap_survey_identifier", "redcap_data_access_group"}
COMPLETE_LABELS = {0.0: "Incomplete", 1.0: "Unverified", 2.0: "Complete"}


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------

def to_number(s: pd.Series) -> pd.Series:
    cleaned = s.astype(str).str.replace(",", ".", regex=False).where(s != "", np.nan)
    return pd.to_numeric(cleaned, errors="coerce")


def to_date(s: pd.Series, validation: str = "") -> pd.Series:
    return parse_dates(s, dayfirst="dmy" in validation)


def _bound(v: Optional[str], kind: str):
    if v is None or str(v).strip() == "":
        return None
    v = str(v).strip()
    if kind in {"date", "datetime"}:
        if v.lower() in {"today", "now"}:
            return pd.Timestamp.now().normalize() + (pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        return pd.to_datetime(v, errors="coerce")
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# helpers to raise issues for a mask
# ---------------------------------------------------------------------------

def _flag(ctx: Context, col: str, mask: pd.Series, rule: str, message: str,
          severity: str = "error", remove: bool = True) -> int:
    mask = mask.fillna(False).astype(bool)
    n = int(mask.sum())
    if not n:
        return 0
    action = "set to missing" if (remove and ctx.config["validation"]["on_invalid"] == "set_missing") else "flagged"
    for idx in ctx.df.index[mask]:
        ctx.issue(idx, col, ctx.df.at[idx, col], rule, message, severity, action)
    if action == "set to missing":
        if pd.api.types.is_numeric_dtype(ctx.df[col]) or pd.api.types.is_datetime64_any_dtype(ctx.df[col]):
            ctx.df.loc[mask, col] = np.nan
        else:
            ctx.df.loc[mask, col] = ""
        ctx.reasons.loc[mask, col] = REASON_INVALID
    return n


# ---------------------------------------------------------------------------
# dictionary checks (string level)
# ---------------------------------------------------------------------------

def dictionary_checks(ctx: Context) -> None:
    dd = ctx.dictionary
    df = ctx.df
    n_issues_before = len(ctx.issues)
    missing_cols, extra_cols = [], []
    if dd is not None:
        expected = {c for s in dd.specs.values() for c in s.data_columns}
        missing_cols = sorted(c for c in expected if c not in df.columns)
        complete_cols = {f"{f}_complete" for f in dd.forms()}
        extra_cols = sorted(c for c in df.columns if c not in expected and c not in REDCAP_SYSTEM
                            and c not in complete_cols and not c.endswith("_timestamp")
                            and c not in {ctx.id_col, ctx.event_col})
    if missing_cols:
        ctx.step("dictionary_columns", "Dictionary fields absent from the export (not an error if the export "
                 "was a subset)", n_absent=len(missing_cols), absent=", ".join(missing_cols[:30]))
    if extra_cols:
        ctx.step("dictionary_columns", "Columns in the export with no dictionary entry (types inferred)",
                 n_extra=len(extra_cols), extra=", ".join(extra_cols[:30]))

    for col in df.columns:
        spec = dd.spec_for_column(col) if dd is not None else None
        if spec is None:
            continue
        s = df[col]
        present = s != ""
        if not present.any():
            continue
        kind = spec.kind
        if kind == "numeric":
            num = to_number(s)
            _flag(ctx, col, present & num.isna(), "type:numeric", f"'{col}' expects a number")
            if spec.is_integer:
                _flag(ctx, col, present & num.notna() & (num % 1 != 0), "type:integer", f"'{col}' expects an integer")
            lo, hi = _bound(spec.vmin, kind), _bound(spec.vmax, kind)
            if spec.ftype == "slider":
                lo = 0 if lo is None else lo
                hi = 100 if hi is None else hi
            num = to_number(df[col])
            if lo is not None:
                _flag(ctx, col, num < lo, "range:min", f"'{col}' below minimum {lo:g}")
            if hi is not None:
                _flag(ctx, col, num > hi, "range:max", f"'{col}' above maximum {hi:g}")
        elif kind in {"date", "datetime"}:
            d = to_date(s, spec.validation)
            _flag(ctx, col, present & d.isna(), "type:date", f"'{col}' is not a valid date")
            lo, hi = _bound(spec.vmin, kind), _bound(spec.vmax, kind)
            d = to_date(df[col], spec.validation)
            if lo is not None and not pd.isna(lo):
                _flag(ctx, col, d < lo, "range:min", f"'{col}' before {lo.date()}")
            if hi is not None and not pd.isna(hi):
                _flag(ctx, col, d > hi, "range:max", f"'{col}' after {hi.date()}")
        elif kind == "categorical" and spec.choices:
            allowed = {_canon(k) for k in spec.choices}
            _flag(ctx, col, present & ~s.map(_canon).isin(allowed), "choices",
                  f"'{col}' value not among the coded choices ({', '.join(sorted(allowed))})")
        elif kind == "checkbox":
            _flag(ctx, col, present & ~s.map(_canon).isin({"0", "1"}), "checkbox", f"'{col}' must be 0 or 1")
        elif kind == "text" and spec.validation == "email":
            _flag(ctx, col, present & ~s.str.match(EMAIL_RE), "type:email", f"'{col}' is not a valid email",
                  severity="warning", remove=False)
    ctx.step("validate_dictionary", "Type, range and coded-choice checks from the data dictionary",
             issues=len(ctx.issues) - n_issues_before)


def form_status(ctx: Context) -> Dict[str, pd.Series]:
    """Per form: True where the row has *any* data for that form."""
    dd = ctx.dictionary
    out = {}
    if dd is None:
        return out
    df = ctx.df
    for form, cols in dd.forms().items():
        signal = []
        for c in cols:
            if c not in df.columns or c in {ctx.id_col, ctx.event_col}:
                continue
            spec = dd.spec_for_column(c)
            if spec.ftype == "calc":
                continue
            if spec.kind == "checkbox":
                signal.append(df[c] == "1")
            else:
                signal.append(df[c] != "")
        if signal:
            out[form] = pd.concat(signal, axis=1).any(axis=1)
    return out


def skip_logic_checks(ctx: Context) -> None:
    """Classify blanks as 'skipped by design' / 'form not started' and flag answers that
    exist even though the branching logic says the question should have been hidden."""
    dd = ctx.dictionary
    if dd is None:
        return
    df, reasons = ctx.df, ctx.reasons
    vcfg = ctx.config["validation"]
    started = form_status(ctx)
    n_skipped = n_form = n_unsupported = n_answered_hidden = n_required = 0
    unsupported: List[str] = []

    for spec in dd.specs.values():
        cols = [c for c in spec.data_columns if c in df.columns]
        if not cols:
            continue
        form_ok = started.get(spec.form, pd.Series(True, index=df.index))
        # form not started -> every cell of that form is "form not started"
        for c in cols:
            not_started = ~form_ok
            if spec.kind == "checkbox":
                reset = not_started & (df[c] == "0")
                df.loc[reset, c] = ""
            m = not_started & (df[c] == "")
            reasons.loc[m, c] = REASON_FORM
            n_form += int(m.sum())

        shown = pd.Series(True, index=df.index)
        if spec.branching and vcfg.get("check_branching", True):
            try:
                shown = compile_logic(spec.branching).evaluate(df)
            except BranchingError as err:
                n_unsupported += 1
                unsupported.append(f"{spec.name}: {err}")
                shown = None
        if shown is not None:
            hidden = ~shown & form_ok
            for c in cols:
                if spec.kind == "checkbox":
                    answered = hidden & (df[c] == "1")
                    reset = hidden & (df[c] == "0")
                    df.loc[reset, c] = ""
                else:
                    answered = hidden & (df[c] != "")
                n_answered_hidden += _flag(
                    ctx, c, answered, "branching",
                    f"'{c}' has a value but its branching logic ({spec.branching}) says it should be hidden",
                    severity="warning", remove=False)
                m = hidden & (df[c] == "")
                reasons.loc[m, c] = REASON_SKIPPED
                n_skipped += int(m.sum())
            if spec.required and vcfg.get("check_required", True):
                applicable = shown & form_ok
                if spec.kind == "checkbox":
                    empty = pd.concat([df[c] != "1" for c in cols], axis=1).all(axis=1)
                    target = cols[0]
                else:
                    empty = df[cols[0]] == ""
                    target = cols[0]
                miss = applicable & empty & (reasons[target].isin(["", REASON_BLANK]))
                n_required += _flag(ctx, target, miss, "required", f"Required field '{spec.name}' is empty",
                                    severity="warning", remove=False)
    if unsupported:
        ctx.step("branching_unsupported", "Branching logic that could not be evaluated (skip status unknown)",
                 n=n_unsupported, fields="; ".join(unsupported[:10]))
    ctx.step("skip_logic", "Classified blanks using branching logic and form completion",
             cells_skipped_by_design=n_skipped, cells_form_not_started=n_form,
             answered_but_hidden=n_answered_hidden, required_missing=n_required)


def duplicate_checks(ctx: Context) -> None:
    if not ctx.config["validation"].get("check_duplicates", True):
        return
    df = ctx.df
    keys = [ctx.id_col] + ([ctx.event_col] if ctx.event_col and ctx.event_col in df.columns else [])
    for extra in ("redcap_repeat_instrument", "redcap_repeat_instance"):
        if extra in df.columns:
            keys.append(extra)
    if ctx.id_col not in df.columns:
        raise ValueError(f"id_column '{ctx.id_col}' not found in data. Columns start with: {list(df.columns[:8])}")
    blank_id = df[ctx.id_col] == ""
    if blank_id.any():
        for idx in df.index[blank_id]:
            ctx.issue(idx, ctx.id_col, "", "id:blank", "Row has no record ID and was removed", "error", "row removed")
        ctx.df = df = df.loc[~blank_id]
        ctx.reasons = ctx.reasons.loc[df.index]
    dup = df.duplicated(keys, keep=False)
    policy = ctx.config["validation"].get("duplicates", "flag")
    removed = 0
    if dup.any():
        for idx in df.index[dup]:
            ctx.issue(idx, ctx.id_col, df.at[idx, ctx.id_col], "duplicate",
                      f"Duplicate key ({', '.join(keys)})", "error",
                      "flagged" if policy == "flag" else "deduplicated")
        if policy in {"keep_first", "keep_most_complete"}:
            if policy == "keep_most_complete":
                filled = (df != "").sum(axis=1)
                order = filled.sort_values(ascending=False, kind="stable").index
                keep_idx = df.loc[order].drop_duplicates(keys, keep="first").index
            else:
                keep_idx = df.drop_duplicates(keys, keep="first").index
            keep_idx = df.index[df.index.isin(keep_idx)]
            removed = len(df) - len(keep_idx)
            ctx.df = df.loc[keep_idx]
            ctx.reasons = ctx.reasons.loc[keep_idx]
    ctx.step("duplicates", f"Duplicate keys checked on {keys} (policy: {policy})",
             duplicate_rows=int(dup.sum()), rows_removed=removed, blank_ids_removed=int(blank_id.sum()))


# ---------------------------------------------------------------------------
# type conversion
# ---------------------------------------------------------------------------

def _infer_kind(col: str, s: pd.Series) -> str:
    vals = s[s != ""]
    if vals.empty:
        return "text"
    num = to_number(vals)
    if num.notna().mean() >= 0.9:
        return "numeric"
    if re.search(r"(date|dob|_dt$|time)", col, re.I):
        if to_date(vals).notna().mean() >= 0.9:
            return "date"
    return "text"


def coerce_types(ctx: Context) -> None:
    dd = ctx.dictionary
    df = ctx.df
    inferred = []
    for col in list(df.columns):
        if col in {ctx.id_col, ctx.event_col} or col in REDCAP_SYSTEM:
            ctx.set_meta(col, kind="text", label=col)
            continue
        spec: Optional[VariableSpec] = dd.spec_for_column(col) if dd is not None else None
        if spec is not None:
            kind = spec.kind
            label = dd.variable_labels().get(col, spec.label)
            vlabels = dd.value_labels().get(col, {})
            if kind == "categorical" and not spec.numeric_codes():
                kind = "categorical_text"  # letter codes (F/M): keep the strings, still a category
                vlabels = dict(spec.choices)
        elif col.endswith("_complete"):
            kind, label, vlabels = "categorical", f"Form status: {col[:-9]}", dict(COMPLETE_LABELS)
        elif col.endswith("_timestamp"):
            kind, label, vlabels = "datetime", col, {}
        else:
            kind, label, vlabels = _infer_kind(col, df[col]), col, {}
            inferred.append(f"{col}:{kind}")
        if vlabels and col in ctx.missing_code_values:  # e.g. 99 = Prefer not to answer -> now missing
            gone = ctx.missing_code_values[col]
            vlabels = {k: v for k, v in vlabels.items() if _canon(k) not in gone}
        s = df[col]
        present = s != ""
        if kind in {"numeric", "categorical", "checkbox"}:
            num = to_number(s)
            bad = present & num.isna()
            if bad.any():
                if spec is None:  # dictionary columns were already checked and reported
                    _flag(ctx, col, bad, "type:numeric", f"Non-numeric value in numeric column '{col}'",
                          severity="warning")
                ctx.reasons.loc[bad, col] = REASON_INVALID
                num = to_number(ctx.df[col])
            df[col] = num
        elif kind in {"date", "datetime"}:
            d = to_date(s, spec.validation if spec else "")
            bad = present & d.isna()
            if bad.any():
                if spec is None:
                    _flag(ctx, col, bad, "type:date", f"Unparseable date in '{col}'", severity="warning")
                ctx.reasons.loc[bad, col] = REASON_INVALID
            df[col] = to_date(ctx.df[col].astype(str), spec.validation if spec else "")
        else:
            df[col] = s.where(present, None).astype(object)
        kind = {"checkbox": "categorical", "categorical_text": "categorical"}.get(kind, kind)
        ctx.set_meta(col, kind=kind, label=label or col,
                     value_labels=vlabels)
    ctx.df = df
    if inferred:
        ctx.step("infer_types", "Types inferred for columns without dictionary entries",
                 n=len(inferred), columns=", ".join(inferred[:40]))
    ctx.step("coerce_types", "Converted columns to analysis types (numbers, dates, text)",
             numeric=sum(m.kind in {"numeric", "categorical"} for m in ctx.meta.values()),
             dates=sum(m.kind in {"date", "datetime"} for m in ctx.meta.values()))


# ---------------------------------------------------------------------------
# custom rules (typed data)
# ---------------------------------------------------------------------------

def _eval_expr(ctx: Context, expr: str) -> pd.Series:
    if "[" in expr:  # REDCap-style syntax: evaluate on a string view
        view = ctx.df.astype(object).where(ctx.df.notna(), "").astype(str)
        for c in view.columns:
            if pd.api.types.is_datetime64_any_dtype(ctx.df[c]):
                view[c] = ctx.df[c].dt.strftime("%Y-%m-%d").fillna("")
            elif pd.api.types.is_numeric_dtype(ctx.df[c]):
                view[c] = ctx.df[c].map(lambda v: "" if pd.isna(v) else (f"{v:g}"))
        return compile_logic(expr).evaluate(view)
    return ctx.df.eval(expr, engine="python")


def custom_rules(ctx: Context) -> None:
    rules = ctx.config["validation"].get("rules") or []
    before = len(ctx.issues)
    for i, rule in enumerate(rules):
        sev = rule.get("severity", "error")
        if "check" in rule:
            for expr in expand_list(rule["check"], ctx.timepoints):
                found = {c: m.start() for c in ctx.df.columns
                         for m in [re.search(rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])", expr)] if m}
                cols = sorted(found, key=found.get)  # in order of appearance
                try:
                    ok = _eval_expr(ctx, expr)
                except Exception as err:  # noqa: BLE001 - report config problems clearly
                    raise ValueError(f"validation.rules[{i}] check '{expr}' failed: {err}") from err
                ok = pd.Series(ok, index=ctx.df.index).astype("boolean")
                all_present = ctx.df[cols].notna().all(axis=1) if cols else pd.Series(True, index=ctx.df.index)
                fail = all_present & ~ok.fillna(True).astype(bool)
                target = rule.get("variable") or (cols[0] if cols else "(row)")
                msg = rule.get("message", f"Check failed: {expr}")
                if target in ctx.df.columns:
                    _flag(ctx, target, fail, rule.get("name", "check"), msg, sev,
                          remove=rule.get("action") == "set_missing")
                else:
                    for idx in ctx.df.index[fail]:
                        ctx.issue(idx, target, "", rule.get("name", "check"), msg, sev)
            continue
        variables = expand_list(rule.get("variable") or rule.get("variables"), ctx.timepoints)
        remove = rule.get("action", "set_missing") == "set_missing"
        for v in variables:
            if v not in ctx.df.columns:
                ctx.step("rule_skipped", f"validation.rules[{i}]: column '{v}' not found")
                continue
            s = ctx.df[v]
            name = rule.get("name")
            if "range" in rule or "min" in rule or "max" in rule:
                lo, hi = rule.get("range", [rule.get("min"), rule.get("max")])
                if pd.api.types.is_datetime64_any_dtype(s):
                    lo = pd.to_datetime(lo) if lo is not None else None
                    hi = pd.to_datetime(hi) if hi is not None else None
                if lo is not None:
                    _flag(ctx, v, s < lo, name or "range:min", rule.get("message", f"'{v}' below {lo}"), sev, remove)
                if hi is not None:
                    _flag(ctx, v, ctx.df[v] > hi, name or "range:max", rule.get("message", f"'{v}' above {hi}"),
                          sev, remove)
            if "allowed" in rule:
                allowed = {_canon(a) for a in rule["allowed"]}
                bad = ctx.df[v].notna() & ~ctx.df[v].map(lambda x: _canon(x) if pd.notna(x) else "").isin(allowed)
                _flag(ctx, v, bad, name or "allowed", rule.get("message", f"'{v}' not in allowed values"), sev, remove)
            if "regex" in rule:
                pat = re.compile(rule["regex"])
                st = ctx.df[v].astype(str)
                bad = ctx.df[v].notna() & ~st.map(lambda x: bool(pat.fullmatch(x)))
                _flag(ctx, v, bad, name or "regex", rule.get("message", f"'{v}' does not match pattern"), sev, remove)
            if rule.get("required"):
                miss = ctx.df[v].isna() & ctx.reasons[v].isin(["", REASON_BLANK])
                _flag(ctx, v, miss, name or "required", rule.get("message", f"'{v}' is required"), sev, False)
            if rule.get("outliers"):
                k = float(rule.get("outliers"))
                x = pd.to_numeric(ctx.df[v], errors="coerce")
                z = (x - x.mean()) / x.std(ddof=1)
                _flag(ctx, v, z.abs() > k, name or "outlier",
                      rule.get("message", f"'{v}' more than {k:g} SD from the mean"), "warning",
                      rule.get("action") == "set_missing")
    ctx.step("validate_rules", "Custom validation rules from config", rules=len(rules),
             issues=len(ctx.issues) - before)

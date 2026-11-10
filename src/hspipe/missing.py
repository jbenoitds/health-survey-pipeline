"""Standardisation of raw text and handling of missing-value codes."""
from __future__ import annotations

from typing import Dict, Iterable

import numpy as np
import pandas as pd

from .config import expand_list
from .context import REASON_BLANK, Context


def _canon(v) -> str:
    """Canonical string for code matching: -99, '-99', '-99.0' all -> '-99'."""
    s = str(v).strip()
    try:
        f = float(s)
        if np.isfinite(f) and f.is_integer():
            return str(int(f))
        return repr(f)
    except ValueError:
        return s


def standardize(ctx: Context) -> None:
    """Trim whitespace, normalise non-breaking spaces, drop fully empty columns/rows."""
    df = ctx.df
    df.columns = [str(c).strip() for c in df.columns]
    dupes = df.columns[df.columns.duplicated()].tolist()
    if dupes:
        raise ValueError(f"Duplicate column names in input: {dupes}")
    changed = 0
    for c in df.columns:
        s = df[c].astype(str)
        new = s.str.replace(" ", " ", regex=False).str.strip()
        changed += int((new != s).sum())
        df[c] = new
    empty_rows = df.apply(lambda r: (r == "").all(), axis=1)
    if empty_rows.any():
        df = df.loc[~empty_rows]
    drop = [c for c in ctx.config["input"].get("drop_columns", []) if c in df.columns]
    if drop:
        df = df.drop(columns=drop)
    ctx.df = df
    ctx.reasons = pd.DataFrame("", index=df.index, columns=df.columns, dtype=object)
    ctx.step("standardize", "Trimmed whitespace, removed empty rows and configured drop_columns",
             cells_trimmed=changed, empty_rows_removed=int(empty_rows.sum()), columns_dropped=len(drop))


def apply_missing_codes(ctx: Context) -> None:
    mcfg = ctx.config["missing"]
    df, reasons = ctx.df, ctx.reasons
    plain = {_canon(c) for c in mcfg.get("codes", [])} | {""}
    labelled: Dict[str, str] = {_canon(k): str(v) for k, v in (mcfg.get("labelled_codes") or {}).items()}
    per_var: Dict[str, Dict[str, str]] = {}
    for var, codes in (mcfg.get("variable_codes") or {}).items():
        for v in expand_list(var, ctx.timepoints):
            per_var[v] = {_canon(k): str(lab) for k, lab in codes.items()}

    apply_to = mcfg.get("apply_to", "all")
    targets: Iterable[str] = df.columns if apply_to == "all" else expand_list(apply_to, ctx.timepoints)
    protected = {ctx.id_col, ctx.event_col}
    counts: Dict[str, int] = {}
    for c in targets:
        if c not in df.columns or c in protected:
            continue
        canon = df[c].map(_canon)
        codes = {**labelled, **per_var.get(c, {})}
        for code, label in codes.items():
            hit = canon == code
            if hit.any():
                reasons.loc[hit, c] = f"code: {label}"
                df.loc[hit, c] = ""
                counts[label] = counts.get(label, 0) + int(hit.sum())
            ctx.missing_code_values.setdefault(c, set()).add(code)
        hit = canon.isin(plain) & (df[c] != "")
        if hit.any():
            df.loc[hit, c] = ""
            counts["generic NA string"] = counts.get("generic NA string", 0) + int(hit.sum())
    # anything still blank with no recorded reason is a plain blank
    blank = (df == "") & (reasons == "")
    reasons[blank] = REASON_BLANK
    ctx.step("missing_codes", "Converted missing-value codes to empty cells (reason kept for the missingness report)",
             **{f"n_{k}": v for k, v in counts.items()})

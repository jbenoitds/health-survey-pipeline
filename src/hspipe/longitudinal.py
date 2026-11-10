"""Long (one row per event) to wide (one row per participant) reshaping."""
from __future__ import annotations

import pandas as pd

from .context import REASON_FORM, Context


def to_wide(ctx: Context) -> None:
    lcfg = ctx.config["longitudinal"]
    if not lcfg.get("reshape"):
        return
    ev = ctx.event_col
    if not ev or ev not in ctx.df.columns:
        raise ValueError("longitudinal.reshape needs input.event_column (e.g. redcap_event_name) present in the data")
    df, reasons = ctx.df, ctx.reasons
    emap = {str(k): str(v) for k, v in lcfg["event_map"].items()}
    sep = lcfg.get("suffix_sep", "_")

    for c in ("redcap_repeat_instrument", "redcap_repeat_instance"):
        if c in df.columns:
            rep = df[c].notna() & (df[c].astype(str) != "")
            if rep.any():
                ctx.step("reshape_warning", f"{int(rep.sum())} repeating-instrument rows dropped before reshaping "
                         "(export repeating instruments separately)")
                df, reasons = df.loc[~rep], reasons.loc[~rep]
            df, reasons = df.drop(columns=c), reasons.drop(columns=c)

    unmapped = sorted(set(df[ev].dropna().astype(str)) - set(emap))
    if unmapped and not lcfg.get("keep_unmapped_events"):
        ctx.step("reshape_warning", "Rows from unmapped events dropped", events=", ".join(unmapped),
                 rows=int(df[ev].astype(str).isin(unmapped).sum()))
    mask = df[ev].astype(str).isin(emap)
    df, reasons = df.loc[mask], reasons.loc[mask]
    suffix = df[ev].astype(str).map(emap)

    value_cols = [c for c in df.columns if c not in {ctx.id_col, ev}]
    invariant_cfg = lcfg.get("time_invariant", "auto")
    if invariant_cfg == "auto":
        invariant = []
        for c in value_cols:
            filled_events = suffix[df[c].notna()].nunique()
            if filled_events <= 1:
                invariant.append(c)
    else:
        invariant = [c for c in invariant_cfg if c in value_cols]
    varying = [c for c in value_cols if c not in invariant]

    ids = pd.Index(df[ctx.id_col].drop_duplicates())
    wide = pd.DataFrame({ctx.id_col: ids})
    wide_r = pd.DataFrame(index=wide.index)
    keyed = df.assign(_sfx=suffix.values)
    keyed_r = reasons.assign(_sfx=suffix.values, _id=df[ctx.id_col].values)

    # time-invariant: first non-missing value across events
    if invariant:
        first = keyed.groupby(ctx.id_col, sort=False)[invariant].first()
        for c in invariant:
            wide[c] = wide[ctx.id_col].map(first[c])
            # reason for a missing value: taken from the event where the field is collected
            collected = suffix[df[c].notna()].unique()
            src = keyed_r[keyed_r["_sfx"].isin(collected)] if len(collected) else keyed_r.iloc[0:0]
            rmap = src.drop_duplicates("_id").set_index("_id")[c] if not src.empty else pd.Series(dtype=object)
            r = wide[ctx.id_col].map(rmap).fillna(REASON_FORM if len(collected) else "blank")
            r = r.replace("", "blank")
            wide_r[c] = r.where(wide[c].isna(), "")
    order = list(dict.fromkeys(emap.values()))
    for sfx in order:
        part = keyed[keyed["_sfx"] == sfx].drop_duplicates(ctx.id_col).set_index(ctx.id_col)
        part_r = keyed_r[keyed_r["_sfx"] == sfx].drop_duplicates("_id").set_index("_id")
        present_ids = wide[ctx.id_col].isin(part.index)
        for c in varying:
            name = f"{c}{sep}{sfx}"
            wide[name] = wide[ctx.id_col].map(part[c]) if not part.empty else pd.NA
            r = wide[ctx.id_col].map(part_r[c]) if not part_r.empty else pd.Series(index=wide.index, dtype=object)
            r = r.where(present_ids, REASON_FORM)  # participant has no row for this event
            wide_r[name] = r.fillna("").where(wide[name].isna(), "")
            m = ctx.meta.get(c)
            if m is not None:
                ctx.set_meta(name, kind=m.kind, label=f"{m.label} [{sfx}]", value_labels=dict(m.value_labels),
                             source=m.source)
            if not pd.api.types.is_numeric_dtype(wide[name]) and pd.api.types.is_numeric_dtype(df[c]):
                wide[name] = pd.to_numeric(wide[name], errors="coerce")
    for c in varying:
        ctx.meta.pop(c, None)
    # drop suffixed columns that are entirely empty (field not collected at that event)
    empty = [c for c in wide.columns if c != ctx.id_col and wide[c].isna().all() and c not in invariant]
    wide = wide.drop(columns=empty)
    wide_r = wide_r.drop(columns=[c for c in empty if c in wide_r.columns])
    wide_r.insert(0, ctx.id_col, "")
    ctx.df, ctx.reasons = wide, wide_r[wide.columns]
    ctx.step("reshape", "Reshaped long (one row per event) to wide (one row per participant)",
             participants=len(wide), time_invariant=len(invariant), time_varying=len(varying),
             empty_event_columns_dropped=len(empty))
    ctx.config["input"]["event_column"] = None
    ctx.reshaped = True

"""Variable recoding and derived variables (the SPSS RECODE / COMPUTE equivalents)."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from .config import ConfigError, expand_list
from .context import Context


def _target(var: str, rule: Dict[str, Any]) -> str:
    if rule.get("into"):
        into = rule["into"]
        return into.replace("{var}", var) if "{var}" in into else into
    return var + rule.get("suffix", "")


def _num_key(k):
    try:
        f = float(k)
        return f
    except (TypeError, ValueError):
        return k


def recode(ctx: Context, after_derive: bool = False) -> None:
    """Apply recode rules. Rules with ``after_derive: true`` run after derived variables exist."""
    rules = ctx.config.get("recode") or []
    for i, rule in enumerate(rules):
        if bool(rule.get("after_derive", False)) != after_derive:
            continue
        variables = expand_list(rule.get("variables") or rule.get("variable"), ctx.timepoints)
        if not variables:
            raise ConfigError(f"recode[{i}] needs 'variable' or 'variables'.")
        if len(variables) > 1 and rule.get("into") and "{var}" not in rule["into"] and "{t}" not in str(rule.get("into")):
            raise ConfigError(f"recode[{i}]: 'into' must contain {{var}} when recoding several variables.")
        intos = expand_list(rule["into"], ctx.timepoints) if rule.get("into") and "{t}" in rule["into"] else None
        done = []
        for j, v in enumerate(variables):
            if v not in ctx.df.columns:
                ctx.step("recode_skipped", f"recode[{i}]: '{v}' not in data")
                continue
            s = ctx.df[v]
            tgt = intos[j] if intos else _target(v, rule)
            src_meta = ctx.meta.get(v)
            labels = {}
            if "reverse" in rule:
                lo, hi = rule["reverse"].get("min"), rule["reverse"].get("max")
                if lo is None or hi is None:
                    raise ConfigError(f"recode[{i}].reverse needs min and max")
                x = pd.to_numeric(s, errors="coerce")
                out = (lo + hi) - x
                out = out.where((x >= lo) & (x <= hi))
                kind = src_meta.kind if src_meta else "numeric"
                if src_meta and src_meta.value_labels:
                    labels = {(lo + hi) - float(k): lab for k, lab in src_meta.value_labels.items()}
            elif "map" in rule:
                mapping = {_num_key(k): v2 for k, v2 in rule["map"].items()}
                other = rule.get("else", "copy")
                keyed = s.map(lambda x: _num_key(x) if pd.notna(x) else x)
                out = keyed.map(lambda x: mapping.get(x, np.nan) if pd.notna(x) else np.nan)
                unmatched = keyed.notna() & ~keyed.isin(list(mapping))
                if other == "copy":
                    out = out.where(~unmatched, s)
                elif other not in ("missing", None):
                    out = out.where(~unmatched, other)
                try:
                    out = pd.to_numeric(out)
                    kind = "categorical"
                except (TypeError, ValueError):
                    out = out.astype(object)
                    kind = "text"
            elif "bins" in rule:
                bins = [float("inf") if str(b).lower() in {"inf", ".inf"} else
                        float("-inf") if str(b).lower() in {"-inf", "-.inf"} else float(b) for b in rule["bins"]]
                blabels = rule.get("labels")
                codes = list(range(1, len(bins)))
                out = pd.cut(pd.to_numeric(s, errors="coerce"), bins=bins, labels=codes,
                             right=rule.get("right", False), include_lowest=True).astype(float)
                if blabels:
                    labels = {float(c): str(lab) for c, lab in zip(codes, blabels)}
                kind = "categorical"
            else:
                raise ConfigError(f"recode[{i}] needs one of: reverse, map, bins")
            ctx.df[tgt] = out
            if tgt not in ctx.reasons.columns:
                ctx.reasons[tgt] = ctx.reasons[v] if v in ctx.reasons.columns else ""
            labels = {**labels, **{float(k) if _is_num(k) else k: str(lab)
                                   for k, lab in (rule.get("labels") or {}).items()}} if not rule.get("bins") else labels
            ctx.set_meta(tgt, kind=rule.get("kind", kind),
                         label=rule.get("label") or (f"{src_meta.label} (recoded)" if src_meta else tgt),
                         value_labels=labels, source="recode")
            done.append(f"{v}->{tgt}" if tgt != v else v)
        ctx.step("recode", rule.get("name") or f"recode[{i}]", variables=", ".join(done))


def _is_num(k) -> bool:
    try:
        float(k)
        return True
    except (TypeError, ValueError):
        return False


def derive(ctx: Context) -> None:
    for i, d in enumerate(ctx.config.get("derive") or []):
        if "name" not in d:
            raise ConfigError(f"derive[{i}] needs a name")
        names = expand_list(d["name"], ctx.timepoints)
        for j, name in enumerate(names):
            tp = ctx.timepoints[j] if "{t}" in d["name"] else None
            sub = (lambda x: x.replace("{t}", tp)) if tp else (lambda x: x)
            if "expr" in d:
                expr = sub(d["expr"])
                try:
                    val = ctx.df.eval(expr, engine="python")
                except Exception as err:  # noqa: BLE001
                    raise ConfigError(f"derive[{i}] '{name}': cannot evaluate '{expr}': {err}") from err
                val = pd.to_numeric(val, errors="coerce") if not pd.api.types.is_bool_dtype(val) else val.astype(float)
                val = val.replace([np.inf, -np.inf], np.nan)
                kind = d.get("kind", "numeric")
            elif d.get("type") == "datediff":
                start, end = ctx.df[sub(d["start"])], ctx.df[sub(d["end"])]
                unit = d.get("unit", "days")
                days = (end - start).dt.days.astype(float)
                val = days / {"days": 1, "weeks": 7, "months": 30.4375, "years": 365.2425}[unit]
                kind = "numeric"
            elif d.get("type") == "count_checked":
                excl = {f"{sub(d['prefix'])}___{str(e).lower().replace('-', '_')}" for e in d.get("exclude", [])}
                cols = [c for c in ctx.df.columns if c.startswith(sub(d["prefix"]) + "___") and c not in excl]
                val = ctx.df[cols].sum(axis=1, min_count=1) if cols else np.nan
                kind = "numeric"
            else:
                raise ConfigError(f"derive[{i}] needs 'expr' or a type (datediff, count_checked)")
            if "round" in d:
                val = val.round(int(d["round"]))
            ctx.df[name] = val
            ctx.reasons[name] = np.where(pd.isna(ctx.df[name]), "blank", "")
            ctx.set_meta(name, kind=kind, label=d.get("label", name).replace("{t}", tp or ""),
                         value_labels={float(k): v for k, v in (d.get("labels") or {}).items()}, source="derived")
        ctx.step("derive", f"Derived {', '.join(names)}")

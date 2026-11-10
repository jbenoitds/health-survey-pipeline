"""Pipeline orchestration."""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from . import __version__
from .config import build_config, load_config
from .context import Context
from .deidentify import _salt, deidentify, mark_redacted, pseudo_id, pseudonymise
from .describe import describe_all
from .dictionary import DataDictionary
from .io import read_table, write_csv, write_spss
from .longitudinal import to_wide
from .missing import apply_missing_codes, standardize
from .recode import derive, recode
from .scoring import score_scales
from .validate import coerce_types, custom_rules, dictionary_checks, duplicate_checks, skip_logic_checks


def load(cfg: Dict[str, Any]) -> Context:
    icfg = cfg["input"]
    df = read_table(icfg["data"], icfg.get("format", "auto"), icfg.get("sheet"), icfg.get("encoding"))
    dd = DataDictionary.from_csv(icfg["data_dictionary"]) if icfg.get("data_dictionary") else None
    ctx = Context(config=cfg, df=df, dictionary=dd, raw_shape=df.shape)
    ctx.step("load", f"Read {Path(icfg['data']).name}", rows=df.shape[0], columns=df.shape[1],
             dictionary_fields=len(dd) if dd else 0)
    return ctx


def clean(ctx: Context) -> Context:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _clean(ctx)


def _clean(ctx: Context) -> Context:
    standardize(ctx)
    mark_redacted(ctx)
    apply_missing_codes(ctx)
    duplicate_checks(ctx)
    dictionary_checks(ctx)
    skip_logic_checks(ctx)
    coerce_types(ctx)
    deidentify(ctx)
    to_wide(ctx)
    custom_rules(ctx)
    recode(ctx)
    derive(ctx)
    recode(ctx, after_derive=True)
    score_scales(ctx)
    pseudonymise(ctx)
    return ctx


def write_outputs(ctx: Context) -> Dict[str, str]:
    from .report import write_excel, write_html  # local import keeps module load light

    out = Path(ctx.config["project"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    ocfg = ctx.config["outputs"]
    written: Dict[str, str] = {}
    issues = ctx.issues_frame()

    hashed = ctx.config["deidentify"].get("enabled", True) and (ctx.config["deidentify"].get("hash_id") or {}).get("enabled")
    restricted = out / "restricted"
    if hashed and not issues.empty:
        # the shareable issue log uses pseudonyms; the original-ID version goes to restricted/
        restricted.mkdir(exist_ok=True)
        written["issues_restricted"] = str(write_csv(issues, restricted / "validation_issues_original_ids.csv"))
        salt = _salt(ctx.config["deidentify"]["hash_id"])
        length = int(ctx.config["deidentify"]["hash_id"].get("length", 12))
        issues = issues.assign(record_id=issues["record_id"].map(lambda v: pseudo_id(str(v), salt, length) if v != "" else ""))
    ctx.tables["issues"] = issues
    if hashed and "crosswalk" in ctx.tables:
        restricted.mkdir(exist_ok=True)
        written["id_crosswalk"] = str(write_csv(ctx.tables["crosswalk"], restricted / "id_crosswalk.csv"))

    # safety flags (e.g. PHQ-9 item 9) – records the clinical team must follow up
    flag_cols = [f["variable"] for s in ctx.scales for f in s["flags"]]
    if flag_cols:
        hits = ctx.df[[ctx.id_col] + flag_cols]
        hits = hits[(hits[flag_cols] == 1).any(axis=1)]
        target = (restricted if hashed else out)
        target.mkdir(exist_ok=True)
        written["safety_flags"] = str(write_csv(hits, target / "safety_flags.csv"))
        ctx.tables["safety_n"] = pd.DataFrame({"n_records": [len(hits)]})

    if ocfg.get("cleaned_csv", True):
        written["cleaned_csv"] = str(write_csv(ctx.df, out / "cleaned_data.csv"))
    written["validation_issues"] = str(write_csv(issues, out / "validation_issues.csv"))
    if ocfg.get("codebook", True):
        written["codebook"] = str(write_csv(ctx.tables["codebook"], out / "codebook.csv"))
    write_csv(ctx.tables["missingness"], out / "missingness.csv")
    write_csv(ctx.tables["table1"], out / "table1.csv")
    if not ctx.tables["scales"].empty:
        write_csv(ctx.tables["scales"], out / "scale_summary.csv")
    if not ctx.tables["timepoints"].empty:
        write_csv(ctx.tables["timepoints"], out / "timepoint_comparisons.csv")
    if ocfg.get("spss"):
        labels = {c: m.label for c, m in ctx.meta.items()}
        vlabels = {c: {float(k): v for k, v in m.value_labels.items() if _isnum(k)}
                   for c, m in ctx.meta.items() if m.value_labels}
        written["spss"] = str(write_spss(ctx.df, out / "cleaned_data.sav", labels, vlabels))
    if ocfg.get("excel", True):
        written["excel"] = str(write_excel(ctx, out / "summary_tables.xlsx"))
    if ocfg.get("html", True):
        written["html"] = str(write_html(ctx, out / "report.html"))
    run_log = {
        "tool": "health-survey-pipeline", "version": __version__, "started": ctx.started,
        "config": ctx.config.get("_config_path", ""), "input": ctx.config["input"]["data"],
        "raw_shape": list(ctx.raw_shape), "clean_shape": list(ctx.df.shape), "steps": ctx.log,
        "outputs": written,
    }
    (out / "run_log.json").write_text(json.dumps(run_log, indent=2, default=str), encoding="utf-8")
    written["run_log"] = str(out / "run_log.json")
    return written


def _isnum(k) -> bool:
    try:
        float(k)
        return True
    except (TypeError, ValueError):
        return False


def run(config: str | Path | Dict[str, Any], base_dir: Optional[str | Path] = None,
        write: bool = True) -> Context:
    """Run the full pipeline. ``config`` is a YAML path or an already-loaded dict."""
    if isinstance(config, (str, Path)):
        cfg = load_config(config)
    elif "_config_path" in config:
        cfg = config
    else:
        cfg = build_config(config, base_dir or ".")
    ctx = clean(load(cfg))
    describe_all(ctx)
    if write:
        ctx.outputs = write_outputs(ctx)  # type: ignore[attr-defined]
    return ctx

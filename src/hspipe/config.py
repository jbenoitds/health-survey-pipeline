"""Project configuration (YAML) loading, defaults and validation."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULTS: Dict[str, Any] = {
    "project": {"name": "Untitled project", "output_dir": "output"},
    "input": {
        "data": None,
        "format": "auto",
        "sheet": None,
        "encoding": None,
        "data_dictionary": None,
        "id_column": "record_id",
        "event_column": None,  # e.g. redcap_event_name for longitudinal REDCap
        "drop_columns": [],
    },
    "timepoints": [],
    "missing": {
        "codes": ["", "NA", "N/A", "n/a", "na", ".", "-", "NULL", "null", "None", "missing", "#N/A"],
        # numeric/string codes with a meaning, kept separate in the missingness report
        "labelled_codes": {},
        "apply_to": "all",  # or a list of variables
    },
    "longitudinal": {
        "reshape": False,
        "event_map": {},  # redcap_event_name -> suffix, e.g. baseline_arm_1: t1
        "suffix_sep": "_",
        "keep_unmapped_events": False,
    },
    "deidentify": {
        "enabled": True,
        "use_dictionary_identifiers": True,
        "drop": [],
        "keep": [],
        "derive_age": [],  # [{dob: dob, reference: consent_date, name: age_years}]
        "hash_id": {"enabled": False, "salt_env": "HSP_SALT", "salt": None, "write_crosswalk": True},
        "drop_free_text": False,
    },
    "validation": {
        "use_dictionary": True,
        "check_branching": True,
        "check_required": True,
        "check_duplicates": True,
        "rules": [],
        "on_invalid": "set_missing",  # set_missing | flag
    },
    "recode": [],
    "derive": [],
    "scales": [],
    "describe": {
        "variables": "auto",
        "continuous": [],
        "categorical": [],
        "group_by": None,
        "tests": True,
        "compare_timepoints": True,
        "max_categories": 15,
    },
    "outputs": {
        "cleaned_csv": True,
        "excel": True,
        "html": True,
        "spss": False,
        "codebook": True,
    },
}

KNOWN_TOP = set(DEFAULTS)


class ConfigError(ValueError):
    pass


def _merge(base: Dict, override: Dict) -> Dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = build_config(raw, base_dir=path.parent)
    cfg["_config_path"] = str(path.resolve())
    return cfg


def build_config(raw: Dict[str, Any], base_dir: str | Path = ".") -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a YAML mapping.")
    unknown = set(raw) - KNOWN_TOP
    if unknown:
        raise ConfigError(f"Unknown config section(s): {sorted(unknown)}. Allowed: {sorted(KNOWN_TOP)}")
    cfg = _merge(DEFAULTS, raw)
    base_dir = Path(base_dir)

    if not cfg["input"].get("data"):
        raise ConfigError("input.data is required (path to the raw export).")

    def resolve(p):
        if p is None:
            return None
        p = Path(p)
        return str(p if p.is_absolute() else (base_dir / p).resolve())

    cfg["input"]["data"] = resolve(cfg["input"]["data"])
    cfg["input"]["data_dictionary"] = resolve(cfg["input"]["data_dictionary"])
    cfg["project"]["output_dir"] = resolve(cfg["project"]["output_dir"])

    if cfg["validation"]["on_invalid"] not in {"set_missing", "flag"}:
        raise ConfigError("validation.on_invalid must be 'set_missing' or 'flag'.")
    tps = cfg.get("timepoints") or []
    if not isinstance(tps, list):
        raise ConfigError("timepoints must be a list, e.g. [t1, t2].")
    if cfg["longitudinal"]["reshape"] and not cfg["longitudinal"]["event_map"]:
        raise ConfigError("longitudinal.reshape is on but longitudinal.event_map is empty.")
    if cfg["longitudinal"]["reshape"] and not tps:
        cfg["timepoints"] = list(dict.fromkeys(cfg["longitudinal"]["event_map"].values()))
    for i, sc in enumerate(cfg["scales"]):
        if "name" not in sc:
            raise ConfigError(f"scales[{i}] needs a 'name'.")
        if "items" not in sc and "instrument" not in sc:
            raise ConfigError(f"scales[{i}] ('{sc['name']}') needs 'items' or an 'instrument'.")
    return cfg


def expand_template(value: str, timepoints: List[str]) -> List[str]:
    """'phq9_{t}' -> ['phq9_t1', 'phq9_t2']; strings without {t} are returned as-is."""
    if isinstance(value, str) and "{t}" in value:
        if not timepoints:
            raise ConfigError(f"'{value}' uses {{t}} but no timepoints are defined.")
        return [value.replace("{t}", str(t)) for t in timepoints]
    return [value]


def expand_list(values, timepoints: List[str]) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for v in values:
        out.extend(expand_template(v, timepoints))
    return out


def template_path() -> Path:
    return Path(__file__).parent / "templates" / "project_template.yaml"

"""Data dictionary / codebook parsing.

Two formats are accepted:

* A REDCap data dictionary CSV (exported from *Project Setup > Data Dictionary*).
* A simple generic codebook CSV with columns
  ``variable, label, type, choices, min, max, identifier, required, branching, form``
  (only ``variable`` is mandatory) for non-REDCap sources such as EMR extracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

NUMERIC_VALIDATIONS = {"integer", "number", "number_1dp", "number_2dp", "number_3dp", "number_4dp",
                       "number_comma_decimal", "number_1dp_comma_decimal", "number_2dp_comma_decimal"}
DATE_VALIDATIONS = {"date_ymd", "date_mdy", "date_dmy"}
DATETIME_VALIDATIONS = {"datetime_ymd", "datetime_mdy", "datetime_dmy", "datetime_seconds_ymd",
                        "datetime_seconds_mdy", "datetime_seconds_dmy"}
CHOICE_TYPES = {"radio", "dropdown", "checkbox", "yesno", "truefalse"}
NO_DATA_TYPES = {"descriptive"}

# Header aliases for REDCap dictionaries (matched on lower-cased prefix)
_REDCAP_COLS = {
    "name": "variable / field name",
    "form": "form name",
    "ftype": "field type",
    "label": "field label",
    "choices": "choices, calculations, or slider labels",
    "validation": "text validation type or show slider number",
    "vmin": "text validation min",
    "vmax": "text validation max",
    "identifier": "identifier?",
    "branching": "branching logic (show field only if...)",
    "required": "required field?",
    "annotation": "field annotation",
}
_GENERIC_COLS = {
    "name": "variable", "form": "form", "ftype": "type", "label": "label", "choices": "choices",
    "vmin": "min", "vmax": "max", "identifier": "identifier", "branching": "branching",
    "required": "required", "validation": "validation",
}


@dataclass
class VariableSpec:
    name: str
    form: str = ""
    ftype: str = "text"
    label: str = ""
    choices: Dict[str, str] = field(default_factory=dict)
    validation: str = ""
    vmin: Optional[str] = None
    vmax: Optional[str] = None
    identifier: bool = False
    branching: str = ""
    required: bool = False
    annotation: str = ""

    # ---- derived properties -------------------------------------------------
    @property
    def kind(self) -> str:
        """One of: numeric, categorical, checkbox, date, datetime, text, none."""
        if self.ftype in NO_DATA_TYPES:
            return "none"
        if self.ftype == "checkbox":
            return "checkbox"
        if self.ftype in CHOICE_TYPES:
            return "categorical"
        if self.ftype in {"slider", "calc"}:
            return "numeric"
        if self.ftype in {"integer", "number", "numeric", "continuous", "float"}:
            return "numeric"
        if self.ftype in {"categorical", "category", "ordinal", "nominal"}:
            return "categorical"
        if self.ftype in {"date"}:
            return "date"
        if self.validation in NUMERIC_VALIDATIONS:
            return "numeric"
        if self.validation in DATE_VALIDATIONS:
            return "date"
        if self.validation in DATETIME_VALIDATIONS:
            return "datetime"
        return "text"

    @property
    def is_integer(self) -> bool:
        return self.validation == "integer" or self.ftype in {"integer"}

    @property
    def data_columns(self) -> List[str]:
        if self.kind == "none" or self.ftype == "file":
            return [] if self.kind == "none" else [self.name]
        if self.kind == "checkbox":
            return [checkbox_column(self.name, c) for c in self.choices]
        return [self.name]

    def numeric_codes(self) -> Optional[Dict[float, str]]:
        """Choice codes as floats if all are numeric (used for SPSS value labels)."""
        out = {}
        for k, v in self.choices.items():
            try:
                out[float(k)] = v
            except ValueError:
                return None
        return out


def checkbox_column(name: str, code: str) -> str:
    """REDCap names checkbox columns ``field___code`` (minus signs become underscores)."""
    return f"{name}___{str(code).lower().replace('-', '_')}"


def parse_choices(raw: str, ftype: str = "") -> Dict[str, str]:
    ftype = (ftype or "").lower()
    if ftype == "yesno":
        return {"1": "Yes", "0": "No"}
    if ftype == "truefalse":
        return {"1": "True", "0": "False"}
    if ftype in {"calc", "slider", "sql"} or not raw or not str(raw).strip():
        return {}
    out: Dict[str, str] = {}
    for part in str(raw).split("|"):
        part = part.strip()
        if not part:
            continue
        if "," in part:
            code, label = part.split(",", 1)
            out[code.strip()] = label.strip()
        elif "=" in part:  # generic codebooks sometimes use 1=Yes
            code, label = part.split("=", 1)
            out[code.strip()] = label.strip()
        else:
            out[part] = part
    return out


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"y", "yes", "1", "true", "t", "x"}


def _clean_label(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", str(s or ""))  # strip HTML used in REDCap labels
    return re.sub(r"\s+", " ", s).strip()


class DataDictionary:
    def __init__(self, specs: List[VariableSpec], source: str = ""):
        self.specs: Dict[str, VariableSpec] = {s.name: s for s in specs}
        self.source = source
        self._col_index: Dict[str, VariableSpec] = {}
        for s in specs:
            for c in s.data_columns:
                self._col_index[c] = s

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_csv(cls, path: str | Path) -> "DataDictionary":
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        return cls.from_frame(df, source=str(path))

    @classmethod
    def from_frame(cls, df: pd.DataFrame, source: str = "") -> "DataDictionary":
        lower = {c.strip().lower(): c for c in df.columns}
        if "variable / field name" in lower:
            mapping = _REDCAP_COLS
        elif "variable" in lower or "name" in lower:
            mapping = dict(_GENERIC_COLS)
            if "variable" not in lower:
                mapping["name"] = "name"
        else:
            raise ValueError("Unrecognised dictionary: expected a REDCap data dictionary or a codebook "
                             "with a 'variable' column.")
        col = {k: lower.get(v) for k, v in mapping.items()}
        specs = []
        for _, row in df.iterrows():
            def g(key):
                c = col.get(key)
                return str(row[c]).strip() if c else ""
            name = g("name")
            if not name:
                continue
            ftype = g("ftype").lower() or "text"
            specs.append(VariableSpec(
                name=name,
                form=g("form"),
                ftype=ftype,
                label=_clean_label(g("label")),
                choices=parse_choices(g("choices"), ftype),
                validation=g("validation").lower(),
                vmin=g("vmin") or None,
                vmax=g("vmax") or None,
                identifier=_truthy(g("identifier")),
                branching=g("branching"),
                required=_truthy(g("required")),
                annotation=g("annotation"),
            ))
        return cls(specs, source=source)

    # -- lookup ---------------------------------------------------------------
    def __contains__(self, name: str) -> bool:
        return name in self.specs

    def __getitem__(self, name: str) -> VariableSpec:
        return self.specs[name]

    def __len__(self) -> int:
        return len(self.specs)

    def spec_for_column(self, column: str) -> Optional[VariableSpec]:
        return self._col_index.get(column) or self.specs.get(column)

    def identifier_columns(self) -> List[str]:
        return [c for s in self.specs.values() if s.identifier for c in s.data_columns]

    def forms(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for s in self.specs.values():
            out.setdefault(s.form, []).extend(s.data_columns)
        return out

    def variable_labels(self) -> Dict[str, str]:
        labels = {}
        for s in self.specs.values():
            if s.kind == "checkbox":
                for code, lab in s.choices.items():
                    labels[checkbox_column(s.name, code)] = f"{s.label}: {lab}"
            else:
                labels[s.name] = s.label
        return labels

    def value_labels(self) -> Dict[str, Dict[float, str]]:
        out = {}
        for s in self.specs.values():
            if s.kind == "categorical":
                codes = s.numeric_codes()
                if codes:
                    out[s.name] = codes
            elif s.kind == "checkbox":
                for code in s.choices:
                    out[checkbox_column(s.name, code)] = {1.0: "Checked", 0.0: "Unchecked"}
        return out

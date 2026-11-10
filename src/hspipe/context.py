"""Shared state passed between pipeline steps."""
from __future__ import annotations

import datetime as _dt
import numbers
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .dictionary import DataDictionary

# Reasons a cell can be empty (stored in Context.reasons)
REASON_BLANK = "blank"
REASON_SKIPPED = "skipped (branching)"
REASON_INVALID = "invalid value removed"
REASON_FORM = "form not started"


@dataclass
class VarMeta:
    kind: str = "text"  # numeric | categorical | checkbox | date | datetime | text
    label: str = ""
    value_labels: Dict[Any, str] = field(default_factory=dict)
    source: str = "raw"  # raw | derived | scale | recode


@dataclass
class Context:
    config: Dict[str, Any]
    df: pd.DataFrame
    dictionary: Optional[DataDictionary] = None
    reasons: Optional[pd.DataFrame] = None  # same shape as df, object dtype, '' when value present
    meta: Dict[str, VarMeta] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    log: List[Dict[str, Any]] = field(default_factory=list)
    scales: List[Dict[str, Any]] = field(default_factory=list)  # scale summaries
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)
    raw_shape: tuple = (0, 0)
    redacted: set = field(default_factory=set)  # columns whose values must never be echoed
    reshaped: bool = False
    missing_code_values: Dict[str, set] = field(default_factory=dict)  # codes turned into missing, per column
    started: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))

    @property
    def id_col(self) -> str:
        return self.config["input"]["id_column"]

    @property
    def event_col(self) -> Optional[str]:
        return self.config["input"].get("event_column")

    @property
    def timepoints(self) -> List[str]:
        return list(self.config.get("timepoints") or [])

    def step(self, name: str, detail: str, **counts) -> None:
        self.log.append({"step": name, "detail": detail, **counts})

    def issue(self, row_index, variable: str, value, rule: str, message: str,
              severity: str = "error", action: str = "flagged") -> None:
        rid = ""
        if self.id_col in self.df.columns and row_index in self.df.index:
            rid = self.df.at[row_index, self.id_col]
        event = ""
        if self.event_col and self.event_col in self.df.columns and row_index in self.df.index:
            event = self.df.at[row_index, self.event_col]
        if variable in self.redacted:
            value = "<redacted>"
        elif isinstance(value, pd.Timestamp):
            value = "" if pd.isna(value) else (str(value.date()) if value == value.normalize() else str(value))
        elif isinstance(value, float):
            value = "" if value != value else (str(int(value)) if value.is_integer() else f"{value:g}")
        self.issues.append({
            # row number as seen in the raw file opened in Excel (header = row 1);
            # blank once data were reshaped (rows no longer map to the raw file)
            "row": (int(row_index) + 2 if isinstance(row_index, numbers.Integral) else row_index)
            if not self.reshaped else "",
            "record_id": rid,
            "event": event,
            "variable": variable,
            "value": "" if value is None else str(value),
            "rule": rule,
            "severity": severity,
            "action": action,
            "message": message,
        })

    def issues_frame(self) -> pd.DataFrame:
        cols = ["row", "record_id", "event", "variable", "value", "rule", "severity", "action", "message"]
        return pd.DataFrame(self.issues, columns=cols)

    def set_meta(self, col: str, **kw) -> None:
        m = self.meta.get(col, VarMeta())
        for k, v in kw.items():
            setattr(m, k, v)
        self.meta[col] = m

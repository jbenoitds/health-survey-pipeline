"""Reading raw exports and writing cleaned outputs."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

SUPPORTED = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".sav", ".zsav", ".dta", ".parquet"}


def _require_pyreadstat():
    try:
        import pyreadstat  # noqa: F401
        return pyreadstat
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "Reading/writing SPSS files needs pyreadstat: pip install 'health-survey-pipeline[spss]'"
        ) from exc


def read_table(path: str | Path, fmt: str = "auto", sheet: Optional[str] = None,
               encoding: Optional[str] = None) -> pd.DataFrame:
    """Read a raw export. Every column is read as text so nothing is silently coerced;
    type conversion happens later, under the pipeline's control (and gets logged)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    ext = path.suffix.lower() if fmt == "auto" else "." + fmt.lower().lstrip(".")
    if ext not in SUPPORTED:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {sorted(SUPPORTED)}")

    if ext in {".csv", ".txt", ".tsv"}:
        encodings = [encoding] if encoding else ["utf-8-sig", "cp1252", "latin-1"]
        last_err = None
        for enc in encodings:
            try:
                sep = "\t" if ext == ".tsv" else _detect_sep(path, enc)
                return pd.read_csv(path, dtype=str, keep_default_na=False, sep=sep, encoding=enc)
            except UnicodeDecodeError as err:
                last_err = err
        raise last_err  # type: ignore[misc]
    if ext in {".xlsx", ".xls"}:
        # explicit engine: pandas < 1.2 otherwise tries xlrd for .xlsx (.xls still needs xlrd)
        engine = "openpyxl" if ext == ".xlsx" else None
        return pd.read_excel(path, sheet_name=sheet or 0, dtype=str, keep_default_na=False, engine=engine)
    if ext in {".sav", ".zsav"}:
        prs = _require_pyreadstat()
        df, _meta = prs.read_sav(str(path), apply_value_formats=False)
        return _stringify(df)
    if ext == ".dta":
        df = pd.read_stata(path, convert_categoricals=False)
        return _stringify(df)
    if ext == ".parquet":
        return _stringify(pd.read_parquet(path))
    raise ValueError(ext)  # pragma: no cover


def _detect_sep(path: Path, encoding: str) -> str:
    """Comma by default; semicolon or tab when the header clearly uses them
    (European-locale Excel saves CSVs with ';')."""
    with open(path, encoding=encoding) as fh:
        header = fh.readline()
    counts = {",": header.count(","), ";": header.count(";"), "\t": header.count("\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > counts[","] else ","


def _stringify(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_float_dtype(s):
            # 3.0 -> "3", keep 2.5 as "2.5"
            out[c] = s.map(lambda v: "" if pd.isna(v) else (str(int(v)) if float(v).is_integer() else repr(float(v))))
        else:
            out[c] = s.map(lambda v: "" if pd.isna(v) else str(v))
    return out


def write_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def write_spss(df: pd.DataFrame, path: Path, variable_labels: Dict[str, str],
               value_labels: Dict[str, Dict[float, str]]) -> Path:
    prs = _require_pyreadstat()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    # SPSS variable names: <=64 chars, no spaces; pyreadstat validates the rest.
    for c in out.columns:
        if not (pd.api.types.is_numeric_dtype(out[c]) or pd.api.types.is_datetime64_any_dtype(out[c])):
            out[c] = out[c].astype("object").where(out[c].notna(), None)
    col_labels = [variable_labels.get(c, "")[:255] or None for c in out.columns]
    vl = {c: {float(k): str(v) for k, v in labs.items()} for c, labs in value_labels.items()
          if c in out.columns and pd.api.types.is_numeric_dtype(out[c])}
    prs.write_sav(out, str(path), column_labels=col_labels, variable_value_labels=vl)
    return path

"""Small shims so the same code runs on 2020-era libraries (pandas 1.1+, scipy 1.5+)
and on current releases (pandas 2.x / 3.x)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _version(v: str) -> tuple:
    out = []
    for part in v.split(".")[:2]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


PANDAS_VERSION = _version(pd.__version__)
PANDAS_2 = PANDAS_VERSION >= (2, 0)

_ISO_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S")


def frame_map(df: pd.DataFrame, func) -> pd.DataFrame:
    """Element-wise map (DataFrame.map since pandas 2.1, applymap before)."""
    return df.map(func) if hasattr(df, "map") else df.applymap(func)


def parse_dates(s: pd.Series, dayfirst: bool = False) -> pd.Series:
    """Parse text dates. Exact ISO formats first (REDCap raw exports), then a lenient
    per-value parse for anything else (e.g. 15/03/2024 after a round trip through Excel)."""
    s = s.where(s.notna() & (s.astype(str) != ""), np.nan)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    rest = s.notna()
    for fmt in _ISO_FORMATS:
        if not rest.any():
            break
        parsed = pd.to_datetime(s[rest], errors="coerce", format=fmt)
        out.loc[parsed.index[parsed.notna()]] = parsed[parsed.notna()]
        rest = s.notna() & out.isna()
    if rest.any():
        values = s[rest]
        if PANDAS_2:
            alt = pd.to_datetime(values, errors="coerce", dayfirst=dayfirst, format="mixed")
        else:  # pandas 1.x parses each value with dateutil when no format is given
            alt = pd.to_datetime(values, errors="coerce", dayfirst=dayfirst)
        out.loc[alt.index] = alt
    return out


def pvalue(result) -> float:
    """p-value from a scipy result: named tuples / result objects (.pvalue) or plain tuples
    (fisher_exact and chi2_contingency returned (statistic, p, ...) in scipy < 1.11)."""
    if hasattr(result, "pvalue"):
        return float(result.pvalue)
    return float(result[1])

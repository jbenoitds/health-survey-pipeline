"""A small, safe evaluator for REDCap branching logic (no ``eval``).

Supports the subset used by the vast majority of projects::

    [field] = '1'      [field] <> ''      [age] >= 18      [chk(3)] = '1'
    [event_arm_1][field] = '2'            (event prefix is ignored)
    and / or / not, parentheses, + - * /, and the functions
    if(), sum(), abs(), min(), max(), isnumber(), contains(), datediff() (days/y/M/w/d units)

Evaluation is vectorised: an expression is evaluated against a whole DataFrame
and returns a boolean Series ("would this field be shown for this row?").
Anything unsupported raises :class:`BranchingError`; callers treat that field's
skip status as *unknown* rather than guessing.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

from .dictionary import checkbox_column


class BranchingError(ValueError):
    pass


_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<field>\[[^\]]+\])
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<op><>|!=|<=|>=|==|=|<|>|\+|-|\*|/|\(|\)|,)
  | (?P<word>[A-Za-z_][A-Za-z0-9_\-]*)
""", re.VERBOSE)


def tokenize(text: str) -> List[Tuple[str, str]]:
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise BranchingError(f"Cannot parse branching logic near: {text[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        val = m.group(kind)
        if kind == "word":
            low = val.lower()
            if low in {"and", "or", "not"}:
                kind, val = "kw", low
            elif low in {"true", "false"}:
                kind, val = "num", "1" if low == "true" else "0"
        out.append((kind, val))
    # Merge [event][field] -> field
    merged: List[Tuple[str, str]] = []
    for tok in out:
        if tok[0] == "field" and merged and merged[-1][0] == "field":
            merged[-1] = tok
        else:
            merged.append(tok)
    return merged


Node = Callable[[pd.DataFrame], Any]


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0
        self.fields: set = set()

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if tok[0] is None or (kind and tok[0] != kind) or (val and tok[1] != val):
            raise BranchingError(f"Unexpected token {tok[1]!r}, expected {val or kind}")
        self.i += 1
        return tok

    def parse(self) -> Node:
        node = self.or_()
        if self.peek()[0] is not None:
            raise BranchingError(f"Unexpected trailing token {self.peek()[1]!r}")
        return node

    def or_(self):
        left = self.and_()
        while self.peek() == ("kw", "or"):
            self.take()
            right = self.and_()
            left = (lambda a, b: lambda df: _bool(a(df), df) | _bool(b(df), df))(left, right)
        return left

    def and_(self):
        left = self.not_()
        while self.peek() == ("kw", "and"):
            self.take()
            right = self.not_()
            left = (lambda a, b: lambda df: _bool(a(df), df) & _bool(b(df), df))(left, right)
        return left

    def not_(self):
        if self.peek() == ("kw", "not"):
            self.take()
            inner = self.not_()
            return lambda df: ~_bool(inner(df), df)
        return self.cmp()

    def cmp(self):
        left = self.add()
        kind, val = self.peek()
        if kind == "op" and val in {"=", "==", "<>", "!=", "<", "<=", ">", ">="}:
            self.take()
            right = self.add()
            return (lambda a, b, op: lambda df: _compare(a(df), b(df), op, df))(left, right, val)
        return left

    def add(self):
        left = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in {"+", "-"}:
            op = self.take()[1]
            right = self.mul()
            left = (lambda a, b, o: lambda df: _arith(a(df), b(df), o))(left, right, op)
        return left

    def mul(self):
        left = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in {"*", "/"}:
            op = self.take()[1]
            right = self.unary()
            left = (lambda a, b, o: lambda df: _arith(a(df), b(df), o))(left, right, op)
        return left

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            inner = self.unary()
            return lambda df: _arith(0, inner(df), "-")
        return self.atom()

    def atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return lambda df, v=val: v
        if kind == "str":
            self.take()
            return lambda df, v=val[1:-1]: v
        if kind == "field":
            self.take()
            return self._field(val[1:-1])
        if (kind, val) == ("op", "("):
            self.take()
            node = self.or_()
            self.take("op", ")")
            return node
        if kind == "word" and self.peek(1) == ("op", "("):
            return self._func()
        raise BranchingError(f"Unsupported token {val!r}")

    def _field(self, inner: str) -> Node:
        m = re.fullmatch(r"([A-Za-z0-9_]+)(?:\(([^)]+)\))?(?::[a-z_]+)?", inner.strip())
        if not m:
            raise BranchingError(f"Unsupported field reference [{inner}] (smart variables are not supported)")
        name, code = m.group(1), m.group(2)
        col = checkbox_column(name, code) if code else name
        self.fields.add(col)

        def get(df, col=col):
            if col not in df.columns:
                raise BranchingError(f"Field '{col}' used in branching logic is not in the data")
            return df[col].fillna("").astype(str)
        return get

    def _func(self) -> Node:
        name = self.take("word")[1].lower()
        self.take("op", "(")
        args: List[Node] = []
        if self.peek() != ("op", ")"):
            args.append(self.or_())
            while self.peek() == ("op", ","):
                self.take()
                args.append(self.or_())
        self.take("op", ")")
        fn = _FUNCS.get(name)
        if fn is None:
            raise BranchingError(f"Function '{name}()' is not supported")
        return lambda df: fn(df, [a(df) for a in args])


# ---- vectorised helpers ----------------------------------------------------

def _series(x, df) -> pd.Series:
    if isinstance(x, pd.Series):
        return x
    return pd.Series([x] * len(df), index=df.index, dtype=object)


def _num(x):
    if isinstance(x, pd.Series):
        return pd.to_numeric(x.where(x != "", np.nan), errors="coerce")
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _bool(x, df) -> pd.Series:
    if isinstance(x, pd.Series):
        if x.dtype == bool:
            return x
        n = _num(x)
        return (n.fillna(0) != 0) if isinstance(n, pd.Series) else _series(bool(n), df)
    if isinstance(x, (bool, np.bool_)):
        return _series(bool(x), df).astype(bool)
    n = _num(x)
    return _series(bool(n == n and n != 0), df).astype(bool)


def _compare(a, b, op, df) -> pd.Series:
    sa, sb = _series(a, df).astype(object), _series(b, df).astype(object)
    sa_str = sa.map(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip())
    sb_str = sb.map(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip())
    na, nb = _num(sa_str), _num(sb_str)
    both_num = na.notna() & nb.notna()
    if op in {"=", "=="}:
        return (both_num & (na == nb)) | (~both_num & (sa_str == sb_str))
    if op in {"<>", "!="}:
        return ~((both_num & (na == nb)) | (~both_num & (sa_str == sb_str)))
    ops = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}
    with np.errstate(invalid="ignore"):
        res = ops[op](na, nb)
    return (both_num & res).astype(bool)


def _arith(a, b, op):
    na, nb = _num(a), _num(b)
    if op == "+":
        return na + nb
    if op == "-":
        return na - nb
    if op == "*":
        return na * nb
    with np.errstate(divide="ignore", invalid="ignore"):
        return na / nb


def _f_if(df, args):
    if len(args) != 3:
        raise BranchingError("if() needs 3 arguments")
    cond = _bool(args[0], df)
    return _series(args[1], df).where(cond, _series(args[2], df))


def _f_sum(df, args):
    frame = pd.concat([_series(_num(a), df) for a in args], axis=1)
    return frame.sum(axis=1, min_count=1)


def _f_datediff(df, args):
    if len(args) < 3:
        raise BranchingError("datediff() needs at least 3 arguments")
    d1 = pd.to_datetime(_series(args[0], df).replace("", np.nan), errors="coerce")
    d2 = pd.to_datetime(_series(args[1], df).replace("", np.nan), errors="coerce")
    unit = str(args[2]).strip("'\"")
    days = (d2 - d1).dt.total_seconds() / 86400.0
    div = {"y": 365.2425, "M": 30.44, "w": 7, "d": 1, "h": 1 / 24, "m": 1 / 1440, "s": 1 / 86400}.get(unit)
    if div is None:
        raise BranchingError(f"datediff unit '{unit}' not supported")
    out = days / div
    signed = len(args) > 3 and str(args[-1]).strip("'\"").lower() in {"true", "1"}
    return out if signed else out.abs()


_FUNCS: Dict[str, Callable] = {
    "if": _f_if,
    "sum": _f_sum,
    "abs": lambda df, a: _num(_series(a[0], df)).abs(),
    "min": lambda df, a: pd.concat([_series(_num(x), df) for x in a], axis=1).min(axis=1),
    "max": lambda df, a: pd.concat([_series(_num(x), df) for x in a], axis=1).max(axis=1),
    "isnumber": lambda df, a: _num(_series(a[0], df)).notna(),
    "contains": lambda df, a: _series(a[0], df).astype(str).str.contains(str(a[1]), regex=False),
    "datediff": _f_datediff,
}


class BranchingLogic:
    """Compiled branching logic expression."""

    def __init__(self, text: str):
        self.text = text
        parser = _Parser(tokenize(text))
        self._node = parser.parse()
        self.fields = sorted(parser.fields)

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Boolean Series: True where the field would be displayed."""
        return _bool(self._node(df), df).reindex(df.index).fillna(False).astype(bool)


def compile_logic(text: str) -> BranchingLogic:
    return BranchingLogic(text)

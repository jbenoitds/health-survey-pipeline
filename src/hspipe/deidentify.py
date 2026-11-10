"""De-identification: drop direct identifiers, derive age from DOB, pseudonymise IDs."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .context import Context

# Column names that very often hold direct identifiers (PHI/PII)
PHI_NAME_PATTERNS = [
    r"(^|_)(first|last|full|given|family|sur|middle|maiden)_?name", r"^name$", r"(^|_)e?mail",
    r"phone|mobile|cell(_|$)|tel(_|$)", r"address|street|city|postal|zip", r"(^|_)mrn($|_)",
    r"health_?card|insurance_?(no|num)|ramq|ohip|nhs_?(no|num)", r"(^|_)(sin|ssn|nas)($|_)",
    r"(^|_)dob($|_)|birth_?date|date_?of_?birth", r"ip_?address", r"signature",
]
EMAIL_IN_TEXT = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
PHONE_IN_TEXT = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def flag_identifier_like(ctx: Context) -> list:
    hits = []
    for c in ctx.df.columns:
        if any(re.search(p, c, re.I) for p in PHI_NAME_PATTERNS):
            hits.append(c)
    return hits


def mark_redacted(ctx: Context) -> None:
    """Called before validation so issue logs never echo identifier values."""
    dcfg = ctx.config["deidentify"]
    if dcfg.get("enabled", True) and (dcfg.get("hash_id") or {}).get("enabled"):
        _salt(dcfg["hash_id"])  # fail fast if the salt is missing
    cols = set(dcfg.get("drop") or []) | set(flag_identifier_like(ctx))
    if ctx.dictionary is not None:
        cols |= set(ctx.dictionary.identifier_columns())
    ctx.redacted = {c for c in cols if c in ctx.df.columns}


def derive_ages(ctx: Context) -> None:
    for spec in ctx.config["deidentify"].get("derive_age") or []:
        dob, ref, name = spec["dob"], spec["reference"], spec.get("name", "age_years")
        if dob not in ctx.df.columns or ref not in ctx.df.columns:
            ctx.step("derive_age", f"Skipped: '{dob}' or '{ref}' not found")
            continue
        d1, d2 = ctx.df[dob], ctx.df[ref]
        if ctx.event_col and ctx.event_col in ctx.df.columns:
            # in long REDCap exports DOB and the reference date may sit on different event rows
            # DOB usually sits on the first event only: share it with every row of the participant.
            # Age is computed only on rows that carry the reference date.
            d1 = d1.groupby(ctx.df[ctx.id_col]).transform("first")
        age = (d2 - d1).dt.days / 365.2425
        ctx.df[name] = np.floor(age) if spec.get("floor", True) else age.round(1)
        ctx.reasons[name] = np.where(ctx.df[name].isna(), "blank", "")
        ctx.set_meta(name, kind="numeric", label=spec.get("label", f"Age in years at {ref}"), source="derived")
        bad = (ctx.df[name] < 0) | (ctx.df[name] > 120)
        for idx in ctx.df.index[bad.fillna(False)]:
            ctx.issue(idx, name, ctx.df.at[idx, name], "age:implausible",
                      f"Derived age outside 0-120 (check {dob}/{ref})", "error", "set to missing")
        ctx.df.loc[bad.fillna(False), name] = np.nan
        ctx.step("derive_age", f"Derived '{name}' from {dob} and {ref}", n=int(ctx.df[name].notna().sum()))


def _salt(hcfg) -> str:
    salt = hcfg.get("salt") or os.environ.get(hcfg.get("salt_env") or "HSP_SALT", "")
    if not salt:
        raise ValueError("deidentify.hash_id is enabled but no salt was given. Set the environment variable "
                         f"{hcfg.get('salt_env') or 'HSP_SALT'} (recommended) or deidentify.hash_id.salt.")
    return salt


def pseudo_id(value: str, salt: str, length: int = 12) -> str:
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()[:length]


def deidentify(ctx: Context) -> None:
    dcfg = ctx.config["deidentify"]
    if not dcfg.get("enabled", True):
        ctx.step("deidentify", "Disabled in config – identifiers retained")
        return
    derive_ages(ctx)
    drop = set(c for c in (dcfg.get("drop") or []) if c in ctx.df.columns)
    if dcfg.get("use_dictionary_identifiers", True) and ctx.dictionary is not None:
        drop |= {c for c in ctx.dictionary.identifier_columns() if c in ctx.df.columns}
    if dcfg.get("drop_free_text") and ctx.dictionary is not None:
        for s in ctx.dictionary.specs.values():
            if s.ftype == "notes" or (s.ftype == "text" and not s.validation):
                drop |= {c for c in s.data_columns if c in ctx.df.columns}
    for d in dcfg.get("derive_age") or []:
        if d.get("drop_dob", True) and d["dob"] in ctx.df.columns:
            drop.add(d["dob"])
    keep = set(dcfg.get("keep") or [])
    drop -= keep | {ctx.id_col}

    suspicious = [c for c in flag_identifier_like(ctx) if c not in drop and c not in keep and c != ctx.id_col]
    text_hits = []
    for c in ctx.df.columns:
        if c in drop or c == ctx.id_col or ctx.meta.get(c) is None or ctx.meta[c].kind != "text":
            continue
        s = ctx.df[c].dropna().astype(str)
        if s.str.contains(EMAIL_IN_TEXT).any() or s.str.contains(PHONE_IN_TEXT).any():
            text_hits.append(c)

    ctx.df = ctx.df.drop(columns=sorted(drop))
    ctx.reasons = ctx.reasons.drop(columns=sorted(drop))
    for c in drop:
        ctx.meta.pop(c, None)
    ctx.step("deidentify", "Dropped direct identifiers", n_dropped=len(drop), dropped=", ".join(sorted(drop)))
    if suspicious:
        ctx.step("deidentify_warning", "Columns whose NAMES look like identifiers but were kept – review, then add "
                 "to deidentify.drop or deidentify.keep", columns=", ".join(suspicious))
    if text_hits:
        ctx.step("deidentify_warning", "Free-text columns containing email/phone-like strings – review",
                 columns=", ".join(text_hits))



def pseudonymise(ctx: Context) -> None:
    """Runs last, so every validation issue is logged with the original ID first."""
    dcfg = ctx.config["deidentify"]
    hcfg = dcfg.get("hash_id") or {}
    if dcfg.get("enabled", True) and hcfg.get("enabled"):
        salt = _salt(hcfg)
        orig = ctx.df[ctx.id_col].astype(str)
        new = orig.map(lambda v: pseudo_id(v, salt, int(hcfg.get("length", 12))))
        if new.nunique() != orig.nunique():
            raise ValueError("Pseudonymised ID collision – increase deidentify.hash_id.length")
        ctx.df[ctx.id_col] = new
        if hcfg.get("write_crosswalk", True):  # written by write_outputs into output/restricted/
            ctx.tables["crosswalk"] = pd.DataFrame({"original_id": orig, "pseudo_id": new}).drop_duplicates()
        ctx.step("pseudonymise", "Replaced record IDs with salted SHA-256 pseudonyms"
                 + (" (crosswalk in output/restricted/ – store separately!)" if hcfg.get("write_crosswalk", True) else ""))

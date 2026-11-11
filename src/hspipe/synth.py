"""Synthetic, fully fake REDCap-style project used for the demo and tests.

Nothing here comes from a real study: participants, names and contact details are
randomly generated. Known data problems are planted on purpose so the pipeline
has something to find (out-of-range values, missing codes, skip-logic violations,
a duplicate row, bad dates, an identifier inside a free-text field...).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd


DD_HEADER = ["Variable / Field Name", "Form Name", "Section Header", "Field Type", "Field Label",
             "Choices, Calculations, OR Slider Labels", "Field Note", "Text Validation Type OR Show Slider Number",
             "Text Validation Min", "Text Validation Max", "Identifier?", "Branching Logic (Show field only if...)",
             "Required Field?", "Custom Alignment", "Question Number (surveys only)", "Matrix Group Name",
             "Matrix Ranking?", "Field Annotation"]

FREQ4 = "0, Not at all | 1, Several days | 2, More than half the days | 3, Nearly every day"
FREQ5 = "0, Never | 1, Almost never | 2, Sometimes | 3, Fairly often | 4, Very often"


def _f(name, form, ftype, label, choices="", val="", vmin="", vmax="", ident="", branch="", req="", matrix=""):
    return {"Variable / Field Name": name, "Form Name": form, "Field Type": ftype, "Field Label": label,
            "Choices, Calculations, OR Slider Labels": choices, "Text Validation Type OR Show Slider Number": val,
            "Text Validation Min": vmin, "Text Validation Max": vmax, "Identifier?": ident,
            "Branching Logic (Show field only if...)": branch, "Required Field?": req, "Matrix Group Name": matrix}


def dictionary_rows() -> List[dict]:
    e = "enrollment"
    rows = [
        _f("record_id", e, "text", "Record ID"),
        _f("first_name", e, "text", "First name", ident="y"),
        _f("last_name", e, "text", "Last name", ident="y"),
        _f("email", e, "text", "Email", val="email", ident="y"),
        _f("phone", e, "text", "Phone number", val="phone", ident="y"),
        _f("dob", e, "text", "Date of birth", val="date_ymd", ident="y"),
        _f("consent_date", e, "text", "Date of consent", val="date_ymd", vmin="2023-01-01", vmax="today", req="y"),
        _f("sex", e, "radio", "Sex assigned at birth", "1, Female | 2, Male | 3, Intersex / another sex", req="y"),
        _f("pregnant", e, "yesno", "Currently pregnant?", branch="[sex] = '1'"),
        _f("education", e, "dropdown", "Highest level of education",
           "1, Primary or less | 2, Secondary | 3, College / trade | 4, Undergraduate degree | 5, Graduate degree"),
        _f("income", e, "radio", "Household income (CAD)",
           "1, < 25k | 2, 25k-49k | 3, 50k-74k | 4, 75k-99k | 5, 100k+ | 99, Prefer not to answer"),
        _f("group", e, "radio", "Study arm", "1, Usual care | 2, Intervention", req="y"),
        _f("height_cm", e, "text", "Height (cm)", val="number", vmin="120", vmax="220"),
        _f("weight_kg", e, "text", "Weight (kg)", val="number", vmin="30", vmax="250"),
        _f("smoker", e, "yesno", "Currently smokes tobacco?"),
        _f("cigs_per_day", e, "text", "Cigarettes per day", val="integer", vmin="1", vmax="80",
           branch="[smoker] = '1'", req="y"),
        _f("conditions", e, "checkbox", "Diagnosed health conditions",
           "1, Hypertension | 2, Diabetes | 3, Asthma | 4, Depression or anxiety | 5, None of these"),
        _f("conditions_notes", e, "notes", "Other conditions / comments"),
        _f("intro_survey", "wellbeing_survey", "descriptive", "The next questions ask about the past 2 weeks."),
        _f("survey_date", "wellbeing_survey", "text", "Survey date", val="date_ymd", req="y"),
    ]
    rows += [_f(f"phq9_{i}", "wellbeing_survey", "radio", f"PHQ-9 item {i}", FREQ4, matrix="phq9")
             for i in range(1, 10)]
    rows += [_f(f"gad7_{i}", "wellbeing_survey", "radio", f"GAD-7 item {i}", FREQ4, matrix="gad7")
             for i in range(1, 8)]
    rows += [_f(f"pss_{i}", "wellbeing_survey", "radio", f"PSS-10 item {i}", FREQ5, matrix="pss")
             for i in range(1, 11)]
    rows += [
        _f("sleep_hours", "wellbeing_survey", "text", "Average hours of sleep per night", val="number",
           vmin="0", vmax="24"),
        _f("activity_days", "wellbeing_survey", "text", "Days with 30+ minutes of physical activity (past week)",
           val="integer", vmin="0", vmax="7"),
    ]
    return rows


FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn", "Robin", "Drew"]
LAST = ["Smith", "Lee", "Martin", "Brown", "Garcia", "Nguyen", "Roy", "Wilson", "Khan", "Lopez", "Clark", "Young"]


def _likert(rng, latent, n_items, top, reverse=()):
    base = latent[:, None] + rng.normal(0, 0.75, (len(latent), n_items))
    vals = np.clip(np.rint(base), 0, top).astype(int)
    for r in reverse:
        vals[:, r - 1] = top - vals[:, r - 1]
    return vals


def make_demo(out_dir: str | Path, n: int = 240, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ids = [str(1000 + i) for i in range(1, n + 1)]
    sex = rng.choice([1, 2, 3], n, p=[0.62, 0.36, 0.02])
    group = rng.choice([1, 2], n)
    consent = pd.Timestamp("2023-03-01") + pd.to_timedelta(rng.integers(0, 500, n), unit="D")
    dob = consent - pd.to_timedelta((rng.uniform(19, 48, n) * 365.25).astype(int), unit="D")
    smoker = rng.choice([0, 1], n, p=[0.82, 0.18])
    height = np.where(sex == 2, rng.normal(176, 7, n), rng.normal(163, 6.5, n)).round(1)
    bmi = rng.normal(26, 4.5, n)
    weight = (bmi * (height / 100) ** 2).round(1)
    latent_dep = np.clip(rng.gamma(2.0, 0.45, n), 0, 3)
    latent_anx = np.clip(0.6 * latent_dep + rng.gamma(1.5, 0.35, n), 0, 3)

    base_rows, fu_rows = [], []
    for i, rid in enumerate(ids):
        cond = rng.random(4) < [0.15, 0.07, 0.09, 0.2]
        r = {
            "record_id": rid, "redcap_event_name": "baseline_arm_1",
            "first_name": rng.choice(FIRST), "last_name": rng.choice(LAST),
            "email": f"participant{rid}@example.org", "phone": f"555-{rng.integers(100, 999)}-{rng.integers(1000, 9999)}",
            "dob": dob[i].strftime("%Y-%m-%d"), "consent_date": consent[i].strftime("%Y-%m-%d"),
            "sex": sex[i], "pregnant": (int(rng.random() < 0.3) if sex[i] == 1 else ""),
            "education": rng.choice([1, 2, 3, 4, 5], p=[0.03, 0.2, 0.27, 0.32, 0.18]),
            "income": rng.choice([1, 2, 3, 4, 5, 99], p=[0.12, 0.2, 0.22, 0.18, 0.2, 0.08]),
            "group": group[i], "height_cm": height[i], "weight_kg": weight[i], "smoker": smoker[i],
            "cigs_per_day": int(rng.integers(2, 25)) if smoker[i] else "",
            **{f"conditions___{k + 1}": int(cond[k]) for k in range(4)},
            "conditions___5": int(not cond.any()), "conditions_notes": "",
            "enrollment_complete": 2,
        }
        r.update(_survey(rng, latent_dep[i], latent_anx[i], consent[i] + pd.Timedelta(days=int(rng.integers(0, 8)))))
        base_rows.append(r)
        if rng.random() < 0.86:  # retained at follow-up
            effect = 0.55 if group[i] == 2 else 0.2
            dep = np.clip(latent_dep[i] - effect + rng.normal(0, 0.3), 0, 3)
            anx = np.clip(latent_anx[i] - effect * 0.8 + rng.normal(0, 0.3), 0, 3)
            f = {"record_id": rid, "redcap_event_name": "followup_arm_1", "enrollment_complete": ""}
            if rng.random() < 0.04:  # follow-up row exists but survey never started
                f["wellbeing_survey_complete"] = 0
            else:
                f.update(_survey(rng, dep, anx, consent[i] + pd.Timedelta(days=int(rng.integers(80, 105)))))
            fu_rows.append(f)

    df = pd.DataFrame(base_rows + fu_rows)
    cols = [c for c in df.columns if c not in {"enrollment_complete", "wellbeing_survey_complete"}]
    df = df[cols[:2] + [c for c in cols[2:]] + ["enrollment_complete", "wellbeing_survey_complete"]]
    df = df.astype(object).where(df.notna(), "")
    df = df.map(lambda v: (f"{v:g}" if isinstance(v, float) else str(v)))
    df = _plant_problems(df, rng)
    df = df.sort_values(["record_id", "redcap_event_name"], kind="stable").reset_index(drop=True)
    # one fully blank line, as happens when exports are edited in Excel
    df = pd.concat([df.iloc[:50], pd.DataFrame([[""] * df.shape[1]], columns=df.columns), df.iloc[50:]],
                   ignore_index=True)

    data_path = out / "demo_redcap_export.csv"
    df.to_csv(data_path, index=False, quoting=csv.QUOTE_MINIMAL)
    dd = pd.DataFrame(dictionary_rows()).reindex(columns=DD_HEADER).astype(object).fillna("")
    dd_path = out / "demo_data_dictionary.csv"
    dd.to_csv(dd_path, index=False)
    return {"data": str(data_path), "dictionary": str(dd_path), "rows": len(df)}


def _survey(rng, dep, anx, date) -> dict:
    phq = _likert(rng, np.array([dep]), 9, 3)[0]
    phq[8] = int(np.clip(np.rint(dep - 1.6 + rng.normal(0, 0.6)), 0, 3))  # item 9 is endorsed far less often
    gad = _likert(rng, np.array([anx]), 7, 3)[0]
    stress = np.clip(0.55 * dep + 0.55 * anx + 0.6, 0, 4)
    pss = _likert(rng, np.array([stress]), 10, 4, reverse=(4, 5, 7, 8))[0]
    r = {"survey_date": date.strftime("%Y-%m-%d")}
    r.update({f"phq9_{k + 1}": int(v) for k, v in enumerate(phq)})
    r.update({f"gad7_{k + 1}": int(v) for k, v in enumerate(gad)})
    r.update({f"pss_{k + 1}": int(v) for k, v in enumerate(pss)})
    r["sleep_hours"] = f"{np.clip(rng.normal(7 - 0.6 * dep, 1.1), 3, 11):.1f}"
    r["activity_days"] = int(np.clip(rng.normal(3.5 - 0.5 * dep, 1.8), 0, 7))
    r["wellbeing_survey_complete"] = 2
    return r


def _future_date(years: int = 5) -> str:
    """A date that is always in the future relative to when the code runs."""
    return (pd.Timestamp.today().normalize() + pd.DateOffset(years=years)).strftime("%Y-%m-%d")


def _plant_problems(df: pd.DataFrame, rng) -> pd.DataFrame:
    base = df.index[df["redcap_event_name"] == "baseline_arm_1"].tolist()
    survey = df.index[df["survey_date"] != ""].tolist()
    pick = lambda pool, k: list(rng.choice(pool, k, replace=False))  # noqa: E731
    item_cols = [c for c in df.columns if c.startswith(("phq9_", "gad7_", "pss_"))]

    for idx in pick(survey, 25):  # missing-data codes
        df.at[idx, rng.choice(item_cols)] = rng.choice(["-99", "-88"])
    for idx in pick(survey, 4):  # out-of-range item values
        df.at[idx, rng.choice([c for c in item_cols if c.startswith("phq9_")])] = rng.choice(["4", "7"])
    for idx in pick(survey, 6):  # a few skipped items (blank)
        df.at[idx, rng.choice(item_cols)] = ""
    s = pick(survey, 3)
    df.at[s[0], "sleep_hours"] = "7 hrs"
    df.at[s[1], "activity_days"] = "seven"
    df.at[s[2], "sleep_hours"] = "NA"
    b = pick(base, 14)
    df.at[b[0], "height_cm"] = "1.65"      # metres instead of cm
    df.at[b[1], "weight_kg"] = "650"       # typo
    df.at[b[2], "consent_date"] = "2024-02-30"  # impossible date
    df.at[b[3], "consent_date"] = _future_date()  # in the future, whenever the demo is run
    df.at[b[4], "income"] = "6"            # not a coded choice
    df.at[b[5], "education"] = " 3 "       # stray whitespace
    df.at[b[6], "sex"] = ""                # required field blank
    df.at[b[7], "conditions_notes"] = "Asthma since childhood, call me at 555-123-4567"
    males = [i for i in base if df.at[i, "sex"] == "2"]
    for idx in pick(males, 2):            # answered although branching hides it
        df.at[idx, "pregnant"] = "0"
    nons = [i for i in base if df.at[i, "smoker"] == "0"]
    df.at[pick(nons, 1)[0], "cigs_per_day"] = "5"
    smok = [i for i in base if df.at[i, "smoker"] == "1"]
    for idx in pick(smok, 2):              # required but missing
        df.at[idx, "cigs_per_day"] = ""
    fu = df.index[(df["redcap_event_name"] == "followup_arm_1") & (df["survey_date"] != "")].tolist()
    df.at[pick(fu, 1)[0], "survey_date"] = "2022-12-01"  # follow-up before consent
    # an exact duplicate row (double import)
    df = pd.concat([df, df.loc[[b[8]]]], ignore_index=True)
    return df

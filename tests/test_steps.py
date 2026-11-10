"""Behaviour of individual pipeline steps, run through the public `run` API on small inputs."""
import numpy as np
import pandas as pd
import pytest

from hspipe import run
from hspipe.config import ConfigError

from hspipe.synth import _future_date

from .conftest import dd_row

FREQ = "0, Not at all | 1, Several days | 2, More than half | 3, Nearly every day"


def issues(ctx, rule=None):
    df = ctx.issues_frame()
    return df if rule is None else df[df["rule"] == rule]


def test_missing_codes_and_reasons(make_project):
    rows = [{"record_id": 1, "q": "-99", "income": "99", "w": "99"},
            {"record_id": 2, "q": "NA", "income": "2", "w": "70"},
            {"record_id": 3, "q": "2", "income": "", "w": "-88"}]
    cfg = make_project(rows, missing={"labelled_codes": {-99: "Refused", -88: "Don't know"},
                                      "variable_codes": {"income": {99: "Prefer not"}}})
    ctx = run(cfg, write=False)
    assert ctx.df["q"].isna().tolist() == [True, True, False]
    assert ctx.df["income"].isna().tolist() == [True, False, True]
    assert ctx.df["w"].tolist()[:2] == [99, 70]  # 99 is a real value for w
    r = ctx.reasons
    assert r.at[0, "q"] == "code: Refused" and r.at[0, "income"] == "code: Prefer not"
    assert r.at[2, "w"] == "code: Don't know"


def test_dictionary_validation(make_project):
    dd = [dd_row("record_id"),
          dd_row("age", ftype="text", val="integer", vmin="18", vmax="65"),
          dd_row("sex", ftype="radio", choices="1, F | 2, M"),
          dd_row("visit", ftype="text", val="date_ymd", vmin="2020-01-01", vmax="today")]
    rows = [{"record_id": 1, "age": "17", "sex": "1", "visit": "2021-05-01"},
            {"record_id": 2, "age": "30.5", "sex": "3", "visit": "2019-12-31"},
            {"record_id": 3, "age": "abc", "sex": "2", "visit": "2021-02-30"},
            {"record_id": 4, "age": "40", "sex": " 2 ", "visit": _future_date()}]
    ctx = run(make_project(rows, dd), write=False)
    got = set(zip(issues(ctx)["record_id"].astype(str), issues(ctx)["rule"]))
    assert {("1", "range:min"), ("2", "type:integer"), ("2", "choices"), ("2", "range:min"),
            ("3", "type:numeric"), ("3", "type:date"), ("4", "range:max")} <= got
    assert ctx.df["sex"].tolist()[3] == 2  # whitespace stripped, kept
    assert ctx.df["age"].isna().tolist() == [True, True, True, False]
    assert pd.api.types.is_datetime64_any_dtype(ctx.df["visit"])
    # row numbers point at the raw file (header = row 1)
    assert issues(ctx, "type:numeric")["row"].tolist() == [4]


def test_flag_mode_keeps_values(make_project):
    dd = [dd_row("record_id"), dd_row("age", val="integer", vmin="18", vmax="65")]
    ctx = run(make_project([{"record_id": 1, "age": "10"}], dd, validation={"on_invalid": "flag"}), write=False)
    assert ctx.df["age"].tolist() == [10]
    assert issues(ctx)["action"].tolist() == ["flagged"]


def test_branching_and_required(make_project):
    dd = [dd_row("record_id"),
          dd_row("smoker", ftype="yesno"),
          dd_row("cigs", val="integer", branch="[smoker] = '1'", req="y"),
          dd_row("why", ftype="checkbox", choices="1, A | 2, B", branch="[smoker] = '1'")]
    rows = [{"record_id": 1, "smoker": "1", "cigs": "10", "why___1": "1", "why___2": "0"},
            {"record_id": 2, "smoker": "1", "cigs": "", "why___1": "0", "why___2": "0"},   # required missing
            {"record_id": 3, "smoker": "0", "cigs": "", "why___1": "0", "why___2": "0"},   # skipped
            {"record_id": 4, "smoker": "0", "cigs": "5", "why___1": "0", "why___2": "0"}]  # answered but hidden
    ctx = run(make_project(rows, dd), write=False)
    assert issues(ctx, "required")["record_id"].astype(str).tolist() == ["2"]
    assert issues(ctx, "branching")["record_id"].astype(str).tolist() == ["4"]
    assert ctx.reasons.at[2, "cigs"] == "skipped (branching)"
    # unchecked boxes of a hidden question are "not asked", not "No"
    assert np.isnan(ctx.df.at[2, "why___1"]) and ctx.df.at[1, "why___1"] == 0


def test_form_not_started(make_project):
    dd = [dd_row("record_id", form="a"), dd_row("x", form="a"),
          dd_row("q1", form="b", req="y"), dd_row("q2", form="b")]
    rows = [{"record_id": 1, "x": "1", "q1": "", "q2": ""},
            {"record_id": 2, "x": "1", "q1": "", "q2": "3"}]
    ctx = run(make_project(rows, dd), write=False)
    assert ctx.reasons.at[0, "q1"] == "form not started"
    assert issues(ctx, "required")["record_id"].astype(str).tolist() == ["2"]


def test_duplicates_policies(make_project):
    rows = [{"record_id": 1, "a": "1", "b": ""}, {"record_id": 1, "a": "1", "b": "2"},
            {"record_id": 2, "a": "1", "b": "1"}, {"record_id": "", "a": "5", "b": "5"}]
    ctx = run(make_project(rows, validation={"duplicates": "keep_most_complete"}), write=False)
    assert len(ctx.df) == 2 and ctx.df.loc[ctx.df.record_id == "1", "b"].item() == 2
    assert len(issues(ctx, "id:blank")) == 1
    ctx = run(make_project(rows), write=False)  # default: flag only
    assert len(ctx.df) == 3 and len(issues(ctx, "duplicate")) == 2


def test_custom_rules(make_project):
    rows = [{"record_id": 1, "a": "5", "b": "3", "d1": "2020-01-01", "d2": "2019-01-01"},
            {"record_id": 2, "a": "50", "b": "9", "d1": "2020-01-01", "d2": "2021-01-01"}]
    rules = [{"variable": "a", "range": [0, 10]},
             {"check": "b <= a", "message": "b must not exceed a"},
             {"check": "d2 >= d1", "name": "date_order"},
             {"check": "[a] < 40", "name": "redcap_style", "severity": "warning"},
             {"variable": "b", "allowed": [3, 4]}]
    ctx = run(make_project(rows, validation={"rules": rules}), write=False)
    df = issues(ctx)
    pairs = set(zip(df["record_id"].astype(str), df["rule"]))
    assert ("2", "range:max") in pairs and ("1", "date_order") in pairs and ("2", "allowed") in pairs
    # the a-range rule removed 50 first, so "b <= a" and the REDCap-style rule can't be evaluated for row 2
    assert ("2", "check") not in pairs and ("2", "redcap_style") not in pairs


def test_deidentify_and_age(make_project, monkeypatch, tmp_path):
    dd = [dd_row("record_id"), dd_row("name", ident="y"), dd_row("dob", val="date_ymd", ident="y"),
          dd_row("visit", val="date_ymd"), dd_row("comments", ftype="notes"), dd_row("home_phone")]
    rows = [{"record_id": "A1", "name": "Test Person", "dob": "1990-06-15", "visit": "2020-06-14",
             "comments": "reach me at someone@example.org", "home_phone": "555 123 4567"}]
    cfg = make_project(rows, dd, deidentify={"derive_age": [{"dob": "dob", "reference": "visit", "name": "age"}],
                                             "hash_id": {"enabled": True, "salt": "s3cret"}})
    ctx = run(cfg)
    assert "name" not in ctx.df and "dob" not in ctx.df
    assert ctx.df["age"].item() == 29
    assert ctx.df["record_id"].item() != "A1" and len(ctx.df["record_id"].item()) == 12
    warn = " ".join(str(e) for e in ctx.log if e["step"] == "deidentify_warning")
    assert "home_phone" in warn and "comments" in warn
    xw = pd.read_csv(tmp_path / "out" / "restricted" / "id_crosswalk.csv")
    assert xw["original_id"].tolist() == ["A1"]
    # identifier values never echoed in issues
    cfg2 = make_project([{**rows[0], "dob": "notadate"}], dd)
    ctx2 = run(cfg2, write=False)
    assert issues(ctx2, "type:date")["value"].tolist() == ["<redacted>"]


def test_hash_requires_salt(make_project, monkeypatch):
    monkeypatch.delenv("HSP_SALT", raising=False)
    cfg = make_project([{"record_id": 1}], deidentify={"hash_id": {"enabled": True}})
    with pytest.raises(ValueError, match="salt"):
        run(cfg, write=False)


def test_reshape_long_to_wide(make_project):
    rows = [{"record_id": 1, "redcap_event_name": "base", "sex": "1", "score": "4"},
            {"record_id": 1, "redcap_event_name": "fu", "sex": "", "score": "2"},
            {"record_id": 2, "redcap_event_name": "base", "sex": "2", "score": "5"},
            {"record_id": 2, "redcap_event_name": "other", "sex": "", "score": "9"}]
    cfg = make_project(rows, input={"event_column": "redcap_event_name"},
                       longitudinal={"reshape": True, "event_map": {"base": "t1", "fu": "t2"}})
    ctx = run(cfg, write=False)
    assert list(ctx.df.columns) == ["record_id", "sex", "score_t1", "score_t2"]
    assert ctx.df["score_t2"].tolist()[0] == 2 and np.isnan(ctx.df["score_t2"].tolist()[1])
    assert ctx.reasons.at[1, "score_t2"] == "form not started"


def test_recode_and_derive(make_project):
    rows = [{"record_id": i, "x": str(v), "h": "170", "w": str(w)} for i, (v, w) in enumerate([(1, 50), (5, 90), (3, 70)])]
    cfg = make_project(rows, recode=[
        {"variable": "x", "reverse": {"min": 1, "max": 5}, "into": "x_r"},
        {"variable": "x", "map": {1: 0, 5: 1}, "else": "missing", "into": "x_bin", "labels": {0: "low", 1: "high"}},
        {"variable": "bmi", "after_derive": True, "bins": [0, 18.5, 25, 30, ".inf"],
         "labels": ["U", "N", "Ow", "Ob"], "into": "bmi_cat"},
    ], derive=[{"name": "bmi", "expr": "w / (h/100)**2", "round": 1}])
    ctx = run(cfg, write=False)
    assert ctx.df["x_r"].tolist() == [5, 1, 3]
    assert ctx.df["x_bin"].tolist()[:2] == [0, 1] and np.isnan(ctx.df["x_bin"].tolist()[2])
    assert ctx.df["bmi"].tolist() == [17.3, 31.1, 24.2]
    assert ctx.df["bmi_cat"].tolist() == [1, 4, 2]
    assert ctx.meta["bmi_cat"].value_labels[4.0] == "Ob"


def test_scale_scoring_rules(make_project):
    items = {f"p{i}": v for i, v in enumerate([1, 1, 1, 1, 1, 1, 1, 1, 1], start=1)}
    rows = [
        {"record_id": 1, **items},                               # complete: 9
        {"record_id": 2, **{**items, "p3": ""}},                  # 1 missing -> prorated 9
        {"record_id": 3, **{**items, "p3": "", "p4": ""}},        # 2 missing -> not scored
        {"record_id": 4, **{**items, "p9": "2"}},                 # item 9 endorsed, total 10
        {"record_id": 5, **{**items, "p2": "7"}},                 # out of range -> removed, prorated
    ]
    cfg = make_project(rows, scales=[{"name": "phq", "instrument": "phq9", "items": "p{i}"}])
    ctx = run(cfg, write=False)
    assert ctx.df["phq"].tolist()[:2] == [9, 9] and np.isnan(ctx.df["phq"].tolist()[2])
    assert ctx.df["phq"].tolist()[3:] == [10, 9]
    assert ctx.df["phq_cat"].tolist()[3] == 3  # 10 -> Moderate (3rd band)
    assert ctx.df["phq_item9_flag"].tolist() == [1, 1, 1, 1, 1]  # every row endorses item 9 here (value 1)
    assert len(issues(ctx, "scale:item_range")) == 1
    assert ctx.reasons.at[2, "phq"] == "insufficient items"


def test_reverse_items_and_strict_sum(make_project):
    rows = [{"record_id": 1, "a": "1", "b": "5", "c": "1"}, {"record_id": 2, "a": "1", "b": "", "c": "1"}]
    cfg = make_project(rows, scales=[
        {"name": "s", "items": ["a", "b", "c"], "item_range": [1, 5], "reverse": [2], "method": "sum",
         "prorate": False, "subscales": {"ab": [1, 2]}},
        {"name": "m", "items": ["a", "b", "c"], "item_range": [1, 5], "reverse": ["b"], "method": "mean",
         "min_valid": 0.6}])
    ctx = run(cfg, write=False)
    assert ctx.df["s"].tolist()[0] == 3 and np.isnan(ctx.df["s"].tolist()[1])  # strict: any missing -> missing
    assert ctx.df["m"].tolist() == [1, 1]
    assert ctx.df["s_ab"].tolist()[0] == 2


def test_scale_config_errors(make_project):
    with pytest.raises(ConfigError, match="Unknown instrument"):
        run(make_project([{"record_id": 1, "a": 1}], scales=[{"name": "x", "instrument": "nope"}]), write=False)
    with pytest.raises(ConfigError, match="not found"):
        run(make_project([{"record_id": 1, "a": 1}], scales=[{"name": "x", "items": ["a", "zz"]}]), write=False)


def test_type_inference_without_dictionary(make_project):
    rows = [{"record_id": i, "num": v, "visit_date": d, "txt": t}
            for i, (v, d, t) in enumerate([("1", "2020-01-01", "a"), ("2.5", "2020-02-01", "b"),
                                           ("3", "2020-03-01", "c"), ("x", "2020-04-01", "d"),
                                           ("4", "2020-05-01", "e"), ("5", "2020-06-01", "f"),
                                           ("6", "2020-07-01", "g"), ("7", "2020-08-01", "h"),
                                           ("8", "2020-09-01", "i"), ("9", "2020-10-01", "j")])]
    ctx = run(make_project(rows), write=False)
    assert pd.api.types.is_numeric_dtype(ctx.df["num"])
    assert pd.api.types.is_datetime64_any_dtype(ctx.df["visit_date"])
    assert ctx.meta["txt"].kind == "text"
    assert issues(ctx)["value"].tolist() == ["x"]


def test_table1_tests(make_project):
    rng = np.random.default_rng(1)
    rows = [{"record_id": i, "grp": str(1 + i % 2), "y": f"{rng.normal(10 + 3 * (i % 2), 1):.2f}",
             "c": str(1 + (i % 3 == 0))} for i in range(60)]
    cfg = make_project(rows, describe={"group_by": "grp", "categorical": ["c"]})
    ctx = run(cfg, write=False)
    t1 = ctx.tables["table1"]
    yrow = t1[t1["Characteristic"].str.startswith("y")].iloc[0]
    assert yrow["test"] == "Welch t-test" and yrow["p"] == "<0.001"
    assert t1[t1["Characteristic"].str.startswith("c,")]["test"].iloc[0].startswith(("Chi", "Fisher"))


@pytest.mark.parametrize("fmt", ["xlsx", "sav", "semicolon"])
def test_input_formats(tmp_path, fmt):
    df = pd.DataFrame({"record_id": ["1", "2"], "age": ["30", "41"], "note": ["é ok", "x"]})
    if fmt == "xlsx":
        path = tmp_path / "d.xlsx"
        df.to_excel(path, index=False)
    elif fmt == "sav":
        pyreadstat = pytest.importorskip("pyreadstat")
        path = tmp_path / "d.sav"
        pyreadstat.write_sav(df.assign(age=df.age.astype(float)), str(path))
    else:
        path = tmp_path / "d.csv"
        path.write_text("record_id;age;note\n1;30;é ok\n2;41;x\n", encoding="cp1252")
    ctx = run({"input": {"data": str(path)}, "project": {"output_dir": str(tmp_path / "o")}}, write=False)
    assert ctx.df["age"].tolist() == [30, 41]
    assert ctx.df["note"].tolist()[0] == "é ok"

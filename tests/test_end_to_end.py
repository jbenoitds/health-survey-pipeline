"""Full run on the synthetic demo project: every planted problem must be found,
no identifier may reach the outputs, and every output file must open."""
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from hspipe import run
from hspipe.cli import main
from hspipe.config import load_config
from hspipe.synth import make_demo

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "hspipe" / "templates"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    make_demo(root / "data", n=150, seed=3)
    shutil.copy(TEMPLATES / "demo_config.yaml", root / "config.yaml")
    cfg = load_config(root / "config.yaml")
    try:
        import pyreadstat  # noqa: F401
    except ImportError:
        cfg["outputs"]["spss"] = False
    ctx = run(cfg)
    return root, ctx


def test_planted_problems_found(demo):
    _root, ctx = demo
    iss = ctx.issues_frame()
    found = set(zip(iss["variable"], iss["rule"]))
    expected = {("height_cm", "range:min"), ("weight_kg", "range:max"), ("consent_date", "type:date"),
                ("consent_date", "range:max"), ("income", "choices"), ("sex", "required"),
                ("pregnant", "branching"), ("cigs_per_day", "branching"), ("cigs_per_day", "required"),
                ("record_id", "duplicate"), ("sleep_hours", "type:numeric"), ("activity_days", "type:numeric"),
                ("survey_date_t2", "check")}
    assert expected <= found, expected - found
    assert any(r == "choices" and v.startswith("phq9_") for v, r in found)  # out-of-range items
    assert len(ctx.df) == 150 and ctx.df["record_id"].is_unique


def test_no_identifiers_in_outputs(demo):
    root, ctx = demo
    for col in ("first_name", "last_name", "email", "phone", "dob", "conditions_notes"):
        assert col not in ctx.df.columns
    text = (root / "output" / "cleaned_data.csv").read_text() + (root / "output" / "report.html").read_text()
    raw = pd.read_csv(root / "data" / "demo_redcap_export.csv", dtype=str, keep_default_na=False)
    for email in raw["email"][raw["email"] != ""].head(30):
        assert email not in text
    assert "555-123-4567" not in text


def test_scales_and_tables(demo):
    _root, ctx = demo
    sc = ctx.tables["scales"].set_index("scale")
    assert set(sc.index) == {"phq9_t1", "phq9_t2", "gad7_t1", "gad7_t2", "pss_t1", "pss_t2"}
    assert (sc["alpha"] > 0.7).all()  # reverse-keyed PSS items handled correctly
    tp = ctx.tables["timepoints"]
    assert set(tp["variable"]) == {"phq9", "gad7", "pss"} and (tp["mean_change"] < 0).all()
    t1 = ctx.tables["table1"]
    assert {"Overall", "Usual care", "Intervention", "p"} <= set(t1.columns)
    miss = ctx.tables["missingness"].set_index("variable")
    assert miss.at["pregnant", "skipped_by_design"] > 0  # males skip the pregnancy question
    assert miss.at["cigs_per_day", "skipped_by_design"] > 100


def test_output_files_open(demo):
    root, _ctx = demo
    out = root / "output"
    for name in ("cleaned_data.csv", "validation_issues.csv", "codebook.csv", "missingness.csv", "table1.csv",
                 "report.html", "summary_tables.xlsx", "run_log.json"):
        assert (out / name).exists(), name
    xl = pd.ExcelFile(out / "summary_tables.xlsx", engine="openpyxl")
    assert {"Overview", "Table 1", "Scales", "Missingness", "Validation issues", "Codebook"} <= set(xl.sheet_names)
    log = json.loads((out / "run_log.json").read_text())
    assert log["clean_shape"][0] == 150
    if (out / "cleaned_data.sav").exists():
        import pyreadstat
        df, meta = pyreadstat.read_sav(str(out / "cleaned_data.sav"))
        assert meta.variable_value_labels["group"] == {1.0: "Usual care", 2.0: "Intervention"}
        assert meta.column_names_to_labels["phq9_t1"].startswith("PHQ-9")


def test_cli_commands(tmp_path, capsys):
    assert main(["instruments"]) == 0
    assert "phq9" in capsys.readouterr().out
    assert main(["init", str(tmp_path / "proj")]) == 0
    assert (tmp_path / "proj" / "config.yaml").exists() and (tmp_path / "proj" / ".gitignore").exists()
    assert main(["demo", str(tmp_path / "d"), "-n", "40"]) == 0
    assert (tmp_path / "d" / "output" / "report.html").exists()
    assert main(["check", str(tmp_path / "d" / "config.yaml")]) == 0
    assert main(["profile", str(tmp_path / "d" / "data" / "demo_redcap_export.csv"),
                 "--event", "redcap_event_name", "-o", str(tmp_path / "prof")]) == 0
    assert (tmp_path / "prof" / "report.html").exists()
    assert main(["run", str(tmp_path / "nope.yaml")]) == 2  # friendly error, no traceback


def test_template_config_parses(tmp_path):
    shutil.copy(TEMPLATES / "project_template.yaml", tmp_path / "config.yaml")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg["timepoints"] == ["t1", "t2"] and cfg["recode"] == [] and cfg["scales"] == []


def test_hashed_ids_consistent(tmp_path, monkeypatch):
    make_demo(tmp_path / "data", n=60, seed=11)
    cfg = load_config(shutil.copy(TEMPLATES / "demo_config.yaml", tmp_path / "config.yaml"))
    cfg["deidentify"]["hash_id"]["enabled"] = True
    cfg["outputs"]["spss"] = False
    monkeypatch.setenv("HSP_SALT", "unit-test-salt")
    run(cfg)
    out = tmp_path / "output"
    clean = pd.read_csv(out / "cleaned_data.csv", dtype=str)
    shared = pd.read_csv(out / "validation_issues.csv", dtype=str)
    private = pd.read_csv(out / "restricted" / "validation_issues_original_ids.csv", dtype=str)
    xw = pd.read_csv(out / "restricted" / "id_crosswalk.csv", dtype=str)
    assert set(private["record_id"]) <= set(xw["original_id"])          # restricted file: real IDs
    assert set(shared["record_id"]) <= set(clean["record_id"])          # shared file: same pseudonyms as data
    assert not set(xw["original_id"]) & set(clean["record_id"])

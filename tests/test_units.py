import numpy as np
import pandas as pd
import pytest

from hspipe.branching import BranchingError, compile_logic
from hspipe.config import ConfigError, build_config, expand_template
from hspipe.dictionary import DataDictionary, checkbox_column, parse_choices
from hspipe.missing import _canon
from hspipe.scoring import cronbach_alpha


# --- dictionary ---------------------------------------------------------------

def test_parse_choices_and_special_types():
    assert parse_choices("1, Yes | 0, No") == {"1": "Yes", "0": "No"}
    assert parse_choices("1=Low|2=High") == {"1": "Low", "2": "High"}
    assert parse_choices("", "yesno") == {"1": "Yes", "0": "No"}
    assert parse_choices("[a]+[b]", "calc") == {}


def test_checkbox_column_names():
    assert checkbox_column("race", "2") == "race___2"
    assert checkbox_column("q", "-99") == "q____99"


def test_generic_codebook_kinds():
    cb = pd.DataFrame({"variable": ["age", "sex", "visit_date", "notes"],
                       "type": ["integer", "categorical", "date", "text"],
                       "choices": ["", "1=F|2=M", "", ""], "identifier": ["", "", "", "y"]})
    dd = DataDictionary.from_frame(cb)
    assert dd["age"].kind == "numeric" and dd["age"].is_integer
    assert dd["sex"].kind == "categorical" and dd["sex"].choices == {"1": "F", "2": "M"}
    assert dd["visit_date"].kind == "date"
    assert dd.identifier_columns() == ["notes"]


def test_unrecognised_dictionary():
    with pytest.raises(ValueError):
        DataDictionary.from_frame(pd.DataFrame({"foo": [1]}))


# --- branching ----------------------------------------------------------------

@pytest.fixture
def bdf():
    return pd.DataFrame({"sex": ["1", "2", "", "1"], "age": ["17", "30", "", "45"],
                         "chk___3": ["1", "0", "0", "0"]})


@pytest.mark.parametrize("expr,expected", [
    ("[sex] = '1'", [True, False, False, True]),
    ("[sex] = 1", [True, False, False, True]),
    ("[sex] <> ''", [True, True, False, True]),
    ("[age] >= 18 and [sex] = '1'", [False, False, False, True]),
    ("[chk(3)] = '1' or [age] > 40", [True, False, False, True]),
    ("([sex]='2' or [sex]='1') and not([age] < 18)", [False, True, False, True]),
    ("[event_1_arm_1][sex] = '2'", [False, True, False, False]),
    ("if([sex]='1', 1, 0) = 1", [True, False, False, True]),
])
def test_branching(bdf, expr, expected):
    assert compile_logic(expr).evaluate(bdf).tolist() == expected


def test_branching_unsupported(bdf):
    with pytest.raises(BranchingError):
        compile_logic("[user-name] = 'x'").evaluate(bdf)
    with pytest.raises(BranchingError):
        compile_logic("rounddown([age]) > 3").evaluate(bdf)
    with pytest.raises(BranchingError):
        compile_logic("[nothere] = '1'").evaluate(bdf)


# --- missing codes / config --------------------------------------------------

def test_canon():
    assert _canon("-99") == _canon(-99) == _canon("-99.0") == "-99"
    assert _canon(" NA ") == "NA"


def test_config_validation(tmp_path):
    with pytest.raises(ConfigError):
        build_config({"input": {}})
    with pytest.raises(ConfigError):
        build_config({"input": {"data": "x.csv"}, "typo_section": {}})
    with pytest.raises(ConfigError):
        build_config({"input": {"data": "x.csv"}, "validation": {"on_invalid": "delete"}})
    cfg = build_config({"input": {"data": "x.csv"}, "longitudinal": {"reshape": True, "event_map": {"a": "t1"}}})
    assert cfg["timepoints"] == ["t1"]
    assert expand_template("phq_{t}", ["t1", "t2"]) == ["phq_t1", "phq_t2"]
    with pytest.raises(ConfigError):
        expand_template("phq_{t}", [])


# --- alpha --------------------------------------------------------------------

def test_cronbach_alpha_known_value():
    # perfectly parallel items -> alpha 1; independent noise -> ~0
    x = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5], "c": [1, 2, 3, 4, 5]})
    assert cronbach_alpha(x)[0] == pytest.approx(1.0)
    rng = np.random.default_rng(0)
    noise = pd.DataFrame(rng.normal(size=(2000, 5)))
    assert abs(cronbach_alpha(noise)[0]) < 0.1
    # textbook example: 3 items
    t = pd.DataFrame({"i1": [2, 3, 3, 4, 5], "i2": [1, 3, 2, 4, 4], "i3": [2, 2, 3, 5, 5]})
    k = 3
    expected = k / (k - 1) * (1 - t.var(ddof=1).sum() / t.sum(axis=1).var(ddof=1))
    assert cronbach_alpha(t)[0] == pytest.approx(expected)

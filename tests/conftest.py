import pandas as pd
import pytest

from hspipe.synth import DD_HEADER


def write_csv(path, rows, columns=None):
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False)
    return path


def dd_row(name, form="f", ftype="text", label="", choices="", val="", vmin="", vmax="", ident="", branch="",
           req=""):
    return {"Variable / Field Name": name, "Form Name": form, "Field Type": ftype, "Field Label": label or name,
            "Choices, Calculations, OR Slider Labels": choices, "Text Validation Type OR Show Slider Number": val,
            "Text Validation Min": vmin, "Text Validation Max": vmax, "Identifier?": ident,
            "Branching Logic (Show field only if...)": branch, "Required Field?": req}


@pytest.fixture
def make_project(tmp_path):
    """Write data + optional dictionary to tmp_path and return a raw config dict."""
    def _make(data_rows, dd_rows=None, **overrides):
        data = write_csv(tmp_path / "data.csv", data_rows)
        cfg = {"project": {"name": "test", "output_dir": str(tmp_path / "out")},
               "input": {"data": str(data)}}
        if dd_rows is not None:
            dd = pd.DataFrame(dd_rows).reindex(columns=DD_HEADER).astype(object).fillna("")
            dd.to_csv(tmp_path / "dd.csv", index=False)
            cfg["input"]["data_dictionary"] = str(tmp_path / "dd.csv")
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        return cfg
    return _make

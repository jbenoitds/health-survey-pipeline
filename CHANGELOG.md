# Changelog

## 0.2.0 (2026-11-11)

Updated to current library versions.

* Minimum versions are now Python 3.10, pandas 2.1, numpy 1.25, scipy 1.11, PyYAML 6.0, openpyxl 3.1,
  pyreadstat 1.2 and pytest 7.4. Support for the 2020 releases added in 0.1.1 is dropped.
* Removed `hspipe/_compat.py`: the code now uses `DataFrame.map`, `pd.to_datetime(format="mixed")` and
  the `.pvalue` attribute of scipy results directly.
* Packaging metadata moved from `setup.cfg` + `setup.py` to `pyproject.toml`.
* Removed `ci/constraints-2020.txt`. CI now runs on Ubuntu and Windows with Python 3.10, 3.12 and 3.13.

## 0.1.1 (2026-11-06)

Runs on the 2020 toolchain.

* Minimum versions are now 2020 releases: Python 3.8, pandas 1.1, numpy 1.19, scipy 1.5, PyYAML 5.3,
  openpyxl 3.0, pyreadstat 1.0, pytest 6.0. Current releases (pandas 2.x / 3.x) still work.
* Packaging moved to `setup.cfg` + `setup.py`, so `pip install -e .` works with pip 20.x / setuptools 49+.
* New `hspipe/_compat.py`: element-wise map, date parsing and scipy p-value shims for old and new library
  versions.
* `.xlsx` files are read with openpyxl explicitly (pandas < 1.2 otherwise asks for xlrd).
* `ci/constraints-2020.txt` pins the exact library versions released by 31 Dec 2020. CI now runs a 2020 job
  (Python 3.8, pip 20.3.3, setuptools 51.1.1, Ubuntu + Windows) and a current-versions job.

## 0.1.0

First release.

* REDCap data dictionary and generic codebook support
* Missing-value codes with reasons; skip-logic-aware missingness
* Dictionary, required, duplicate and custom validation rules
* De-identification (identifiers, DOB to age, salted pseudonyms)
* Long-to-wide reshaping for longitudinal REDCap exports
* Recode / derive rules, scale scoring with 7 built-in instruments, Cronbach's alpha, safety flags
* Table 1, paired timepoint change, missingness, codebook
* CSV, SPSS, Excel and HTML outputs; `hsp` CLI (init, run, check, profile, demo, instruments)

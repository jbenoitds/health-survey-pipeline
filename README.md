# health-survey-pipeline

Cleaning, validation, scoring and descriptive reports for REDCap, survey and EMR exports, driven by one YAML file.

As a research assistant I spent a large part of every study cleaning REDCap exports by hand or with one-off SPSS syntax: missing-value codes, skip logic, out-of-range answers, duplicate rows, identifiers that must not leave the team. Then the next wave of data arrived and it all had to be redone. This package is what I wrote so that a study is described once, in a config file, and every new export goes through the same documented steps.

`hsp run config.yaml` will:

1. read the export and its data dictionary (REDCap dictionary, a simple codebook, or nothing),
2. clean it: missing codes, whitespace, duplicates, types,
3. validate it against the dictionary, the REDCap branching logic and your own rules, logging every problem,
4. de-identify it: drop identifiers, turn date of birth into age, optionally pseudonymise IDs,
5. reshape longitudinal data to one row per participant,
6. recode, derive and score questionnaires (PHQ-9, GAD-7, PSS-10, EPDS, WHO-5, K10, or your own),
7. describe it: Table 1, missing data by reason, change across timepoints, codebook,
8. write CSV, SPSS `.sav` and Excel files and a self-contained HTML report.

Version 0.2.0, MIT license.

## Project history

The first version dates from May 2020, written for the REDCap studies I was working on, without any AI tool. It stayed on my laptop, outside of any version control, until November 2026. When I published it, I also updated it to current libraries (pandas 2, scipy 1.11+, Python 3.10+; the details are in the [CHANGELOG](CHANGELOG.md)). This README was rewritten with help from Claude, an AI assistant; the code is mine.

## Installation

Python 3.10 or newer.

```bash
pip install -e ".[spss]"     # drop [spss] if you don't need .sav files
```

This installs the `hsp` command.

## Quick start

```bash
hsp demo
```

builds a fake two-wave REDCap project (240 invented participants) and runs everything. Open `hsp_demo/output/report.html`. The demo data have problems planted on purpose (out-of-range values, missing codes, skip-logic violations, a duplicate, impossible dates, a phone number in a comment field) and the tests check that each one is caught.

For a real study:

```bash
hsp init my_study                 # commented config.yaml, data/, output/, .gitignore
# copy the export and the data dictionary into my_study/data/, then edit config.yaml
hsp check my_study/config.yaml    # dry run, writes nothing
hsp run   my_study/config.yaml
```

Also: `hsp profile export.csv --dictionary dict.csv` for a quick look at any file, and `hsp instruments` for the built-in questionnaires. From Python:

```python
from hspipe import run
ctx = run("my_study/config.yaml")
ctx.df, ctx.tables["table1"], ctx.issues_frame()
```

## Example config

```yaml
project: {name: "Two-wave wellbeing survey", output_dir: output}

input:
  data: data/export.csv
  data_dictionary: data/data_dictionary.csv
  id_column: record_id
  event_column: redcap_event_name

timepoints: [t1, t2]                       # {t} expands to each of these

missing:
  labelled_codes: {-99: Refused, -88: Don't know}
  variable_codes: {income: {99: Prefer not to answer}}

longitudinal:
  reshape: true
  event_map: {baseline_arm_1: t1, followup_arm_1: t2}

deidentify:
  derive_age: [{dob: dob, reference: consent_date, name: age_years}]

validation:
  duplicates: keep_most_complete
  rules:
    - {variable: age_years, range: [18, 50]}
    - {check: "survey_date_{t} >= consent_date", message: "Survey dated before consent"}

derive:
  - {name: bmi, expr: "weight_kg / (height_cm / 100) ** 2", round: 1}

scales:
  - {name: "phq9_{t}", instrument: phq9, items: "phq9_{i}_{t}"}
  - {name: "pss_{t}",  instrument: pss10, items: "pss_{i}_{t}"}

describe:
  group_by: group

outputs: {spss: true}
```

All options are in [`docs/configuration.md`](docs/configuration.md). [`examples/`](examples/) has a longitudinal REDCap survey and a flat EMR extract, both synthetic.

## A few details that mattered in practice

- **Everything is read as text first**, so nothing gets converted behind your back. Types are applied later.
- **Every empty cell keeps a reason** (missing code, blank, invalid value removed, skipped by branching logic, form not started). The missing-data table is built from these, so item non-response is separated from questions that were never shown.
- **REDCap branching logic** (`[sex] = '1' and [age] >= 18`, `[q(2)]`, `datediff()`, `if()`) is evaluated by a small parser in `branching.py`, never with Python's `eval`. Hidden questions count as "skipped", and answers to questions that should have been hidden are flagged.
- **Missing codes can be per variable** (`income: 99`), so a real value of 99 somewhere else is left alone.
- **Scales:** reverse items, sum or mean, minimum number of answered items, prorating, subscales, cut-offs, safety flags (e.g. PHQ-9 item 9), and Cronbach's alpha for each scale. A low alpha usually means a reverse item was missed.
- **Identifiers never appear in logs or reports** (they show as `<redacted>`). Pseudonyms are salted SHA-256, with the salt read from the `HSP_SALT` environment variable, and the ID crosswalk goes to a separate `output/restricted/` folder.

## Coming from SPSS

| SPSS | here |
|---|---|
| `RECODE q4 q5 (1=5) (2=4) (3=3) (4=2) (5=1) INTO q4_r q5_r.` | `recode: [{variables: [q4, q5], reverse: {min: 1, max: 5}, into: "{var}_r"}]` |
| `COMPUTE total = SUM(q1 TO q10).` | scale with `method: sum`, `prorate: true`, `min_valid: ...` |
| `COMPUTE total = q1 + q2 + ... + q10.` | scale with `method: sum`, `prorate: false` |
| `MISSING VALUES q1 TO q10 (-99, -88).` | `missing.labelled_codes` |

`SUM()` and `+` treat missing items differently in SPSS, which is how two analysts end up with different totals. Here the rule is set per scale and shown in the scale summary.

## Output

```
output/
├── cleaned_data.csv / .sav     de-identified, one row per participant
├── summary_tables.xlsx         Table 1, scales, change, descriptives, missingness, issues, codebook
├── report.html                 self-contained, works offline
├── validation_issues.csv       one row per problem: record, raw row number, variable, value, rule, action
├── missingness.csv, table1.csv, scale_summary.csv, timepoint_comparisons.csv, codebook.csv
├── run_log.json                every step with counts
└── restricted/                 only when needed: ID crosswalk, safety-flag record IDs
```

## Please note

- Never commit participant data. `hsp init` writes a `.gitignore` for `data/` and `output/`.
- The questionnaire definitions only hold scoring metadata (no item wording). Check them against the version you used; EPDS reverse scoring assumes items coded 0–3 in form order.
- Safety flags help you find records to follow up; they don't replace your study's safety protocol.

## Known limits

- REDCap repeating instruments are dropped before reshaping; export them separately for now.
- Branching logic with smart variables or unusual functions is reported as "could not be evaluated" and the field's skip status is left unknown.
- p-values are unadjusted. This is a cleaning and reporting tool, not a modelling package.

## Development

```bash
pip install -e ".[dev]"
pytest
```

CI runs the tests on Ubuntu and Windows with Python 3.10, 3.12 and 3.13.

## License

MIT, see [`LICENSE`](LICENSE).

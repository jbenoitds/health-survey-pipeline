*Last updated: November 6, 2026*

# health-survey-pipeline

**Config-driven cleaning, validation, scoring and descriptive reporting for REDCap, survey and EMR exports.**

Version 0.1.1 · Python 3.8+ · runs on 2020-era libraries and on current ones · MIT license

---

## What it is

Health and psychology research teams spend a large share of every project cleaning raw exports. Data come
out of REDCap, an online survey platform or an electronic medical record (EMR) system full of missing-value
codes, skip logic, typos, out-of-range values, duplicate rows and identifiers that must not leave the team.
Analysts usually fix these by hand or with one-off SPSS syntax. That approach is slow, hard to audit, and has
to be redone for every new wave of data.

`health-survey-pipeline` automates that work. You describe a study once, in a short YAML configuration file,
and run one command. The tool:

1. **reads** the raw export and its data dictionary,
2. **cleans** it: missing-value codes, whitespace, duplicates, data types,
3. **validates** it against the dictionary and your own rules, logging every problem,
4. **de-identifies** it: drops identifiers, turns date of birth into age, can pseudonymise IDs,
5. **reshapes** longitudinal data to one row per participant,
6. **recodes and scores** it: reverse items, derived variables, questionnaire totals, cut-off categories,
7. **describes** it: Table 1, missing-data summaries, change across timepoints, a codebook,
8. **writes** analysis-ready files (CSV, SPSS `.sav`, Excel) and a self-contained HTML report.

Because every step is driven by the configuration and logged, the same file re-runs identically on the
next export, and anyone can see exactly what was changed and why.

---

## How it works

```
 raw export ─┐
             ├─► 1 load ─► 2 clean ─► 3 validate ─► 4 de-identify ─► 5 reshape ─► 6 recode & score ─► 7 describe ─► 8 write
 dictionary ─┘                                                                                                   │
                                                                     cleaned data · validation log · tables · report ◄┘
```

### 1. Load

Reads CSV/TSV (detects `,` `;` or tab, and UTF-8 or Windows-1252 encoding), Excel, SPSS `.sav`, Stata `.dta`
or Parquet. **Everything is read as text first**, so nothing is silently converted. Types are applied later,
under the pipeline's control.

The **data dictionary** tells the pipeline what each column should contain. It can be:

* a **REDCap data dictionary**, used as exported: field types, coded choices, validation type, min/max,
  identifier flag, required flag, branching logic and checkbox fields;
* a **simple codebook** CSV for non-REDCap data (`variable, label, type, choices, min, max, identifier, ...`);
* **nothing**, in which case column types are inferred from the values.

### 2. Clean

* Trims stray whitespace and removes fully blank rows.
* Converts **missing-value codes** to empty cells:
  * plain strings (`NA`, `.`, `NULL`, ...);
  * labelled codes for every column (`-99 = Refused`, `-88 = Don't know`);
  * codes for specific variables only (`income: 99 = Prefer not to answer`), so a real value of 99
    elsewhere stays intact.
* **Every empty cell keeps a reason**: a missing code, plain blank, invalid value removed, skipped by
  branching logic, or form not started. The missing-data report is built from these reasons.
* Finds **duplicate records** (on ID + event) and either flags them, keeps the first, or keeps the most
  complete row.

### 3. Validate

Each check produces a row in `validation_issues.csv` with the record ID, the row number in the raw file
(as seen in Excel), the variable, the offending value, the rule and the action taken.

* **From the dictionary:** numbers, integers and dates are real; values fall within min/max (including
  `today` for dates); coded answers are one of the allowed choices; emails look valid.
* **Skip logic:** REDCap branching logic (`[sex] = '1' and [age] >= 18`, checkboxes `[q(2)]`, `datediff()`,
  `if()`, ...) is evaluated with a small built-in parser, never Python's `eval`. This lets the pipeline
  * mark questions that were *hidden by design* as "skipped", not missing;
  * flag answers that exist even though the question should have been hidden;
  * report required fields as missing only where the question was actually shown and the form was started.
* **Custom rules** from your configuration: ranges, allowed values, patterns, outliers (|z| > k), and
  cross-field checks such as `survey_date_{t} >= consent_date`.

Invalid values are either set to missing (default) or kept and only flagged.

### 4. De-identify

* Drops every field marked *Identifier = y* in the dictionary, plus any column you list.
* Derives age from date of birth and a reference date, then drops the date of birth.
* Warns about kept columns whose names look like identifiers (name, email, phone, address, MRN,
  health-card number, ...) and about free text that contains emails or phone numbers.
* Optionally replaces record IDs with **salted SHA-256 pseudonyms**. The salt is read from an environment
  variable, so it never sits in the config file. The ID crosswalk is written to a separate
  `output/restricted/` folder.
* Identifier values are **never written to any log or report**. They appear as `<redacted>`.

### 5. Reshape (longitudinal studies)

A longitudinal REDCap export has one row per participant per event. The pipeline turns it into one row per
participant. Time-varying fields get a timepoint suffix (`phq9_1_t1`, `phq9_1_t2`). Fields collected at a
single event, such as demographics, keep their plain name. In the configuration, `{t}` stands for every
timepoint, so one rule covers all waves.

### 6. Recode, derive and score

* **Recode** (the SPSS `RECODE` equivalent): reverse coding, value maps (`1,2 → 1; 3 → 2`), binning into
  labelled categories.
* **Derive** (the SPSS `COMPUTE` equivalent): arithmetic expressions such as BMI, date differences, counts of
  ticked checkboxes.
* **Score questionnaires.** Built-in definitions are provided for PHQ-9, PHQ-2, GAD-7, PSS-10, EPDS, WHO-5 and
  K10, and you can define any other scale. For each scale you choose:
  * which items are reverse-scored;
  * sum or mean;
  * the minimum number of answered items, and whether to prorate;
  * subscales and cut-off categories (e.g. PHQ-9 minimal / mild / moderate / ...);
  * **safety flags**, e.g. any endorsement of PHQ-9 item 9 or EPDS item 10.

  Out-of-range item values are removed and reported, because they would silently distort totals.
  **Cronbach's alpha** is computed for every scale. A low alpha usually means a reverse-coded item was
  missed.

### 7. Describe

* Continuous summaries (n, mean, SD, median, IQR, range, skew) and categorical frequencies, using the value
  labels from the dictionary.
* **Missing data by reason** for every variable, including *item non-response*: unanswered questions among
  people who were shown them. This is separated from questions skipped by design and from loss to follow-up.
* **Table 1** by group, e.g. study arm. Continuous variables get mean (SD) with a Welch t-test or one-way
  ANOVA. Categorical variables get n (%) with a chi-square or Fisher exact test.
* **Change across timepoints** for every scale: paired t-test, Wilcoxon signed-rank test, and Cohen's
  d<sub>z</sub>.
* A **codebook** of every output variable with its label, type, value labels and range.

### 8. Write

```
output/
├── cleaned_data.csv            analysis-ready, de-identified, one row per participant
├── cleaned_data.sav            same, with SPSS variable and value labels (optional)
├── summary_tables.xlsx         overview, Table 1, scales, change, descriptives, missingness, issues, codebook, log
├── report.html                 self-contained report: open in any browser, works offline, light/dark
├── validation_issues.csv       one row per problem found
├── missingness.csv             per variable: present, skipped by design, form not started, codes, invalid
├── table1.csv  scale_summary.csv  timepoint_comparisons.csv  codebook.csv
├── run_log.json                every processing step with counts, for reproducibility
└── restricted/                 only when needed: ID crosswalk, original-ID issue log, safety-flag record IDs
```

---

## Requirements

The package is built for the **2020 toolchain** and also runs on current releases.

| | minimum (2020 releases) | tested up to |
|---|---|---|
| Python | 3.8 | 3.13 |
| pandas | 1.1 | 3.0 |
| numpy | 1.19 | 2.x |
| scipy | 1.5 | current |
| PyYAML | 5.3 | current |
| openpyxl | 3.0 | current |
| pyreadstat (optional, SPSS files) | 1.0 | current |
| pip / setuptools | 20.2 / 49 | current |

The exact library versions released by 31 December 2020 are pinned in `ci/constraints-2020.txt`. The full
test suite passes with those pins, with mid-2020 releases (pandas 1.1.0, scipy 1.5.2), and with current
releases (pandas 2.2 and 3.0). On the demo data, the 2020 setup and the current one produce identical
output, cell for cell.

## Installation

```bash
# from a clone of the repository
pip install -e ".[spss]"            # drop [spss] if you don't need SPSS .sav input/output

# to reproduce the exact 2020 environment
pip install -c ci/constraints-2020.txt -e ".[dev]"
```

This installs the `hsp` command.

---

## Quick start

### Try it on fake data

```bash
hsp demo
```

This generates a synthetic two-wave REDCap project with 240 invented participants and runs the whole
pipeline. Open `hsp_demo/output/report.html`. The demo data contain deliberately planted problems:
out-of-range values, missing codes, skip-logic violations, a duplicate row, impossible and future dates,
and a phone number hidden in a comments field. The test suite checks that every one of them is caught.

### Use it on your own export

```bash
hsp init my_study                 # creates my_study/config.yaml (fully commented), data/, output/, .gitignore
# 1. copy your raw export and data dictionary into my_study/data/
# 2. edit my_study/config.yaml
hsp check my_study/config.yaml    # dry run: prints the issues found, writes nothing
hsp run   my_study/config.yaml    # full run: writes everything to my_study/output/
```

### Other commands

```bash
hsp profile export.csv --dictionary data_dictionary.csv   # quick report on any file, no config needed
hsp instruments                                           # list the built-in questionnaire definitions
```

### From Python

```python
from hspipe import run

ctx = run("my_study/config.yaml")
ctx.df                    # cleaned DataFrame
ctx.tables["table1"]      # Table 1
ctx.issues_frame()        # every validation issue
```

---

## Example configuration

```yaml
project: {name: "Two-wave wellbeing survey", output_dir: output}

input:
  data: data/export.csv
  data_dictionary: data/data_dictionary.csv
  id_column: record_id
  event_column: redcap_event_name

timepoints: [t1, t2]                       # {t} below expands to each of these

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
  - {name: "pss_{t}",  instrument: pss10, items: "pss_{i}_{t}"}     # items 4, 5, 7, 8 reverse-scored

describe:
  group_by: group                          # Table 1 by study arm

outputs: {spss: true}
```

Every option is documented in [`docs/configuration.md`](docs/configuration.md) and in the commented template
that `hsp init` writes. Two worked examples, both with synthetic data, are in [`examples/`](examples/):

* a longitudinal REDCap survey;
* a flat EMR extract with a simple codebook.

---

## Coming from SPSS syntax

| SPSS | health-survey-pipeline |
|---|---|
| `RECODE q4 q5 (1=5) (2=4) (3=3) (4=2) (5=1) INTO q4_r q5_r.` | `recode: [{variables: [q4, q5], reverse: {min: 1, max: 5}, into: "{var}_r"}]` |
| `COMPUTE total = SUM(q1 TO q10).` (any answered item counts) | scale with `method: sum`, `prorate: true`, `min_valid: ...` |
| `COMPUTE total = q1 + q2 + ... + q10.` (any missing → missing) | scale with `method: sum`, `prorate: false` |
| `COMPUTE sub_a = MEAN(q1, q2, q3).` | `subscales: {a: [1, 2, 3]}` with `subscale_method: mean` |
| `VARIABLE LABELS` / `VALUE LABELS` | taken from the data dictionary and kept in the `.sav` output |
| `MISSING VALUES q1 TO q10 (-99, -88).` | `missing.labelled_codes` |

SPSS's `SUM()` and `+` treat missing items differently, a classic source of silently different totals. Here the
rule is set explicitly for each scale and shown in the scale summary.

---

## Data protection

* **Never commit participant data.** `hsp init` writes a `.gitignore` that excludes `data/`, `output/` and
  common data formats.
* Identifier fields are dropped by default, and suspicious columns are reported for review.
* With `deidentify.hash_id.enabled`, record IDs become salted pseudonyms. Set the salt in the `HSP_SALT`
  environment variable. Keep `output/restricted/` (the ID crosswalk and the original-ID issue log)
  separate from anything you share.
* Safety items are flagged in the report. The records needing clinical follow-up are listed in
  `safety_flags.csv`. This does not replace your study's safety protocol.

## Questionnaire definitions

The built-in definitions hold only scoring metadata, never item wording: number of items, response range,
reverse-keyed items and published cut-offs. **Check them against the manual and version you administered.**
In particular, EPDS reverse scoring assumes items are coded 0–3 in the order the options appear on the form.
If your export already stores item scores, set `reverse: []`.

---

## Project layout

```
src/hspipe/
  io.py            readers and writers (CSV, Excel, SPSS, Stata, Parquet)
  dictionary.py    REDCap data dictionary and generic codebook parsing
  branching.py     REDCap branching-logic evaluator (vectorised, no eval)
  missing.py       standardisation and missing-value codes
  validate.py      dictionary checks, skip logic, required fields, duplicates, custom rules, type conversion
  deidentify.py    identifier removal, age from date of birth, pseudonymisation
  longitudinal.py  long → wide reshaping
  recode.py        recode rules and derived variables
  scoring.py       questionnaire scoring, built-in instruments, Cronbach's alpha
  describe.py      descriptives, missingness, Table 1, timepoint change, codebook
  report.py        Excel workbook and HTML report
  pipeline.py      runs the steps in order
  cli.py           the `hsp` command
  synth.py         synthetic demo project
  _compat.py       shims so one codebase runs on 2020 and current library versions
tests/             42 tests: unit, step-level and end-to-end on the synthetic project
examples/          REDCap longitudinal and EMR-extract examples (synthetic data)
docs/              configuration reference
ci/                pinned 2020 library versions
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Continuous integration runs the tests on:

* Python 3.8 with pip 20.3.3 and the pinned 2020 libraries, on Ubuntu and Windows;
* Python 3.10 and 3.13 with current libraries.

## Limitations

* REDCap repeating instruments are dropped before reshaping. Export them separately for now.
* Branching logic that uses smart variables or uncommon functions is reported as "could not be evaluated".
  The skip status of those fields is left unknown rather than guessed.
* p-values are unadjusted and descriptive. This is a cleaning and reporting tool, not a modelling package.

## License

MIT. See [`LICENSE`](LICENSE).

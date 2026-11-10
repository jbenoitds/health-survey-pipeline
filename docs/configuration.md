# Configuration reference

A project is one YAML file. Paths are relative to the file. Any section or key you omit takes the default shown.
`hsp init` writes a fully commented template. Unknown top-level sections are rejected, so typos surface
immediately.

Anywhere a variable name is accepted, `{t}` expands to every entry of `timepoints`. For example,
`sleep_hours_{t}` becomes `sleep_hours_t1, sleep_hours_t2`.

---

## `project`

| key | default | |
|---|---|---|
| `name` | `Untitled project` | shown in the report |
| `output_dir` | `output` | created if missing |

## `input`

| key | default | |
|---|---|---|
| `data` | **required** | `.csv .tsv .txt .xlsx .xls .sav .zsav .dta .parquet` |
| `format` | `auto` | force a reader, e.g. `csv` |
| `sheet` | first sheet | Excel sheet name |
| `encoding` | auto (`utf-8-sig`, then `cp1252`, then `latin-1`) | |
| `data_dictionary` | none | REDCap data dictionary CSV, or a generic codebook (see below) |
| `id_column` | `record_id` | |
| `event_column` | none | `redcap_event_name` for longitudinal REDCap exports |
| `drop_columns` | `[]` | discarded right after loading |

### Generic codebook (non-REDCap data)

A CSV with a `variable` column and any of: `label, type, choices, min, max, identifier, required, branching,
form, validation`.

* `type`: `integer`, `number`, `date`, `categorical` (or `radio`, `dropdown`), `checkbox`, `yesno`, `text`,
  `notes`
* `choices`: `1=Never|2=Former|3=Current` or REDCap style `1, Never | 2, Former`
* `identifier`, `required`: `y` / `yes` / `1`

## `timepoints`

List of suffixes, e.g. `[t1, t2, t3]`. These are filled in automatically from `longitudinal.event_map` if you
reshape and leave this empty.

## `missing`

| key | default | |
|---|---|---|
| `codes` | `["", "NA", "N/A", "na", ".", "-", "NULL", ...]` | strings meaning "missing" |
| `labelled_codes` | `{}` | `{-99: Refused, -88: Don't know}`: applied to every column, counted separately |
| `variable_codes` | `{}` | `{income: {99: Prefer not to answer}}`: only for these variables (supports `{t}`) |
| `apply_to` | `all` | or a list of variables |

Numeric codes match regardless of formatting (`-99`, `-99.0`, `"-99"`). A labelled code is also removed
from that variable's value labels, so it doesn't appear as a 0% level in tables.

## `longitudinal`

| key | default | |
|---|---|---|
| `reshape` | `false` | long (one row per event) → wide (one row per participant) |
| `event_map` | `{}` | `{baseline_arm_1: t1, followup_arm_1: t2}`. Rows from unmapped events are dropped (and logged). |
| `suffix_sep` | `_` | `phq9_1` + `t1` → `phq9_1_t1` |
| `time_invariant` | `auto` | `auto` keeps fields collected at a single event unsuffixed; or give an explicit list |
| `keep_unmapped_events` | `false` | |

## `deidentify`

| key | default | |
|---|---|---|
| `enabled` | `true` | |
| `use_dictionary_identifiers` | `true` | drop fields with *Identifier? = y* |
| `drop` / `keep` | `[]` | extra columns to drop / never drop |
| `derive_age` | `[]` | `[{dob: dob, reference: consent_date, name: age_years, floor: true, drop_dob: true}]` |
| `drop_free_text` | `false` | drop all `notes` and unvalidated `text` fields |
| `hash_id.enabled` | `false` | salted SHA-256 pseudonyms (12 hex characters; `length` to change) |
| `hash_id.salt_env` | `HSP_SALT` | environment variable holding the salt (preferred) |
| `hash_id.salt` | none | inline salt (avoid committing it) |
| `hash_id.write_crosswalk` | `true` | `output/restricted/id_crosswalk.csv` |

Identifier values are redacted from every log. Column names that look like identifiers (name, email, phone,
address, postal code, MRN, health-card number, DOB...) and free text containing emails or phone numbers are
listed as warnings in the report.

## `validation`

| key | default | |
|---|---|---|
| `use_dictionary` | `true` | type / range / choice checks from the dictionary |
| `check_branching` | `true` | skip-logic classification and "answered but hidden" warnings |
| `check_required` | `true` | required fields, counted only where the question was shown and the form was started |
| `check_duplicates` | `true` | on `id_column` (+ event, + repeat instance) |
| `duplicates` | `flag` | `flag`, `keep_first`, or `keep_most_complete` |
| `on_invalid` | `set_missing` | or `flag` (keep the value, report only) |
| `rules` | `[]` | custom rules, see below |

Custom rules run on the final layout (after reshaping), so `{t}` names are available:

```yaml
rules:
  - {variable: age_years, range: [18, 65], message: "Outside eligibility"}   # min/max also accepted
  - {variable: "sleep_hours_{t}", outliers: 3}                              # |z| > 3 -> warning
  - {variable: province, allowed: [QC, ON, BC]}
  - {variable: postal3, regex: "[A-Z][0-9][A-Z]"}
  - {variable: consent_date, required: true}
  - {check: "survey_date_{t} >= consent_date", message: "Survey before consent"}   # pandas expression
  - {check: "[smoker] = '0' or [cigs_per_day] <> ''", severity: warning}          # REDCap syntax
```

Rule keys: `name`, `message`, `severity` (`error` or `warning`), and `action` (`set_missing` is the default
for variable rules; `check` rules only flag unless `action: set_missing` and `variable:` are given).

## `recode`

```yaml
recode:
  - {variables: [q4, q5], reverse: {min: 1, max: 5}, into: "{var}_r"}
  - variable: education
    map: {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}
    else: missing            # copy (default) | missing | a value
    into: education_3cat
    labels: {1: Secondary or less, 2: College, 3: University}
  - variable: bmi
    after_derive: true       # run after `derive` (bmi is derived)
    bins: [0, 18.5, 25, 30, .inf]
    labels: [Underweight, Normal, Overweight, Obese]
    right: false             # intervals [a, b)
    into: bmi_cat
```

Without `into`, the variable is recoded in place (or use `suffix: _r`).

## `derive`

```yaml
derive:
  - {name: bmi, expr: "weight_kg / (height_cm / 100) ** 2", round: 1, label: "BMI"}
  - {name: "sleep_short_{t}", expr: "sleep_hours_{t} < 6", labels: {0: No, 1: Yes}, kind: categorical}
  - {name: days_fu, type: datediff, start: survey_date_t1, end: survey_date_t2, unit: days}   # days|weeks|months|years
  - {name: n_conditions, type: count_checked, prefix: conditions, exclude: [5]}
```

`expr` is a pandas expression over column names (`+ - * / **`, comparisons, `&`, `|`, and `col.dt.year` for
dates).

## `scales`

```yaml
scales:
  - {name: "phq9_{t}", instrument: phq9, items: "phq9_{i}_{t}"}          # {i} = 1..n_items
  - name: resilience
    label: "Resilience (custom)"
    items: [res1, res2, res3, res4, res5, res6]
    item_range: [1, 5]          # out-of-range items are reported and removed
    reverse: [2, 4, 6]          # 1-based positions, or column names
    method: mean                # sum | mean
    min_valid: 0.8              # fraction (<=1) or count of answered items
    prorate: false              # sum only: mean x n_items when some items are missing
    multiplier: 1
    round: null
    subscales: {a: [1, 2, 3], b: [4, 5, 6]}
    subscale_method: mean
    cutoffs: [[0, Low], [3, Moderate], [4, High]]   # lower bounds, ascending
    flags: [{item: 6, op: ">", value: 3, suffix: item6_flag, label: "Item 6 high"}]
    keep_item_count: true       # adds <name>_nvalid
    keep_recoded_items: false   # adds <item>_r for reverse-scored items
```

Built-in instruments (`hsp instruments`) supply defaults that any key above can override.

| key | items | range | reverse | rule | cut-offs | flag |
|---|---|---|---|---|---|---|
| `phq9` | 9 | 0–3 | – | sum, ≥8 items, prorated | 5/10/15/20 | item 9 > 0 |
| `phq2` | 2 | 0–3 | – | sum | ≥3 positive | |
| `gad7` | 7 | 0–3 | – | sum, ≥6 items, prorated | 5/10/15 | |
| `pss10` | 10 | 0–4 | 4, 5, 7, 8 | sum, ≥9 items, prorated | 14/27 | |
| `epds` | 10 | 0–3 | 3, 5–10 * | sum, ≥9 items, prorated | ≥13 | item 10 > 0 |
| `who5` | 5 | 0–5 | – | sum × 4 (0–100) | ≥52 adequate | |
| `k10` | 10 | 1–5 | – | sum, ≥9 items, prorated | 16/22/30 | |

\* assumes options are coded 0–3 in the order they appear on the form. Set `reverse: []` if your export
already stores item scores.

For each scale, the scale summary reports the items, rule, minimum items, reverse items, n scored,
n with insufficient items, mean, SD, range and Cronbach's alpha (listwise). An alpha below 0.70 is
highlighted, which often means an item's reverse coding is wrong.

## `describe`

| key | default | |
|---|---|---|
| `variables` | `auto` | `auto`: every numeric and categorical variable; `[]`: only the lists below |
| `continuous` / `categorical` | `[]` | explicit lists (supports `{t}`) |
| `group_by` | none | Table 1 columns + tests (Welch t / one-way ANOVA; χ², Fisher exact for 2×2 with small expected counts) |
| `tests` | `true` | |
| `compare_timepoints` | `true` | paired change, first timepoint vs each later one, for `{t}` scales and derived variables |
| `compare` | auto | explicit templates, e.g. `["sleep_hours_{t}"]` |
| `max_categories` | `15` | auto mode: categorical variables with more levels are skipped |

## `outputs`

| key | default |
|---|---|
| `cleaned_csv` | `true` |
| `excel` | `true` |
| `html` | `true` |
| `spss` | `false` (needs `pip install "health-survey-pipeline[spss]"`) |
| `codebook` | `true` |

## Order of operations

1. load → standardise (trim, drop blank rows) → missing codes
2. duplicates → dictionary checks → skip logic / required → type conversion
3. de-identify (age from DOB, drop identifiers) → long → wide
4. custom rules → recode → derive → recode (`after_derive`) → scales → pseudonymise IDs
5. describe → write outputs

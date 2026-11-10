# Examples

All example data are synthetic.

| folder | what it shows |
|---|---|
| `redcap_longitudinal/` | Two-wave REDCap project: data dictionary, skip logic, reshaping, PHQ-9 / GAD-7 / PSS-10 scoring, Table 1 by arm. Generate the data with `hsp demo`, or run `python make_data.py` here, then `hsp run config.yaml`. |
| `emr_extract/` | Flat clinical extract with a simple codebook (no REDCap): letter-coded categories, labelled missing codes, a cross-field blood-pressure rule, derived age and MAP, BP categories. Run `hsp run examples/emr_extract/config.yaml`. |

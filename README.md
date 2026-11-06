# Patient Profile Common Model Framework

This repository contains a synthetic, publicly available demonstration of a configurable Patient Profile framework for clinical trial subject-level review.

The package demonstrates how heterogeneous electronic data capture (EDC) exports can be transformed into standardized intermediate tables that can then be reused by reporting modules, dashboards, or static subject-level Patient Profile reports.

## Contents

```text
src/
  common_tables_generator_patient_profile.py
  example_patient_profile_renderer.py

configs/
  example_pheedit_config.json
  example_viedoc_config.json

docs/
  common_model_data_dictionary.md
  configuration_guide.md

examples/
  synthetic_pheedit/
  synthetic_viedoc/
  outputs/
```

## What this package is

This package is a public, anonymized implementation example of:

1. A configurable common table generator.
2. Two example EDC-specific configuration files.
3. A simple public Patient Profile HTML renderer.
4. Small synthetic datasets that allow the examples to run without clinical trial data.

## What this package is not

This package is not:

- a production clinical review system;
- a validated clinical database;
- a replacement for an EDC;
- a DSMB, IB, DSUR, or aggregate safety reporting system;
- a medical coding pipeline;
- a reproduction of a proprietary dashboard or production PDF template.

## Installation

Python 3.10+ is recommended.

```bash
pip install -r requirements.txt
```

## Quick start: PheedIt-like synthetic example

From the repository root:

```bash
python src/common_tables_generator_patient_profile.py \
  --config configs/example_pheedit_config.json \
  --out examples/outputs/synthetic_pheedit_common_tables

python src/example_patient_profile_renderer.py \
  --tables examples/outputs/synthetic_pheedit_common_tables \
  --subject SUBJ-001 \
  --out examples/outputs/patient_profile_SUBJ-001.html
```

## Quick start: Viedoc-like synthetic example

```bash
python src/common_tables_generator_patient_profile.py \
  --config configs/example_viedoc_config.json \
  --out examples/outputs/synthetic_viedoc_common_tables

python src/example_patient_profile_renderer.py \
  --tables examples/outputs/synthetic_viedoc_common_tables \
  --subject V-SUBJ-001 \
  --out examples/outputs/patient_profile_V-SUBJ-001.html
```

## Output tables

The generator can create the following standardized intermediate tables, depending on the configuration and input datasets:

- `snapshot_metadata.csv`
- `subject_master.csv`
- `visit_status.csv`
- `treatment_administration.csv`
- `adverse_events.csv`
- `laboratory_measurements.csv`
- `tumor_assessments.csv`
- `overall_tumor_response.csv`
- `end_of_study_status.csv`
- `overall_survival.csv`
- `patient_status_summary.csv`
- `generation_log.csv`
- `mapping_qc.csv`

## License

The source code included in this public demonstration repository is distributed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for details.

The license applies to the publicly available demonstration materials and does not extend to the sponsor's confidential production implementations or clinical trial datasets.

## Data availability and privacy

Only synthetic data are included. Trial-specific raw exports, generated subject-level outputs, and production dashboard/PDF templates should not be shared publicly unless they have been formally approved for disclosure.


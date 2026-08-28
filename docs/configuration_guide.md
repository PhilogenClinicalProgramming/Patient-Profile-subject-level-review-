# Configuration Guide

The generator is controlled by JSON configuration files. Each configuration defines:

1. Global metadata.
2. Input datasets.
3. Output tables.
4. Column mapping rules.
5. Optional per-source overrides.

## Global metadata

```json
{
  "base_path": "examples/synthetic_pheedit",
  "study_id": "SYNTHETIC_STUDY_A",
  "edc_system": "PheedIt-like",
  "snapshot_datetime": "2026-01-01 00:00:00",
  "code_version": "public-v0.1",
  "config_version": "example_config_public_v0.1",
  "data_model_version": "1.0"
}
```

## Dataset definitions

Simple CSV:

```json
"dm": {
  "file": "dm.csv",
  "header": 0,
  "sep": ","
}
```

Viedoc-like double-header CSV:

```json
"vs": {
  "file": "viedoc_vs.csv",
  "sep": ",",
  "read_mode": "viedoc_double_header"
}
```

## Mapping rules

### Source column mapping

```json
"subject_id": {
  "source": ["subject_id", "PATNO"],
  "required": true
}
```

The first non-empty available source column is used.

### Constant value

```json
"country": {
  "constant": "Not disclosed"
}
```

### Parsed date

```json
"start_date": {
  "parsed_date": "AESTDAT"
}
```

### Date from split day/month/year fields

```json
"start_date": {
  "date_from_parts": {
    "day": "AESTDY",
    "month": "AESTMO",
    "year": "AESTYR",
    "output_format": "iso"
  }
}
```

### Flag normalization

```json
"serious_flag": {
  "source": ["AESER"],
  "normalize_flag": true
}
```

Converts common yes/no encodings to `Yes`/`No`.

### Checkbox labels

```json
"race": {
  "checkbox_labels": {
    "columns": {
      "RACE_WHITE": "White",
      "RACE_ASIAN": "Asian"
    },
    "separator": "; "
  }
}
```

### Lookup

```json
"subject_id": {
  "lookup": {
    "dataset": "dm",
    "left_on": "PATNO",
    "right_on": "PATNO",
    "value": "subject_id"
  },
  "required": true
}
```

## Per-source overrides

When a common table is built from multiple input datasets, `source_overrides` can define source-specific mappings or processing.

```json
"source_overrides": {
  "treatment_1": {
    "columns": {
      "drug_name": {"constant": "Study treatment 1"},
      "administration_date": {"parsed_date": "treatment_1_date"}
    }
  }
}
```

## Viedoc-like wide laboratory reshaping

For Viedoc-like exports where one row contains multiple analytes as columns, use:

```json
"source_overrides": {
  "lbcc": {
    "lab_format": "viedoc_wide",
    "panel": "Clinical chemistry",
    "sample_prefix": "CC",
    "prefixes": ["ALT", "AST", "BILI"]
  }
}
```

The generator reshapes fields such as `ALT_LBORRES`, `ALT_LBORRESU`, `ALT_LBORNR_Lower`, and `ALT_LBORNR_Upper` into long-format laboratory rows.

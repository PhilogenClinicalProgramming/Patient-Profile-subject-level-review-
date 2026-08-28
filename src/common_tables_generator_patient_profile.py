#!/usr/bin/env python
"""
Common Patient Profile table generator.

This anonymized script creates intermediate tables for subject-level Patient Profile
review from heterogeneous EDC exports. It is not intended to prepare aggregate
DSMB/IB/DSUR tables and it does not require MedDRA coding.

The core principle is to preserve and harmonize eCRF-recorded information
(subjects, visits, treatment administrations, adverse events, labs and tumor
assessments) so that the same downstream Patient Profile reporting logic can be
used across different EDC systems.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


STANDARD_TABLES: dict[str, list[str]] = {
    "snapshot_metadata": [
        "study_id", "edc_system", "snapshot_datetime", "snapshot_source",
        "code_version", "config_version", "data_model_version",
    ],
    "subject_master": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "site_id",
        "country", "treatment_group", "study_part", "sex", "age", "race",
        "ethnicity", "enrollment_date", "source_dataset", "source_row_id",
    ],
    "visit_status": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id",
        "visit_name", "visit_order", "visit_date", "visit_status",
        "expected_flag", "performed_flag", "optional_flag", "not_done_reason",
        "source_dataset", "source_row_id",
    ],
    "treatment_administration": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "visit_name",
        "drug_name", "administration_date", "start_time", "end_time", "dose",
        "dose_unit", "volume", "volume_unit", "administered_flag",
        "reason_not_administered", "dose_modified_flag", "dose_modification_reason",
        "source_dataset", "source_row_id",
    ],
    "adverse_events": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "ae_id",
        "ae_term", "start_date", "end_date", "ongoing_flag", "grade",
        "serious_flag", "relatedness_as_recorded", "action_study_treatment_1",
        "action_study_treatment_2", "action_other", "outcome",
        "concomitant_treatment_flag", "source_dataset", "source_row_id",
    ],
    "laboratory_measurements": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "visit_name",
        "assessment_date", "panel", "parameter", "result_value", "result_unit",
        "normal_low", "normal_high", "normal_range_source", "abnormal_flag",
        "clinically_significant_flag", "not_done_reason", "source_dataset", "source_row_id",
    ],
    "tumor_assessments": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "visit_name",
        "assessment_date", "lesion_type", "lesion_id", "location", "measurement_value",
        "measurement_unit", "response", "new_lesion_flag", "not_done_reason",
        "source_dataset", "source_row_id",
    ],
    "overall_tumor_response": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id", "visit_name",
        "assessment_date", "target_response", "non_target_response",
        "new_lesion_progression", "overall_response", "reason_not_assessed",
        "source_dataset", "source_row_id",
    ],
    "end_of_study_status": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id",
        "treatment_completed_flag", "reason_treatment_not_completed",
        "maintenance_completed_flag", "reason_maintenance_not_completed",
        "followup_completed_flag", "reason_followup_not_completed",
        "end_of_study_date", "source_dataset", "source_row_id",
    ],
    "overall_survival": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id",
        "last_contact_date", "death_date", "vital_status", "reason",
        "source_dataset", "source_row_id",
    ],
    "patient_status_summary": [
        "study_id", "edc_system", "snapshot_datetime", "subject_id",
        "patient_status", "first_dose_date", "last_dose_date", "end_of_treatment_date",
        "last_contact_date", "death_date", "source_dataset", "source_row_id",
    ],
    "generation_log": [
        "run_datetime", "study_id", "edc_system", "snapshot_datetime", "table_name",
        "status", "n_rows", "n_subjects", "message",
    ],
    "mapping_qc": [
        "table_name", "output_column", "mapping_type", "source_dataset",
        "source_column", "required", "status", "message",
    ],
}


def read_csv_flexible(path: Path, *, header: int = 0, sep: str = ",") -> pd.DataFrame:
    """Read CSV files using common clinical-export encodings."""
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            df = pd.read_csv(path, encoding=enc, header=header, sep=sep, low_memory=False)
            df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
            return df
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return pd.read_csv(path, header=header, sep=sep, low_memory=False)


def clean_string(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    s = str(value).strip()
    if s in {"", ".", "nan", "NaN", "None", "<NA>"}:
        return pd.NA
    return s


def coalesce_columns(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for col in columns:
        if col not in df.columns:
            continue
        s = df[col].map(clean_string)
        out = out.where(out.notna(), s)
    return out


def normalize_flag(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    s = str(value).strip().upper()
    if s in {"Y", "YES", "1", "TRUE", "T"}:
        return "Yes"
    if s in {"N", "NO", "0", "FALSE", "F"}:
        return "No"
    return str(value).strip()




def derive_checkbox_labels(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    """
    Derive readable labels from checkbox columns.

    Expected JSON structure
    -----------------------
    "checkbox_labels": {
      "columns": {
        "CHECKBOX_COL_1": "Readable label 1",
        "CHECKBOX_COL_2": "Readable label 2"
      },
      "other_flag": "OTHER_CHECKBOX_COL",
      "other_text": ["Specify", "Specify.1", "OTHER_TEXT_COL"],
      "missing_other_text": "missing data",
      "separator": "; "
    }

    A checkbox is considered selected when its value is one of:
    YES, Y, 1, TRUE, T.
    """
    label_map = spec.get("columns", {}) or {}
    other_flag = spec.get("other_flag")
    other_text_cols = spec.get("other_text", []) or []
    missing_other_text = spec.get("missing_other_text", "missing data")
    separator = spec.get("separator", "; ")

    def _truthy(v: Any) -> bool:
        if pd.isna(v):
            return False
        return str(v).strip().upper() in {"YES", "Y", "1", "TRUE", "T"}

    def _first_text(row: pd.Series, cols: list[str]) -> Any:
        for col in cols:
            if col not in row.index:
                continue
            v = clean_string(row.get(col, pd.NA))
            if pd.notna(v):
                return str(v).strip()
        return pd.NA

    def _combine(row: pd.Series) -> Any:
        values: list[str] = []

        for col, label in label_map.items():
            if col in row.index and _truthy(row.get(col, pd.NA)):
                values.append(str(label))

        if other_flag and other_flag in row.index and _truthy(row.get(other_flag, pd.NA)):
            txt = _first_text(row, other_text_cols)
            values.append(str(txt) if pd.notna(txt) else str(missing_other_text))

        values_unique = list(dict.fromkeys(values))
        return separator.join(values_unique) if values_unique else pd.NA

    return df.apply(_combine, axis=1)


def parse_mixed_date(value: Any, output_format: str = "%Y-%m-%d") -> Any:
    if pd.isna(value):
        return pd.NA
    s = str(value).strip()
    if s in {"", ".", "nan", "NaN", "None", "<NA>"}:
        return pd.NA
    if any(x in s.upper() for x in ["UNK", "UNKNOWN", "NK"]):
        return s
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        dt = pd.to_datetime(s, errors="coerce", dayfirst=False)
    if pd.isna(dt):
        return s
    return dt.strftime(output_format)


def make_partial_date(
    row: pd.Series,
    *,
    day_col: str | None,
    month_col: str | None,
    year_col: str | None,
    output_format: str = "crf",
) -> Any:
    def part(col: str | None) -> Any:
        if not col or col not in row.index:
            return None
        val = row[col]
        if pd.isna(val):
            return None
        s = str(val).strip()
        if s in {"", ".", "nan", "NaN", "None", "<NA>"}:
            return None
        return s

    dy = part(day_col)
    mo = part(month_col)
    yr = part(year_col)
    if yr is None:
        return pd.NA

    def fmt_day(x: Any) -> str | None:
        if x is None:
            return None
        try:
            i = int(float(str(x)))
            if i == 0:
                return None
            return f"{i:02d}"
        except Exception:
            return str(x).strip()

    def fmt_month(x: Any) -> str:
        if x is None:
            return "UNK"
        s = str(x).strip()
        if s == "" or s == "." or s.upper() in {"UNKNOWN", "UNK", "NK"}:
            return "UNK"
        month_map = {
            "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
            "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
            "SEP": "09", "SEPT": "09", "OCT": "10", "NOV": "11", "DEC": "12",
        }
        su = s.upper()
        if su in month_map:
            return month_map[su]
        try:
            i = int(float(s))
            if 1 <= i <= 12:
                return f"{i:02d}"
        except Exception:
            pass
        return s

    day = fmt_day(dy)
    month = fmt_month(mo)
    year = str(int(float(yr))) if re.fullmatch(r"\d+(\.0)?", str(yr).strip()) else str(yr).strip()
    candidate = f"{day}/{month}/{year}" if day else f"{month}/{year}"
    if output_format == "iso" and day and month != "UNK":
        dt = pd.to_datetime(candidate, format="%d/%m/%Y", errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    return candidate


def derive_snapshot_from_filename(filename: str) -> str | None:
    m = re.search(r"_(\d{8})_(\d{6})_", str(filename))
    if not m:
        return None
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def stable_config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]






# =========================================================
# VISIT STATUS HELPERS
# =========================================================

def derive_visit_order(value: Any) -> float:
    """Approximate protocol-aware visit ordering used for Patient Profile visit timelines."""
    if pd.isna(value):
        return 99999.0
    s = str(value).strip().upper()
    s = re.sub(r"\s+", " ", s)

    if s in {"SCREENING", "SCR", "SCREENING EVALUATION"}:
        return 0.0
    if "UNSCHEDULED" in s or s == "UNS":
        return 9000.0
    if "EOT" in s or "END OF TREATMENT" in s:
        return 9700.0

    m_fu = re.search(r"\b(?:FUP|FOLLOW UP)\s*W\s*(\d+)\b", s)
    if m_fu:
        return 10000.0 + int(m_fu.group(1))

    is_maint = "MAINT" in s

    m = re.search(r"\bC\s*0*(\d+)\s*D\s*0*(\d+)\s*[_\-]\s*0*(\d+)\b", s)
    if m:
        cycle = int(m.group(1))
        d1 = int(m.group(2))
        d2 = int(m.group(3))
        return (3000.0 if is_maint else 1000.0) + cycle * 100.0 + d1 + (d2 / 1000.0)

    m = re.search(r"\bC\s*0*(\d+)\s*D\s*0*(\d+)\b", s)
    if m:
        cycle = int(m.group(1))
        day = int(m.group(2))
        return (3000.0 if is_maint else 1000.0) + cycle * 100.0 + day

    return 9995.0


def derive_optional_visit_flag(value: Any) -> Any:
    """Flag protocol windows that are optional/flexible in the Patient Profile visit overview."""
    if pd.isna(value):
        return pd.NA
    s = str(value).strip().upper()
    if "UNSCHEDULED" in s:
        return "Yes"
    if re.search(r"\bD\s*0*(8|15|29)\s*[_\-]\s*0*(14|21|35)\b", s):
        return "Yes"
    return "No"


def derive_performed_flag_from_date(value: Any) -> str:
    return "Yes" if pd.notna(clean_string(value)) else "No"


def derive_visit_status(row: pd.Series) -> str:
    performed = str(row.get("performed_flag", "")).strip().upper()
    reason = clean_string(row.get("not_done_reason", pd.NA))
    if performed == "YES":
        return "Performed"
    if pd.notna(reason):
        return "Not performed"
    return "Expected"

# =========================================================
# LABORATORY NORMAL RANGE HELPERS
# =========================================================

def _to_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", ".", regex=False), errors="coerce")


def parse_sas_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%d%b%Y", errors="coerce")


def normalize_test_code(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper()


def _parse_lab_datetime(df: pd.DataFrame, *, day_col: str = "LBDY", month_col: str = "LBMO", month_num_col: str = "LBMO_N", year_col: str = "LBYR") -> tuple[pd.Series, pd.Series]:
    """
    Return (datetime_series, display_date_series) for laboratory dates.
    Dates are parsed only when day/month/year are complete enough.
    """
    index = df.index
    day = pd.to_numeric(df[day_col], errors="coerce") if day_col in df.columns else pd.Series(pd.NA, index=index)
    year = pd.to_numeric(df[year_col], errors="coerce") if year_col in df.columns else pd.Series(pd.NA, index=index)

    if month_num_col in df.columns:
        month_num = pd.to_numeric(df[month_num_col], errors="coerce")
    elif month_col in df.columns:
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        month_num = df[month_col].astype("string").str.strip().str.upper().map(month_map)
    else:
        month_num = pd.Series(pd.NA, index=index)

    dt = pd.to_datetime(
        dict(year=year, month=month_num, day=day),
        errors="coerce",
    )

    display = pd.Series(pd.NA, index=index, dtype="object")
    valid = dt.notna()
    display.loc[valid] = dt.loc[valid].dt.strftime("%Y-%m-%d")

    return dt, display


def prepare_nr_lookup_tables(nr: pd.DataFrame, patinfo: pd.DataFrame, kt: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Prepare normal-range lookup tables using the same concepts as the Patient Profile code:
    nr + patinfo identify laboratory/site-specific normal ranges; kt links lab pages/visits to KTSPID.
    """
    nr = nr.copy()
    patinfo = patinfo.copy()
    kt = kt.copy()

    for d in (nr, patinfo, kt):
        d.columns = [str(c).replace("\ufeff", "").strip() for c in d.columns]

    required_nr = {"LBTEST", "PATNO"}
    missing_nr = required_nr - set(nr.columns)
    if missing_nr:
        raise ValueError(f"Missing columns in normal range dataset: {sorted(missing_nr)}")

    nr = nr.loc[nr["LBTEST"].notna() & nr["LBTEST"].astype(str).str.strip().ne("")].copy()
    nr["PATNO"] = pd.to_numeric(nr["PATNO"], errors="coerce")
    nr["LABNumber"] = nr["PATNO"].where(nr["PATNO"] > 10000)
    nr["LBTEST"] = normalize_test_code(nr["LBTEST"])

    for c in ["KTSPID", "AGE_L", "AGE_U", "LBORNRLO", "LBORNRHI", "LBSTNRLO", "LBSTNRHI"]:
        if c in nr.columns:
            nr[c] = _to_numeric_series(nr[c])

    if "NRSTDAT" in nr.columns:
        nr["NRSTDAT"] = parse_sas_date(nr["NRSTDAT"])
    if "NRENDAT" in nr.columns:
        nr["NRENDAT"] = parse_sas_date(nr["NRENDAT"])

    if not {"patno", "site_nr"}.issubset(patinfo.columns):
        raise ValueError("patinfo must contain columns 'patno' and 'site_nr'")
    patinfo["patno"] = pd.to_numeric(patinfo["patno"], errors="coerce")
    patinfo["site_nr"] = pd.to_numeric(patinfo["site_nr"], errors="coerce")
    labs_id = (
        patinfo.loc[patinfo["patno"] > 10000, ["patno", "site_nr"]]
        .rename(columns={"patno": "LABNumber"})
        .drop_duplicates(subset=["LABNumber"])
        .copy()
    )

    required_kt = {"PATNO", "PAGE_NO", "REP_NO", "KITCENTER", "KTSPID", "VISITNO"}
    missing_kt = required_kt - set(kt.columns)
    if missing_kt:
        raise ValueError(f"Missing columns in kit dataset: {sorted(missing_kt)}")

    labs_kits = (
        kt.loc[kt["KITCENTER"].notna(), ["PATNO", "PAGE_NO", "REP_NO", "KITCENTER", "KTSPID", "VISITNO"]]
        .rename(columns={
            "KITCENTER": "site_nr",
            "PAGE_NO": "labPage_no",
            "REP_NO": "labRep_no",
            "VISITNO": "_deb_visit_no",
        })
        .copy()
    )
    for c in ["PATNO", "labPage_no", "labRep_no", "site_nr", "KTSPID", "_deb_visit_no"]:
        labs_kits[c] = pd.to_numeric(labs_kits[c], errors="coerce")

    labs_id_nr = nr.merge(labs_id, on="LABNumber", how="left", validate="m:1").copy()
    if "SEX" in labs_id_nr.columns:
        labs_id_nr = labs_id_nr.rename(columns={"SEX": "sex_NR"})
    if "sex_NR" in labs_id_nr.columns:
        labs_id_nr["sex_NR"] = labs_id_nr["sex_NR"].astype("string").str.strip().str.lower()

    return {
        "nr": nr,
        "labs_id": labs_id,
        "labs_kits": labs_kits,
        "labs_id_nr": labs_id_nr,
    }


def select_kits_for_lab_module(labs_kits: pd.DataFrame, *, tot_repno: int, tot_lab_mods_no: int, curr_lab_mod_no: int) -> pd.DataFrame:
    x = labs_kits.copy()
    key = tot_repno * (x["labPage_no"] - 1) + x["labRep_no"]
    return x.loc[(key % tot_lab_mods_no) == curr_lab_mod_no].copy()


def _get_patient_covariates_from_dm(dm: pd.DataFrame) -> pd.DataFrame:
    dm = dm.copy()
    dm.columns = [str(c).replace("\ufeff", "").strip() for c in dm.columns]
    id_col = "patno_ref" if "patno_ref" in dm.columns else "PATNO"
    out = pd.DataFrame(index=dm.index)
    out["subject_id"] = dm[id_col].astype("string").str.strip()
    out["SEX"] = dm["SEX"].astype("string").str.strip() if "SEX" in dm.columns else pd.NA
    out["BASELINE_AGE"] = pd.to_numeric(dm["AGE"], errors="coerce") if "AGE" in dm.columns else pd.NA
    out["site_nr_cov"] = pd.to_numeric(dm["site_nr"], errors="coerce") if "site_nr" in dm.columns else pd.NA

    # Prefer explicit enrollment date; fallback to DM date components.
    if "enrDate" in dm.columns:
        out["ICDAT"] = pd.to_datetime(dm["enrDate"], errors="coerce", dayfirst=True)
    elif {"DMDY", "DMMO_N", "DMYR"}.issubset(dm.columns):
        out["ICDAT"] = pd.to_datetime(
            dict(
                year=pd.to_numeric(dm["DMYR"], errors="coerce"),
                month=pd.to_numeric(dm["DMMO_N"], errors="coerce"),
                day=pd.to_numeric(dm["DMDY"], errors="coerce"),
            ),
            errors="coerce",
        )
    else:
        out["ICDAT"] = pd.NaT

    return out.drop_duplicates(subset=["subject_id"], keep="first")


def _compute_abnormal_flag(result_value: pd.Series, low: pd.Series, high: pd.Series) -> pd.Series:
    res = _to_numeric_series(result_value)
    lo = pd.to_numeric(low, errors="coerce")
    hi = pd.to_numeric(high, errors="coerce")
    out = pd.Series(pd.NA, index=result_value.index, dtype="object")
    out.loc[res.notna() & lo.notna() & (res < lo)] = "Low"
    out.loc[res.notna() & hi.notna() & (res > hi)] = "High"
    out.loc[res.notna() & ((lo.notna()) | (hi.notna())) & out.isna()] = "Normal"
    return out


def attach_normal_ranges_to_lab_rows(
    lab_df: pd.DataFrame,
    dm_df: pd.DataFrame,
    lookup: dict[str, pd.DataFrame],
    *,
    patient_id_col_lab: str = "patno_ref",
    lab_test_col: str = "LBTEST",
    tot_repno: int,
    tot_lab_mods_no: int,
    curr_lab_mod_no: int,
) -> pd.DataFrame:
    """
    Attach normal ranges to all rows in a laboratory dataset.
    All laboratory rows are retained. Normal-range fields are filled when linkage succeeds.
    """
    lab = lab_df.copy()
    lab.columns = [str(c).replace("\ufeff", "").strip() for c in lab.columns]
    lab["_lab_rowid"] = range(len(lab))

    if lab_test_col not in lab.columns:
        lab["_nr_found"] = False
        return lab

    lab[lab_test_col] = normalize_test_code(lab[lab_test_col])
    lab["LBDAT_DT"], lab["LBDAT_ISO"] = _parse_lab_datetime(lab)

    for c in ["PATNO", "_deb_visit_no"]:
        if c in lab.columns:
            lab[c] = pd.to_numeric(lab[c], errors="coerce")

    if patient_id_col_lab in lab.columns:
        lab["subject_id"] = lab[patient_id_col_lab].astype("string").str.strip()
    elif "PATNO" in lab.columns:
        lab["subject_id"] = lab["PATNO"].astype("string").str.strip()
    else:
        lab["subject_id"] = pd.NA

    cov = _get_patient_covariates_from_dm(dm_df)
    lab = lab.merge(cov, on="subject_id", how="left")

    if "LBDAT_DT" in lab.columns:
        delta_years = (lab["LBDAT_DT"] - pd.to_datetime(lab["ICDAT"], errors="coerce")).dt.days / 365.25
        lab["AGE_FOR_NR"] = (pd.to_numeric(lab["BASELINE_AGE"], errors="coerce") + delta_years).where(
            delta_years.notna(),
            pd.to_numeric(lab["BASELINE_AGE"], errors="coerce"),
        )
    else:
        lab["AGE_FOR_NR"] = pd.to_numeric(lab["BASELINE_AGE"], errors="coerce")

    kits_mod = select_kits_for_lab_module(
        lookup["labs_kits"],
        tot_repno=tot_repno,
        tot_lab_mods_no=tot_lab_mods_no,
        curr_lab_mod_no=curr_lab_mod_no,
    )

    for c in ["PATNO", "_deb_visit_no"]:
        if c in kits_mod.columns:
            kits_mod[c] = pd.to_numeric(kits_mod[c], errors="coerce")

    lab_k = lab.merge(
        kits_mod,
        on=["PATNO", "_deb_visit_no"],
        how="left",
        suffixes=("", "_KT"),
    )

    left = lab_k.copy()
    right = lookup["labs_id_nr"].copy()
    right["LBTEST"] = normalize_test_code(right["LBTEST"])

    for c in ["site_nr", "KTSPID"]:
        if c in left.columns:
            left[c] = pd.to_numeric(left[c], errors="coerce")
        if c in right.columns:
            right[c] = pd.to_numeric(right[c], errors="coerce")

    merged = left.merge(
        right,
        left_on=["site_nr", "KTSPID", lab_test_col],
        right_on=["site_nr", "KTSPID", "LBTEST"],
        how="left",
        suffixes=("", "_NR"),
    )

    patient_sex = merged["SEX"].astype("string").str.strip().str.lower()
    nr_sex = merged["sex_NR"].astype("string").str.strip().str.lower() if "sex_NR" in merged.columns else pd.Series("", index=merged.index)
    sex_ok = nr_sex.isin(["both", "", "nan", "<na>"]) | patient_sex.eq(nr_sex)

    age = pd.to_numeric(merged.get("AGE_FOR_NR", pd.Series(pd.NA, index=merged.index)), errors="coerce")
    age_l = pd.to_numeric(merged.get("AGE_L", pd.Series(pd.NA, index=merged.index)), errors="coerce")
    age_u = pd.to_numeric(merged.get("AGE_U", pd.Series(pd.NA, index=merged.index)), errors="coerce")
    age_ok = (age_l.isna() | (age >= age_l)) & (age_u.isna() | (age <= age_u))

    lab_date = pd.to_datetime(merged.get("LBDAT_DT", pd.Series(pd.NaT, index=merged.index)), errors="coerce")
    nr_start = pd.to_datetime(merged.get("NRSTDAT", pd.Series(pd.NaT, index=merged.index)), errors="coerce")
    nr_end = pd.to_datetime(merged.get("NRENDAT", pd.Series(pd.NaT, index=merged.index)), errors="coerce")
    date_ok = lab_date.isna() | ((nr_start.isna() | (lab_date >= nr_start)) & (nr_end.isna() | (lab_date <= nr_end)))

    merged["_nr_match_ok"] = sex_ok & age_ok & date_ok & (
        merged.get("LBORNRLO", pd.Series(pd.NA, index=merged.index)).notna() |
        merged.get("LBORNRHI", pd.Series(pd.NA, index=merged.index)).notna() |
        merged.get("LBSTNRLO", pd.Series(pd.NA, index=merged.index)).notna() |
        merged.get("LBSTNRHI", pd.Series(pd.NA, index=merged.index)).notna()
    )

    matched = merged.loc[merged["_nr_match_ok"]].copy()
    if not matched.empty:
        age_span = pd.to_numeric(matched.get("AGE_U", pd.Series(pd.NA, index=matched.index)), errors="coerce") - pd.to_numeric(matched.get("AGE_L", pd.Series(pd.NA, index=matched.index)), errors="coerce")
        matched["_age_span"] = age_span.fillna(999999)
        if "NRSTDAT" in matched.columns:
            matched["NRSTDAT"] = pd.to_datetime(matched["NRSTDAT"], errors="coerce")
        matched = matched.sort_values(
            by=["_lab_rowid", "_age_span", "NRSTDAT"],
            ascending=[True, True, False],
            na_position="last",
        )
        best = matched.drop_duplicates(subset=["_lab_rowid"], keep="first").copy()
        nr_cols = [c for c in ["_lab_rowid", "LBORNRLO", "LBORNRHI", "LBSTNRLO", "LBSTNRHI", "LBORRESU", "LBSTRESU", "KTSPID", "NRSTDAT", "NRENDAT"] if c in best.columns]
        best = best[nr_cols].copy()
        best["_nr_found"] = True
        out = lab.merge(best, on="_lab_rowid", how="left")
        out["_nr_found"] = out["_nr_found"].fillna(False)
    else:
        out = lab.copy()
        out["_nr_found"] = False

    return out




# =========================================================
# TUMOR ASSESSMENT HELPERS
# =========================================================

def _first_available_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _numeric_from_crf_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", ".", regex=False), errors="coerce")


def derive_tumor_assessment_date(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Derive tumor assessment date from modality-specific CRF date fields.
    Priority: CT, MRI, PETCT, PER, OTH.
    Returns date_iso and method_source.
    """
    index = df.index
    out = pd.Series(pd.NA, index=index, dtype="object")
    source = pd.Series(pd.NA, index=index, dtype="object")

    modalities = [
        ("CT", "TUDY_CT", "TUMO_CT", "TUYR_CT"),
        ("MRI", "TUDY_MRI", "TUMO_MRI", "TUYR_MRI"),
        ("PETCT", "TUDY_PETCT", "TUMO_PETCT", "TUYR_PETCT"),
        ("PER", "TUDY_PER", "TUMO_PER", "TUYR_PER"),
        ("OTH", "TUDY_OTH", "TUMO_OTH", "TUYR_OTH"),
    ]

    for label, day_col, month_col, year_col in modalities:
        if not {day_col, month_col, year_col}.issubset(df.columns):
            continue
        vals = df.apply(
            lambda r: make_partial_date(
                r,
                day_col=day_col,
                month_col=month_col,
                year_col=year_col,
                output_format="iso",
            ),
            axis=1,
        )
        mask = out.isna() & vals.notna()
        out.loc[mask] = vals.loc[mask]
        source.loc[mask] = label

    return out, source


def clean_lesion_location(df: pd.DataFrame) -> pd.Series:
    """Use free-text location when the coded/display location is Other; otherwise use TULOC."""
    index = df.index
    loc = df["TULOC"].map(clean_string) if "TULOC" in df.columns else pd.Series(pd.NA, index=index, dtype="object")
    oth = df["TULOCOTH"].map(clean_string) if "TULOCOTH" in df.columns else pd.Series(pd.NA, index=index, dtype="object")
    mask_other = loc.astype("string").str.strip().str.casefold().eq("other") & oth.notna()
    loc = loc.where(~mask_other, oth)
    loc = loc.where(loc.notna(), oth)
    return loc


def is_meaningful_tumor_row(df: pd.DataFrame) -> pd.Series:
    candidates = [c for c in ["TUGRPID", "TULOC", "TULOCOTH", "TRORRESMAX", "TRORRESMIN", "TUMSTATE", "TULT", "TRREASND"] if c in df.columns]
    if not candidates:
        return pd.Series(False, index=df.index)
    tmp = pd.DataFrame({c: df[c].map(clean_string) for c in candidates})
    return tmp.notna().any(axis=1)


def build_tumor_source_derived_columns(df: pd.DataFrame, lesion_type_value: str) -> pd.DataFrame:
    out = df.copy()
    out["ASSESSMENT_DATE_DERIVED"], out["ASSESSMENT_METHOD_SOURCE"] = derive_tumor_assessment_date(out)
    out["LESION_TYPE_DERIVED"] = lesion_type_value
    out["LOCATION_DERIVED"] = clean_lesion_location(out)
    out["MEASUREMENT_VALUE_DERIVED"] = _numeric_from_crf_series(out["TRORRESMAX"]) if "TRORRESMAX" in out.columns else pd.NA
    out["MEASUREMENT_UNIT_DERIVED"] = "mm"
    out["NEW_LESION_FLAG_DERIVED"] = "Yes" if lesion_type_value == "new_lesion" else "No"
    if "TRINEVAL" in out.columns:
        out["NOT_DONE_REASON_DERIVED"] = coalesce_columns(out, [c for c in ["TRREASND", "TRINEVAL"] if c in out.columns])
    else:
        out["NOT_DONE_REASON_DERIVED"] = coalesce_columns(out, [c for c in ["TRREASND"] if c in out.columns])
    return out


def build_overall_response_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ASSESSMENT_DATE_DERIVED"] = out.apply(
        lambda r: make_partial_date(
            r,
            day_col="RSDY" if "RSDY" in out.columns else None,
            month_col="RSMO" if "RSMO" in out.columns else None,
            year_col="RSYR" if "RSYR" in out.columns else None,
            output_format="iso",
        ),
        axis=1,
    )
    return out




# =========================================================
# END-OF-STUDY / OVERALL SURVIVAL HELPERS
# =========================================================

def _truthy_scalar(v: Any) -> bool:
    if pd.isna(v):
        return False
    return str(v).strip().upper() in {"YES", "Y", "1", "TRUE", "T"}


def _date_from_parts_row(row: pd.Series, day: str, month: str, year: str) -> Any:
    return make_partial_date(row, day_col=day, month_col=month, year_col=year, output_format="iso")


def _clean_text_scalar(v: Any) -> Any:
    return clean_string(v)


def selected_tpg_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    TPG contains repeated termination reasons. A row is treated as selected if at least
    one DSEPOCH_* flag is Yes. This preserves the eCRF-recorded termination reason.
    """
    flag_cols = [c for c in ["DSEPOCH_C99158", "DSEPOCH_C123452", "DSEPOCH_C123453"] if c in df.columns]
    if not flag_cols:
        return df.iloc[0:0].copy()
    mask = pd.Series(False, index=df.index)
    for c in flag_cols:
        mask = mask | df[c].map(_truthy_scalar)
    return df.loc[mask].copy()


def derive_tpg_end_of_study(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive one end-of-study row per subject from TPG.
    Conservative interpretation:
    - if the selected reason is 'Completed...' => treatment_completed_flag Yes
    - otherwise treatment_completed_flag No and the selected DSTERM is retained as reason.
    Maintenance/follow-up completion are retained as No when treatment is not completed;
    source data do not contain a separate validated free-text reason in the current export.
    """
    if df.empty:
        return pd.DataFrame()

    d = df.copy()
    d["subject_id"] = coalesce_columns(d, [c for c in ["patno_ref", "PATNO"] if c in d.columns])
    d["END_OF_STUDY_DATE_DERIVED"] = d.apply(lambda r: _date_from_parts_row(r, "DSSTDY", "DSSTMO", "DSSTYR"), axis=1)

    selected = selected_tpg_rows(d)
    if selected.empty:
        # fallback: one row with latest available date per subject, preserving traceability.
        selected = d.copy()

    rows = []
    for sid, g in selected.groupby("subject_id", dropna=True):
        g = g.copy()
        # Prefer rows with dates and selected flags; keep first by date/source row.
        g["_date_sort"] = pd.to_datetime(g["END_OF_STUDY_DATE_DERIVED"], errors="coerce")
        g = g.sort_values(["_date_sort", "_source_row_id"], na_position="last")
        chosen = g.iloc[0]

        reason = _clean_text_scalar(chosen.get("DSTERM", pd.NA))
        completed = pd.notna(reason) and str(reason).strip().lower().startswith("completed")

        rows.append({
            "subject_id": sid,
            "treatment_completed_flag": "Yes" if completed else "No",
            "reason_treatment_not_completed": pd.NA if completed else reason,
            "maintenance_completed_flag": "Yes" if completed else "No",
            "reason_maintenance_not_completed": pd.NA,
            "followup_completed_flag": "Yes" if completed else "No",
            "reason_followup_not_completed": pd.NA,
            "end_of_study_date": chosen.get("END_OF_STUDY_DATE_DERIVED", pd.NA),
            "source_dataset": chosen.get("_source_dataset", "tpg"),
            "source_row_id": chosen.get("_source_row_id", pd.NA),
        })
    return pd.DataFrame(rows)


def derive_overall_survival(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["subject_id"] = coalesce_columns(d, [c for c in ["patno_ref", "PATNO"] if c in d.columns])
    d["last_contact_date"] = d.apply(lambda r: _date_from_parts_row(r, "SSDTCDY", "SSDTCMO", "SSDTCYR"), axis=1)
    d["death_date"] = d.apply(lambda r: _date_from_parts_row(r, "DTHDY", "DTHMO", "DTHYR"), axis=1)
    d["vital_status"] = coalesce_columns(d, [c for c in ["SSSTRESC"] if c in d.columns])
    d["reason"] = coalesce_columns(d, [c for c in ["DSTERM"] if c in d.columns])
    keep = d[["subject_id", "last_contact_date", "death_date", "vital_status", "reason", "_source_dataset", "_source_row_id"]].copy()
    # Keep rows with at least one survival-related field.
    info_cols = ["last_contact_date", "death_date", "vital_status", "reason"]
    keep = keep.loc[keep[info_cols].notna().any(axis=1)].copy()
    keep = keep.rename(columns={"_source_dataset": "source_dataset", "_source_row_id": "source_row_id"})
    return keep


def derive_patient_status_summary(subjects: pd.Series, survival: pd.DataFrame, eos: pd.DataFrame, treatment: pd.DataFrame) -> pd.DataFrame:
    rows = []
    trt = treatment.copy() if treatment is not None else pd.DataFrame()
    if not trt.empty and "administration_date" in trt.columns:
        trt["_admin_dt"] = pd.to_datetime(trt["administration_date"], errors="coerce")
        if "administered_flag" in trt.columns:
            administered = trt["administered_flag"].astype("string").str.strip().str.upper().isin(["YES", "Y", "1", "TRUE"])
            trt = trt.loc[administered | trt["administered_flag"].isna()].copy()

    surv = survival.copy() if survival is not None else pd.DataFrame()
    if not surv.empty:
        surv["_last_contact_dt"] = pd.to_datetime(surv["last_contact_date"], errors="coerce")
        surv["_death_dt"] = pd.to_datetime(surv["death_date"], errors="coerce")
        surv["_status_dt"] = surv["_death_dt"].combine_first(surv["_last_contact_dt"])

    for sid in subjects.dropna().astype(str).str.strip().drop_duplicates():
        first_dose = pd.NA
        last_dose = pd.NA
        status = pd.NA
        last_contact = pd.NA
        death_date = pd.NA
        eot = pd.NA
        src_dataset = pd.NA
        src_row = pd.NA

        if not trt.empty and "subject_id" in trt.columns:
            tg = trt.loc[trt["subject_id"].astype(str).str.strip().eq(sid)].copy()
            if not tg.empty:
                dt = tg["_admin_dt"].dropna()
                if not dt.empty:
                    first_dose = dt.min().strftime("%Y-%m-%d")
                    last_dose = dt.max().strftime("%Y-%m-%d")

        if eos is not None and not eos.empty:
            eg = eos.loc[eos["subject_id"].astype(str).str.strip().eq(sid)]
            if not eg.empty:
                eot = eg.iloc[0].get("end_of_study_date", pd.NA)
                src_dataset = eg.iloc[0].get("source_dataset", pd.NA)
                src_row = eg.iloc[0].get("source_row_id", pd.NA)

        if not surv.empty and "subject_id" in surv.columns:
            sg = surv.loc[surv["subject_id"].astype(str).str.strip().eq(sid)].copy()
            if not sg.empty:
                # If any row says Dead, use the death row; otherwise use the latest contact/status row.
                dead = sg.loc[sg["vital_status"].astype("string").str.strip().str.upper().eq("DEAD")]
                chosen = dead.sort_values("_status_dt", na_position="last").iloc[-1] if not dead.empty else sg.sort_values("_status_dt", na_position="last").iloc[-1]
                status = chosen.get("vital_status", pd.NA)
                last_contact = chosen.get("last_contact_date", pd.NA)
                death_date = chosen.get("death_date", pd.NA)
                src_dataset = chosen.get("source_dataset", src_dataset)
                src_row = chosen.get("source_row_id", src_row)

        rows.append({
            "subject_id": sid,
            "patient_status": status,
            "first_dose_date": first_dose,
            "last_dose_date": last_dose,
            "end_of_treatment_date": eot,
            "last_contact_date": last_contact,
            "death_date": death_date,
            "source_dataset": src_dataset,
            "source_row_id": src_row,
        })

    return pd.DataFrame(rows)




# =========================================================
# VIEDOC EXPORT HELPERS
# =========================================================

def read_viedoc_double_header_csv(path: Path, sep: str = ",") -> pd.DataFrame:
    """
    Read Viedoc exports where row 1 contains long labels and row 2 contains short codes.
    The resulting DataFrame uses short-code columns and keeps a code->label mapping
    in df.attrs["viedoc_label_map"].
    """
    last_exc = None
    for enc in ["utf-8-sig", "utf-8", "cp1252", "latin1"]:
        try:
            raw = pd.read_csv(path, header=None, sep=sep, encoding=enc, low_memory=False)
            if raw.shape[0] < 2 or raw.shape[1] <= 1:
                continue
            labels = raw.iloc[0].astype("string").str.replace("\ufeff", "", regex=False).str.strip()
            codes = raw.iloc[1].astype("string").str.replace("\ufeff", "", regex=False).str.strip()
            df = raw.iloc[2:].copy()
            df.columns = [str(c) for c in codes]
            df = df.reset_index(drop=True)
            df.attrs["viedoc_label_map"] = dict(zip([str(c) for c in codes], [str(x) for x in labels]))
            return df
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Unable to read Viedoc double-header CSV {path}: {last_exc}")


def parse_date_to_iso_series(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", dayfirst=False)
    # fallback dayfirst only for unresolved
    unresolved = dt.isna()
    if unresolved.any():
        dt2 = pd.to_datetime(series.loc[unresolved], errors="coerce", dayfirst=True)
        dt.loc[unresolved] = dt2
    return dt.dt.strftime("%Y-%m-%d").astype("object").where(dt.notna(), pd.NA)


def derive_viedoc_visit_order(value: Any) -> float:
    if pd.isna(value):
        return 99999.0
    s = str(value).strip()
    su = s.upper()
    if "SCREEN" in su:
        return 0.0
    if "END OF TREATMENT" in su or "EOT" in su:
        return 9700.0
    if "END OF SCREENING" in su:
        return 980.0
    m = re.search(r"CYCLE\s*0*(\d+)\s*DAY\s*0*(\d+)(?:\s*[-–]\s*0*(\d+))?", su)
    if m:
        cycle = int(m.group(1))
        day = int(m.group(2))
        day2 = int(m.group(3)) if m.group(3) else 0
        return 1000.0 + cycle * 100.0 + day + (day2 / 1000.0)
    m2 = re.search(r"\bC\s*0*(\d+)\s*D\s*0*(\d+)", su)
    if m2:
        return 1000.0 + int(m2.group(1)) * 100.0 + int(m2.group(2))
    return 9995.0


def viedoc_wide_labs_to_long(src: pd.DataFrame, *, source_key: str, panel: str, prefixes: list[str], sample_prefix: str | None = None) -> pd.DataFrame:
    """
    Convert Viedoc wide laboratory exports (e.g. ALT_LBORRES, ALT_LBORNR_Lower) to
    the common long laboratory_measurements structure used by Patient Profile.
    """
    rows = []
    label_map = src.attrs.get("viedoc_label_map", {}) or {}
    for prefix in prefixes:
        test_col = f"{prefix}_LBTEST"
        value_col = f"{prefix}_LBORRES"
        value_col_alt = f"{prefix}_LBORRESNUM"
        unit_col = f"{prefix}_LBORRESU"
        low_col = f"{prefix}_LBORNR_Lower"
        high_col = f"{prefix}_LBORNR_Upper"
        range_col = f"{prefix}_LBORNR"
        comp_col = f"{prefix}_LBORNR_Comparator"
        clsig_col = f"{prefix}_LBCLSIG"
        if value_col not in src.columns and value_col_alt in src.columns:
            value_col = value_col_alt
        if test_col not in src.columns and value_col not in src.columns:
            continue

        for idx, r in src.iterrows():
            value = r.get(value_col, pd.NA)
            test_name = clean_string(r.get(test_col, pd.NA))
            if pd.isna(test_name):
                test_name = label_map.get(value_col, prefix)
            # Keep rows with either a result, a not-collected reason, or a test name.
            sample_done = r.get(f"{sample_prefix}_LBPERF", pd.NA) if sample_prefix else pd.NA
            not_done_reason = r.get(f"{sample_prefix}_LBREASND", pd.NA) if sample_prefix else pd.NA
            meaningful = pd.notna(clean_string(value)) or pd.notna(clean_string(not_done_reason)) or pd.notna(clean_string(test_name))
            if not meaningful:
                continue
            low = r.get(low_col, pd.NA)
            high = r.get(high_col, pd.NA)
            result_num = pd.to_numeric(str(value).replace(",", "."), errors="coerce") if pd.notna(value) else pd.NA
            low_num = pd.to_numeric(str(low).replace(",", "."), errors="coerce") if pd.notna(low) else pd.NA
            high_num = pd.to_numeric(str(high).replace(",", "."), errors="coerce") if pd.notna(high) else pd.NA
            abnormal = pd.NA
            if pd.notna(result_num):
                if pd.notna(low_num) and result_num < low_num:
                    abnormal = "Low"
                elif pd.notna(high_num) and result_num > high_num:
                    abnormal = "High"
                elif pd.notna(low_num) or pd.notna(high_num):
                    abnormal = "Normal"

            assessment_date = r.get(f"{sample_prefix}_LBDAT", pd.NA) if sample_prefix else pd.NA
            if pd.isna(clean_string(assessment_date)):
                assessment_date = r.get("EventDate", pd.NA)

            rows.append({
                "subject_id": clean_string(r.get("SubjectId", pd.NA)),
                "visit_name": clean_string(r.get("EventName", pd.NA)),
                "assessment_date": parse_mixed_date(assessment_date),
                "panel": panel,
                "parameter": test_name,
                "result_value": value,
                "result_unit": r.get(unit_col, pd.NA),
                "normal_low": low,
                "normal_high": high,
                "normal_range_source": "viedoc_export",
                "abnormal_flag": abnormal,
                "clinically_significant_flag": normalize_flag(r.get(clsig_col, pd.NA)),
                "not_done_reason": not_done_reason,
                "source_dataset": source_key,
                "source_row_id": r.get("_source_row_id", pd.NA),
            })
    return pd.DataFrame(rows)


def derive_viedoc_visit_columns(src: pd.DataFrame) -> pd.DataFrame:
    out = src.copy()
    out["VISIT_ORDER_DERIVED"] = out["EventName"].map(derive_viedoc_visit_order) if "EventName" in out.columns else pd.NA
    out["VISIT_DATE_DERIVED"] = parse_date_to_iso_series(out["EventDate"]) if "EventDate" in out.columns else pd.NA
    if "VSPERF" in out.columns:
        out["PERFORMED_FLAG_DERIVED"] = out["VSPERF"].map(normalize_flag)
    elif "LBPERF" in out.columns:
        out["PERFORMED_FLAG_DERIVED"] = out["LBPERF"].map(normalize_flag)
    else:
        out["PERFORMED_FLAG_DERIVED"] = "Yes"
    reason_cols = [c for c in ["VSREASND", "LBREASND", "Reason not performed"] if c in out.columns]
    out["NOT_DONE_REASON_DERIVED"] = coalesce_columns(out, reason_cols) if reason_cols else pd.Series(pd.NA, index=out.index, dtype="object")
    tmp = pd.DataFrame(index=out.index)
    tmp["performed_flag"] = out["PERFORMED_FLAG_DERIVED"]
    tmp["not_done_reason"] = out["NOT_DONE_REASON_DERIVED"]
    out["VISIT_STATUS_DERIVED"] = tmp.apply(derive_visit_status, axis=1)
    out["OPTIONAL_FLAG_DERIVED"] = out["EventName"].map(derive_optional_visit_flag) if "EventName" in out.columns else "No"
    return out


@dataclass
class LoadedDataset:
    key: str
    path: Path
    df: pd.DataFrame


class CommonTableGenerator:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base_path = Path(config["base_path"])
        self.study_id = str(config.get("study_id", "STUDY_001"))
        self.edc_system = str(config.get("edc_system", "EDC"))
        self.data_model_version = str(config.get("data_model_version", "1.0"))
        self.code_version = str(config.get("code_version", "unversioned"))
        self.config_version = str(config.get("config_version") or stable_config_hash(config))
        self.snapshot_datetime = self._get_snapshot_datetime()
        self.datasets: dict[str, LoadedDataset] = {}
        self.mapping_qc_rows: list[dict[str, Any]] = []
        self.log_rows: list[dict[str, Any]] = []

    def _get_snapshot_datetime(self) -> str:
        if self.config.get("snapshot_datetime"):
            return str(self.config["snapshot_datetime"])
        inferred = []
        for spec in self.config.get("datasets", {}).values():
            files = spec.get("files") or ([spec["file"]] if "file" in spec else [])
            for fn in files:
                dt = derive_snapshot_from_filename(str(fn))
                if dt:
                    inferred.append(dt)
        return max(inferred) if inferred else "N/A"

    def load_dataset(self, key: str) -> pd.DataFrame:
        if key in self.datasets:
            return self.datasets[key].df.copy()
        spec = self.config.get("datasets", {}).get(key)
        if spec is None:
            raise KeyError(f"Dataset key '{key}' is not configured.")
        files = spec.get("files") or ([spec["file"]] if "file" in spec else [])
        if not files:
            raise ValueError(f"Dataset key '{key}' has no file(s).")
        frames = []
        paths = []
        for file_name in files:
            path = self.base_path / file_name
            if not path.exists():
                raise FileNotFoundError(f"File not found for dataset '{key}': {path}")
            read_mode = spec.get("read_mode")
            if read_mode == "viedoc_double_header":
                df = read_viedoc_double_header_csv(path, sep=str(spec.get("sep", ",")))
            else:
                df = read_csv_flexible(path, header=int(spec.get("header", 0)), sep=str(spec.get("sep", ",")))
            df["_source_dataset"] = key
            df["_source_file"] = path.name
            df["_source_row_id"] = range(1, len(df) + 1)
            frames.append(df)
            paths.append(path)
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        self.datasets[key] = LoadedDataset(key=key, path=paths[0], df=out)
        return out.copy()

    def _base_values(self) -> dict[str, Any]:
        return {"study_id": self.study_id, "edc_system": self.edc_system, "snapshot_datetime": self.snapshot_datetime}

    def _mapping_qc(self, table_name: str, output_column: str, mapping_type: str, source_dataset: str | None,
                    source_column: str | None, required: bool, status: str, message: str) -> None:
        self.mapping_qc_rows.append({
            "table_name": table_name, "output_column": output_column, "mapping_type": mapping_type,
            "source_dataset": source_dataset, "source_column": source_column, "required": required,
            "status": status, "message": message,
        })

    def _apply_mapping(self, df: pd.DataFrame, mapping: dict[str, Any], table_name: str, source_key: str, out_col: str) -> pd.Series:
        required = bool(mapping.get("required", False))
        if "constant" in mapping:
            self._mapping_qc(table_name, out_col, "constant", source_key, None, required, "OK", "")
            return pd.Series([mapping["constant"]] * len(df), index=df.index, dtype="object")
        if "checkbox_labels" in mapping:
            spec = mapping["checkbox_labels"]
            label_map = spec.get("columns", {}) or {}
            other_flag = spec.get("other_flag")
            other_text = spec.get("other_text", []) or []
            source_cols = list(label_map.keys())
            if other_flag:
                source_cols.append(other_flag)
            source_cols.extend(other_text)
            available = [c for c in source_cols if c in df.columns]
            status = "OK" if available else ("ERROR" if required else "WARNING")
            self._mapping_qc(
                table_name,
                out_col,
                "checkbox_labels",
                source_key,
                "; ".join(map(str, source_cols)),
                required,
                status,
                "" if available else "No checkbox/source column found.",
            )
            return derive_checkbox_labels(df, spec) if available else pd.Series(pd.NA, index=df.index, dtype="object")
        if "source" in mapping:
            candidates = mapping["source"] if isinstance(mapping["source"], list) else [mapping["source"]]
            available = [c for c in candidates if c in df.columns]
            status = "OK" if available else ("ERROR" if required else "WARNING")
            self._mapping_qc(table_name, out_col, "source", source_key, "; ".join(map(str, candidates)), required, status,
                             "" if available else "No source column found.")
            return coalesce_columns(df, available) if available else pd.Series(pd.NA, index=df.index, dtype="object")
        if "parsed_date" in mapping:
            col = mapping["parsed_date"]
            ok = col in df.columns
            self._mapping_qc(table_name, out_col, "parsed_date", source_key, str(col), required,
                             "OK" if ok else ("ERROR" if required else "WARNING"), "" if ok else "No source column found.")
            return df[col].map(parse_mixed_date) if ok else pd.Series(pd.NA, index=df.index, dtype="object")
        if "date_from_parts" in mapping:
            spec = mapping["date_from_parts"]
            cols = [spec.get("day"), spec.get("month"), spec.get("year")]
            missing = [c for c in cols if c and c not in df.columns]
            self._mapping_qc(table_name, out_col, "date_from_parts", source_key, "; ".join([str(c) for c in cols if c]),
                             required, "OK" if not missing else ("ERROR" if required else "WARNING"),
                             "" if not missing else f"Missing columns: {missing}")
            if missing:
                return pd.Series(pd.NA, index=df.index, dtype="object")
            return df.apply(
                lambda r: make_partial_date(
                    r,
                    day_col=spec.get("day"),
                    month_col=spec.get("month"),
                    year_col=spec.get("year"),
                    output_format=str(spec.get("output_format", "crf")),
                ),
                axis=1,
            )
        if "lookup" in mapping:
            spec = mapping["lookup"]
            ref_key = spec.get("dataset")
            left_on = spec.get("left_on")
            right_on = spec.get("right_on")
            value_col = spec.get("value")
            problems = []
            if not ref_key:
                problems.append("lookup.dataset")
            if not left_on or left_on not in df.columns:
                problems.append(f"left_on:{left_on}")
            try:
                ref = self.load_dataset(str(ref_key)) if ref_key else pd.DataFrame()
            except Exception:
                ref = pd.DataFrame()
                problems.append(f"dataset:{ref_key}")
            if right_on not in ref.columns:
                problems.append(f"right_on:{right_on}")
            if value_col not in ref.columns:
                problems.append(f"value:{value_col}")
            status = "OK" if not problems else ("ERROR" if required else "WARNING")
            self._mapping_qc(
                table_name, out_col, "lookup", source_key,
                f"{left_on} -> {ref_key}.{right_on}.{value_col}",
                required, status, "" if not problems else "Missing lookup components: " + ", ".join(problems)
            )
            if problems:
                return pd.Series(pd.NA, index=df.index, dtype="object")
            ref_map = (
                ref[[right_on, value_col]]
                .dropna(subset=[right_on])
                .drop_duplicates(subset=[right_on], keep="first")
                .assign(_lookup_key=lambda x: x[right_on].astype(str).str.strip())
                .set_index("_lookup_key")[value_col]
                .to_dict()
            )
            return df[left_on].astype(str).str.strip().map(ref_map)
        self._mapping_qc(table_name, out_col, "unmapped", source_key, None, required, "ERROR" if required else "WARNING", "No mapping rule provided.")
        return pd.Series(pd.NA, index=df.index, dtype="object")

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict[str, Any]] | None) -> pd.DataFrame:
        out = df.copy()
        for f in filters or []:
            col = f.get("column")
            if not col or col not in out.columns:
                continue
            if "equals" in f:
                val = str(f["equals"]).strip().upper()
                out = out.loc[out[col].astype(str).str.strip().str.upper().eq(val)].copy()
            if "contains" in f:
                val = str(f["contains"]).strip()
                out = out.loc[out[col].astype(str).str.contains(val, case=False, na=False, regex=False)].copy()
            if f.get("not_empty"):
                out = out.loc[out[col].map(clean_string).notna()].copy()
        return out

    def _rule_for_source(self, cfg: dict[str, Any], source_key: str, col: str) -> dict[str, Any]:
        """Return a mapping rule, allowing per-source overrides for multi-source tables."""
        rule = dict(cfg.get("columns", {}).get(col, {}))
        override = cfg.get("source_overrides", {}).get(source_key, {})
        rule.update(override.get("columns", {}).get(col, {}))
        return rule





    def build_end_of_study_status(self) -> pd.DataFrame:
        table_name = "end_of_study_status"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_key = cfg.get("source", "tpg")
        src = self.load_dataset(source_key)
        derived = derive_tpg_end_of_study(src)
        if derived.empty:
            self._log(table_name, "GENERATED", 0, 0, "No end-of-study rows derived.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        out = pd.DataFrame(index=derived.index)
        base = self._base_values()
        for col in STANDARD_TABLES[table_name]:
            if col in base:
                out[col] = base[col]
            elif col in derived.columns:
                out[col] = derived[col]
            else:
                out[col] = pd.NA

        n_subjects = int(out["subject_id"].nunique(dropna=True))
        self._mapping_qc(table_name, "end_of_study_date", "derived", source_key, "DSSTDY; DSSTMO; DSSTYR", False, "OK", "")
        self._mapping_qc(table_name, "reason_treatment_not_completed", "derived", source_key, "DSTERM selected by DSEPOCH_* flags", False, "OK", "")
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out[STANDARD_TABLES[table_name]]

    def build_overall_survival(self) -> pd.DataFrame:
        table_name = "overall_survival"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_key = cfg.get("source", "ovsr")
        src = self.load_dataset(source_key)
        derived = derive_overall_survival(src)

        out = pd.DataFrame(index=derived.index)
        base = self._base_values()
        for col in STANDARD_TABLES[table_name]:
            if col in base:
                out[col] = base[col]
            elif col in derived.columns:
                out[col] = derived[col]
            else:
                out[col] = pd.NA

        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)

        n_subjects = int(out["subject_id"].nunique(dropna=True)) if not out.empty else 0
        self._mapping_qc(table_name, "last_contact_date", "date_from_parts", source_key, "SSDTCDY; SSDTCMO; SSDTCYR", False, "OK", "")
        self._mapping_qc(table_name, "death_date", "date_from_parts", source_key, "DTHDY; DTHMO; DTHYR", False, "OK", "")
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out[STANDARD_TABLES[table_name]]

    def build_patient_status_summary(self) -> pd.DataFrame:
        table_name = "patient_status_summary"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        # Subjects from subject master source.
        dm_key = cfg.get("subject_dataset", "dm")
        dm = self.load_dataset(dm_key)
        subjects = coalesce_columns(dm, [c for c in ["patno_ref", "PATNO"] if c in dm.columns])

        ovsr_key = cfg.get("survival_dataset", "ovsr")
        tpg_key = cfg.get("end_of_study_dataset", "tpg")
        survival_raw = derive_overall_survival(self.load_dataset(ovsr_key))
        eos = derive_tpg_end_of_study(self.load_dataset(tpg_key))

        treatment = self.build_table("treatment_administration") if "treatment_administration" in self.config.get("tables", {}) else pd.DataFrame()

        derived = derive_patient_status_summary(subjects, survival_raw, eos, treatment)

        out = pd.DataFrame(index=derived.index)
        base = self._base_values()
        for col in STANDARD_TABLES[table_name]:
            if col in base:
                out[col] = base[col]
            elif col in derived.columns:
                out[col] = derived[col]
            else:
                out[col] = pd.NA

        n_subjects = int(out["subject_id"].nunique(dropna=True)) if not out.empty else 0
        self._mapping_qc(table_name, "patient_status", "derived", "ovsr", "SSSTRESC latest/death-prioritized", False, "OK", "")
        self._mapping_qc(table_name, "first_dose_date/last_dose_date", "derived", "treatment_administration", "administration_date", False, "OK", "")
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out[STANDARD_TABLES[table_name]]


    def build_tumor_assessments(self) -> pd.DataFrame:
        table_name = "tumor_assessments"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_keys = cfg.get("sources") or ([cfg["source"]] if "source" in cfg else [])
        frames: list[pd.DataFrame] = []

        for source_key in source_keys:
            override = cfg.get("source_overrides", {}).get(source_key, {})
            src = self._apply_filters(self.load_dataset(source_key), override.get("filters", cfg.get("filters"))).copy()

            lesion_type = override.get("lesion_type", source_key)
            src = build_tumor_source_derived_columns(src, lesion_type)
            src = src.loc[is_meaningful_tumor_row(src)].copy()

            mapped = pd.DataFrame(index=src.index)
            base = self._base_values()
            for col in STANDARD_TABLES[table_name]:
                if col in base:
                    mapped[col] = base[col]
                elif col == "source_dataset":
                    mapped[col] = src.get("_source_dataset", source_key)
                elif col == "source_row_id":
                    mapped[col] = src.get("_source_row_id", pd.NA)
                else:
                    rule = self._rule_for_source(cfg, source_key, col)
                    mapped[col] = self._apply_mapping(src, rule, table_name, source_key, col)
                    if rule.get("normalize_flag"):
                        mapped[col] = mapped[col].map(normalize_flag)

            frames.append(mapped[STANDARD_TABLES[table_name]])

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_TABLES[table_name])
        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)

        n_subjects = int(out["subject_id"].nunique(dropna=True)) if "subject_id" in out.columns else 0
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out

    def build_overall_tumor_response(self) -> pd.DataFrame:
        table_name = "overall_tumor_response"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_keys = cfg.get("sources") or ([cfg["source"]] if "source" in cfg else [])
        frames: list[pd.DataFrame] = []
        for source_key in source_keys:
            override = cfg.get("source_overrides", {}).get(source_key, {})
            src = self._apply_filters(self.load_dataset(source_key), override.get("filters", cfg.get("filters"))).copy()
            src = build_overall_response_derived_columns(src)

            # Keep only rows with actual assessment information, then deduplicate repeated RS rows.
            info_cols = [c for c in ["TRGRESP", "NTRGRESP", "NEWYN", "OVRLRESP", "RSREASND", "ASSESSMENT_DATE_DERIVED"] if c in src.columns]
            if info_cols:
                tmp = pd.DataFrame({c: src[c].map(clean_string) for c in info_cols})
                src = src.loc[tmp.notna().any(axis=1)].copy()

            mapped = pd.DataFrame(index=src.index)
            base = self._base_values()
            for col in STANDARD_TABLES[table_name]:
                if col in base:
                    mapped[col] = base[col]
                elif col == "source_dataset":
                    mapped[col] = src.get("_source_dataset", source_key)
                elif col == "source_row_id":
                    mapped[col] = src.get("_source_row_id", pd.NA)
                else:
                    rule = self._rule_for_source(cfg, source_key, col)
                    mapped[col] = self._apply_mapping(src, rule, table_name, source_key, col)
                    if rule.get("normalize_flag"):
                        mapped[col] = mapped[col].map(normalize_flag)

            frames.append(mapped[STANDARD_TABLES[table_name]])

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_TABLES[table_name])
        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)

        n_subjects = int(out["subject_id"].nunique(dropna=True)) if "subject_id" in out.columns else 0
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out


    def build_visit_status(self) -> pd.DataFrame:
        table_name = "visit_status"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_keys = cfg.get("sources") or ([cfg["source"]] if "source" in cfg else [])
        frames: list[pd.DataFrame] = []

        for source_key in source_keys:
            override = cfg.get("source_overrides", {}).get(source_key, {})
            src = self._apply_filters(self.load_dataset(source_key), override.get("filters", cfg.get("filters"))).copy()

            # Derived fields for standardized visit timeline.
            if override.get("visit_format") == "viedoc":
                src = derive_viedoc_visit_columns(src)
            else:
                if "VISIT" in src.columns:
                    src["VISIT_ORDER_DERIVED"] = src["VISIT"].map(derive_visit_order)
                    src["OPTIONAL_FLAG_DERIVED"] = src["VISIT"].map(derive_optional_visit_flag)
                else:
                    src["VISIT_ORDER_DERIVED"] = pd.NA
                    src["OPTIONAL_FLAG_DERIVED"] = pd.NA

                src["VISIT_DATE_DERIVED"] = src.apply(
                    lambda r: make_partial_date(
                        r,
                        day_col="SVSTDY" if "SVSTDY" in src.columns else None,
                        month_col="SVSTMO" if "SVSTMO" in src.columns else None,
                        year_col="SVSTYR" if "SVSTYR" in src.columns else None,
                        output_format="iso",
                    ),
                    axis=1,
                )

                src["PERFORMED_FLAG_DERIVED"] = src["VISIT_DATE_DERIVED"].map(derive_performed_flag_from_date)

                reason_cols = [c for c in ["SVRSN", "SVCOM"] if c in src.columns]
                src["NOT_DONE_REASON_DERIVED"] = coalesce_columns(src, reason_cols) if reason_cols else pd.Series(pd.NA, index=src.index, dtype="object")

                # Construct status from derived columns.
                tmp = pd.DataFrame(index=src.index)
                tmp["performed_flag"] = src["PERFORMED_FLAG_DERIVED"]
                tmp["not_done_reason"] = src["NOT_DONE_REASON_DERIVED"]
                src["VISIT_STATUS_DERIVED"] = tmp.apply(derive_visit_status, axis=1)

            mapped = pd.DataFrame(index=src.index)
            base = self._base_values()
            for col in STANDARD_TABLES[table_name]:
                if col in base:
                    mapped[col] = base[col]
                elif col == "source_dataset":
                    mapped[col] = src.get("_source_dataset", source_key)
                elif col == "source_row_id":
                    mapped[col] = src.get("_source_row_id", pd.NA)
                else:
                    rule = self._rule_for_source(cfg, source_key, col)
                    mapped[col] = self._apply_mapping(src, rule, table_name, source_key, col)
                    if rule.get("normalize_flag"):
                        mapped[col] = mapped[col].map(normalize_flag)

            frames.append(mapped[STANDARD_TABLES[table_name]])

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_TABLES[table_name])
        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.sort_values(["subject_id", "visit_order", "visit_date"], na_position="last")
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)

        n_subjects = int(out["subject_id"].nunique(dropna=True)) if "subject_id" in out.columns else 0
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out


    def _get_normal_range_lookup(self) -> dict[str, pd.DataFrame] | None:
        cfg = self.config.get("normal_ranges", {})
        if not cfg or not cfg.get("enabled", False):
            return None
        if hasattr(self, "_normal_range_lookup_cache"):
            return self._normal_range_lookup_cache

        nr_key = cfg.get("nr_dataset", "nr")
        patinfo_key = cfg.get("patinfo_dataset", "patinfo")
        kt_key = cfg.get("kt_dataset", "kt")

        nr = self.load_dataset(nr_key)
        patinfo = self.load_dataset(patinfo_key)
        kt = self.load_dataset(kt_key)

        self._normal_range_lookup_cache = prepare_nr_lookup_tables(nr, patinfo, kt)
        return self._normal_range_lookup_cache

    def build_laboratory_measurements(self) -> pd.DataFrame:
        table_name = "laboratory_measurements"
        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_keys = cfg.get("sources") or ([cfg["source"]] if "source" in cfg else [])
        lookup = self._get_normal_range_lookup()
        dm_df = None
        if lookup is not None:
            dm_key = cfg.get("dm_dataset", "dm")
            if dm_key in self.config.get("datasets", {}):
                dm_df = self.load_dataset(dm_key)

        frames: list[pd.DataFrame] = []
        for source_key in source_keys:
            override = cfg.get("source_overrides", {}).get(source_key, {})
            src = self._apply_filters(self.load_dataset(source_key), override.get("filters", cfg.get("filters")))

            if override.get("lab_format") == "viedoc_wide":
                long_src = viedoc_wide_labs_to_long(
                    src,
                    source_key=source_key,
                    panel=override.get("panel", source_key),
                    prefixes=override.get("prefixes", []),
                    sample_prefix=override.get("sample_prefix"),
                )
                if long_src.empty:
                    frames.append(pd.DataFrame(columns=STANDARD_TABLES[table_name]))
                    continue
                mapped = pd.DataFrame(index=long_src.index)
                base = self._base_values()
                for col in STANDARD_TABLES[table_name]:
                    if col in base:
                        mapped[col] = base[col]
                    elif col in long_src.columns:
                        mapped[col] = long_src[col]
                    else:
                        mapped[col] = pd.NA
                frames.append(mapped[STANDARD_TABLES[table_name]])
                self._mapping_qc(table_name, "laboratory_measurements", "viedoc_wide", source_key, "; ".join(override.get("prefixes", [])), False, "OK", "")
                continue

            nr_cfg = override.get("normal_range", {})
            if lookup is not None and dm_df is not None and nr_cfg.get("enabled", False):
                try:
                    src = attach_normal_ranges_to_lab_rows(
                        lab_df=src,
                        dm_df=dm_df,
                        lookup=lookup,
                        patient_id_col_lab=nr_cfg.get("patient_id_col_lab", "patno_ref"),
                        lab_test_col=nr_cfg.get("lab_test_col", "LBTEST"),
                        tot_repno=int(nr_cfg["tot_repno"]),
                        tot_lab_mods_no=int(nr_cfg["tot_lab_mods_no"]),
                        curr_lab_mod_no=int(nr_cfg["curr_lab_mod_no"]),
                    )
                    self._mapping_qc(
                        table_name, "normal_low/high", "normal_range", source_key,
                        f"tot_repno={nr_cfg['tot_repno']}; tot_lab_mods_no={nr_cfg['tot_lab_mods_no']}; curr_lab_mod_no={nr_cfg['curr_lab_mod_no']}",
                        False, "OK", "",
                    )
                except Exception as exc:
                    src = src.copy()
                    src["_nr_found"] = False
                    self._mapping_qc(
                        table_name, "normal_low/high", "normal_range", source_key,
                        "normal range linkage", False, "WARNING", str(exc),
                    )
            else:
                src = src.copy()
                src["_nr_found"] = False
                self._mapping_qc(
                    table_name, "normal_low/high", "normal_range", source_key,
                    "normal range linkage", False, "WARNING", "Normal range linkage not enabled/configured for this source.",
                )

            # Derived helper columns used by generic mapping.
            if "subject_id" not in src.columns:
                if "patno_ref" in src.columns:
                    src["subject_id"] = src["patno_ref"].astype("string").str.strip()
                elif "PATNO" in src.columns:
                    src["subject_id"] = src["PATNO"].astype("string").str.strip()

            if "LBDAT_ISO" not in src.columns:
                _, src["LBDAT_ISO"] = _parse_lab_datetime(src)

            if "NORMAL_LOW_DERIVED" not in src.columns:
                src["NORMAL_LOW_DERIVED"] = src["LBORNRLO"] if "LBORNRLO" in src.columns else pd.NA
                if "LBSTNRLO" in src.columns:
                    src["NORMAL_LOW_DERIVED"] = src["NORMAL_LOW_DERIVED"].combine_first(src["LBSTNRLO"])
                if "ANL_LBORRESRNR_Lower" in src.columns:
                    src["NORMAL_LOW_DERIVED"] = src["NORMAL_LOW_DERIVED"].combine_first(src["ANL_LBORRESRNR_Lower"])

            if "NORMAL_HIGH_DERIVED" not in src.columns:
                src["NORMAL_HIGH_DERIVED"] = src["LBORNRHI"] if "LBORNRHI" in src.columns else pd.NA
                if "LBSTNRHI" in src.columns:
                    src["NORMAL_HIGH_DERIVED"] = src["NORMAL_HIGH_DERIVED"].combine_first(src["LBSTNRHI"])
                if "ANL_LBORRESRNR_Upper" in src.columns:
                    src["NORMAL_HIGH_DERIVED"] = src["NORMAL_HIGH_DERIVED"].combine_first(src["ANL_LBORRESRNR_Upper"])

            src["NORMAL_RANGE_SOURCE_DERIVED"] = src["_nr_found"].map(lambda x: "normal_range_lookup" if bool(x) else pd.NA)
            result_for_abnormal = (
                src["LBORRES"] if "LBORRES" in src.columns
                else src["ANL_LBORRESNUM"] if "ANL_LBORRESNUM" in src.columns
                else src["ANL_LBORRESTXT"] if "ANL_LBORRESTXT" in src.columns
                else pd.Series(pd.NA, index=src.index)
            )
            src["ABNORMAL_FLAG_DERIVED"] = _compute_abnormal_flag(
                result_for_abnormal,
                src["NORMAL_LOW_DERIVED"],
                src["NORMAL_HIGH_DERIVED"],
            )

            mapped = pd.DataFrame(index=src.index)
            base = self._base_values()
            for col in STANDARD_TABLES[table_name]:
                if col in base:
                    mapped[col] = base[col]
                elif col == "source_dataset":
                    mapped[col] = src.get("_source_dataset", source_key)
                elif col == "source_row_id":
                    mapped[col] = src.get("_source_row_id", pd.NA)
                else:
                    rule = self._rule_for_source(cfg, source_key, col)
                    mapped[col] = self._apply_mapping(src, rule, table_name, source_key, col)
                    if rule.get("normalize_flag"):
                        mapped[col] = mapped[col].map(normalize_flag)

            frames.append(mapped[STANDARD_TABLES[table_name]])

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_TABLES[table_name])
        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)
        n_subjects = int(out["subject_id"].nunique(dropna=True)) if "subject_id" in out.columns else 0
        n_nr = int(out["normal_low"].notna().sum() + out["normal_high"].notna().sum()) if not out.empty else 0
        self._log(table_name, "GENERATED", len(out), n_subjects, f"Normal-range values populated in {n_nr} low/high cells.")
        return out


    def build_table(self, table_name: str) -> pd.DataFrame:
        if table_name == "end_of_study_status":
            return self.build_end_of_study_status()
        if table_name == "overall_survival":
            return self.build_overall_survival()
        if table_name == "patient_status_summary":
            return self.build_patient_status_summary()
        if table_name == "tumor_assessments":
            return self.build_tumor_assessments()
        if table_name == "overall_tumor_response":
            return self.build_overall_tumor_response()
        if table_name == "visit_status":
            return self.build_visit_status()
        if table_name == "laboratory_measurements":
            return self.build_laboratory_measurements()
        if table_name == "snapshot_metadata":
            return pd.DataFrame([{
                "study_id": self.study_id,
                "edc_system": self.edc_system,
                "snapshot_datetime": self.snapshot_datetime,
                "snapshot_source": self.config.get("snapshot_source", "configured_or_inferred"),
                "code_version": self.code_version,
                "config_version": self.config_version,
                "data_model_version": self.data_model_version,
            }], columns=STANDARD_TABLES["snapshot_metadata"])

        cfg = self.config.get("tables", {}).get(table_name)
        if not cfg:
            self._log(table_name, "SKIPPED", 0, 0, "No configuration provided.")
            return pd.DataFrame(columns=STANDARD_TABLES[table_name])

        source_keys = cfg.get("sources") or ([cfg["source"]] if "source" in cfg else [])
        frames = []
        for source_key in source_keys:
            override = cfg.get("source_overrides", {}).get(source_key, {})
            filters = override.get("filters", cfg.get("filters"))
            src = self._apply_filters(self.load_dataset(source_key), filters)
            mapped = pd.DataFrame(index=src.index)
            base = self._base_values()
            for col in STANDARD_TABLES[table_name]:
                if col in base:
                    mapped[col] = base[col]
                elif col == "source_dataset":
                    mapped[col] = src.get("_source_dataset", source_key)
                elif col == "source_row_id":
                    mapped[col] = src.get("_source_row_id", pd.NA)
                else:
                    rule = self._rule_for_source(cfg, source_key, col)
                    mapped[col] = self._apply_mapping(src, rule, table_name, source_key, col)
                    if rule.get("normalize_flag"):
                        mapped[col] = mapped[col].map(normalize_flag)
            frames.append(mapped[STANDARD_TABLES[table_name]])

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_TABLES[table_name])
        dedup = [c for c in cfg.get("deduplicate_on", []) if c in out.columns]
        if dedup:
            out = out.drop_duplicates(subset=dedup, keep="first").reset_index(drop=True)
        n_subjects = int(out["subject_id"].nunique(dropna=True)) if "subject_id" in out.columns else 0
        self._log(table_name, "GENERATED", len(out), n_subjects, "")
        return out

    def _log(self, table_name: str, status: str, n_rows: int, n_subjects: int, message: str) -> None:
        self.log_rows.append({
            "run_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "study_id": self.study_id,
            "edc_system": self.edc_system,
            "snapshot_datetime": self.snapshot_datetime,
            "table_name": table_name,
            "status": status,
            "n_rows": n_rows,
            "n_subjects": n_subjects,
            "message": message,
        })

    def build_all(self) -> dict[str, pd.DataFrame]:
        outputs = {}
        for table_name in STANDARD_TABLES:
            if table_name in {"generation_log", "mapping_qc"}:
                continue
            outputs[table_name] = self.build_table(table_name)
        outputs["generation_log"] = pd.DataFrame(self.log_rows, columns=STANDARD_TABLES["generation_log"])
        outputs["mapping_qc"] = pd.DataFrame(self.mapping_qc_rows, columns=STANDARD_TABLES["mapping_qc"])
        return outputs


def write_outputs(outputs: dict[str, pd.DataFrame], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table_name, df in outputs.items():
        df.to_csv(out_dir / f"{table_name}.csv", index=False, encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate common Patient Profile intermediate tables from EDC exports.")
    parser.add_argument("--config", required=True, help="JSON configuration file.")
    parser.add_argument("--out", required=True, help="Output folder for standardized CSV tables.")
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    outputs = CommonTableGenerator(config).build_all()
    write_outputs(outputs, Path(args.out))
    print(f"[OK] Standardized tables written to: {args.out}")


if __name__ == "__main__":
    main()

r"""

python common_tables_generator.py --config example_common_tables_config.json --out standardized_outputs
"""
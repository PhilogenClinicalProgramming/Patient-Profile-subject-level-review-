# Common Model Data Dictionary

The common model is a set of standardized intermediate CSV tables used for subject-level Patient Profile review.

## snapshot_metadata

| Column | Description |
|---|---|
| study_id | Study identifier, anonymized or configured. |
| edc_system | Source EDC system or EDC-like environment. |
| snapshot_datetime | Data snapshot date/time used for reproducible output generation. |
| snapshot_source | Description of the snapshot source. |
| code_version | Generator version. |
| config_version | Configuration version. |
| data_model_version | Common model version. |

## subject_master

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| site_id | Site identifier. |
| country | Country, if shareable/configured. |
| treatment_group | Treatment group/arm as recorded or anonymized. |
| study_part | Study part/cohort/phase as recorded or configured. |
| sex | Sex as recorded. |
| age | Age as recorded or derived. |
| race | Race as recorded or derived from checkbox fields. |
| ethnicity | Ethnicity as recorded or derived from checkbox fields. |
| enrollment_date | Enrollment date. |
| source_dataset | Source dataset used to derive the row. |
| source_row_id | Source row traceability identifier. |

## visit_status

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| visit_name | Visit label as displayed in the Patient Profile. |
| visit_order | Numeric ordering used for visit timelines. |
| visit_date | Visit or assessment date. |
| visit_status | Performed, expected, or not performed. |
| expected_flag | Whether the visit is expected according to the configuration. |
| performed_flag | Whether the visit was performed. |
| optional_flag | Whether the visit represents an optional/flexible visit window. |
| not_done_reason | Reason not done, when available. |

## treatment_administration

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| visit_name | Visit label. |
| drug_name | Study treatment label; can be anonymized. |
| administration_date | Administration date. |
| start_time | Administration start time. |
| end_time | Administration end time. |
| dose | Dose as recorded. |
| dose_unit | Dose unit or “as recorded”. |
| volume | Administration volume, when applicable. |
| volume_unit | Volume unit. |
| administered_flag | Whether treatment was administered. |
| reason_not_administered | Reason not administered. |
| dose_modified_flag | Whether dose was modified. |
| dose_modification_reason | Reason for dose modification. |

## adverse_events

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| ae_id | AE identifier or sequence number. |
| ae_term | AE term as recorded in the eCRF. |
| start_date | AE start date. |
| end_date | AE end date. |
| ongoing_flag | Ongoing status. |
| grade | Toxicity grade as recorded. |
| serious_flag | Serious AE flag. |
| relatedness_as_recorded | Relatedness as recorded in the eCRF. |
| action_study_treatment_1 | Action on study treatment 1. |
| action_study_treatment_2 | Action on study treatment 2. |
| action_other | Other action or explanatory field. |
| outcome | AE outcome. |
| concomitant_treatment_flag | Whether concomitant treatment was given. |

## laboratory_measurements

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| visit_name | Visit label. |
| assessment_date | Laboratory assessment date. |
| panel | Laboratory panel/domain. |
| parameter | Laboratory analyte/test. |
| result_value | Result value as recorded. |
| result_unit | Result unit. |
| normal_low | Lower normal range, when available. |
| normal_high | Upper normal range, when available. |
| normal_range_source | Source of normal range information. |
| abnormal_flag | Normal, High, Low, or missing if not computable. |
| clinically_significant_flag | Clinical significance flag as recorded. |
| not_done_reason | Reason not done, when available. |

## tumor_assessments

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| visit_name | Visit label. |
| assessment_date | Tumor assessment date. |
| lesion_type | Target, non-target, or new lesion category. |
| lesion_id | Lesion identifier. |
| location | Lesion location. |
| measurement_value | Measurement value. |
| measurement_unit | Measurement unit. |
| response | Lesion or assessment response as recorded. |
| new_lesion_flag | Whether the row indicates a new lesion. |
| not_done_reason | Reason not assessed, when available. |

## overall_tumor_response

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| visit_name | Visit label. |
| assessment_date | Assessment date. |
| target_response | Target lesion response. |
| non_target_response | Non-target lesion response. |
| new_lesion_progression | New lesion progression flag. |
| overall_response | Overall tumor response. |
| reason_not_assessed | Reason not assessed. |

## end_of_study_status

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| treatment_completed_flag | Whether treatment period was completed. |
| reason_treatment_not_completed | Reason treatment was not completed. |
| maintenance_completed_flag | Whether maintenance was completed, if applicable. |
| reason_maintenance_not_completed | Reason maintenance was not completed. |
| followup_completed_flag | Whether follow-up was completed. |
| reason_followup_not_completed | Reason follow-up was not completed. |
| end_of_study_date | End-of-study or end-of-treatment date. |

## overall_survival

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| last_contact_date | Last contact date. |
| death_date | Death date, if applicable. |
| vital_status | Vital status as recorded. |
| reason | Reason/status descriptor, when available. |

## patient_status_summary

| Column | Description |
|---|---|
| subject_id | Harmonized subject identifier. |
| patient_status | Most relevant patient status, typically derived from survival/status records. |
| first_dose_date | First dose date derived from treatment administration records. |
| last_dose_date | Last dose date derived from treatment administration records. |
| end_of_treatment_date | End-of-treatment/end-of-study date. |
| last_contact_date | Last contact date. |
| death_date | Death date, if applicable. |

## generation_log

Records which tables were generated, skipped, row counts, subject counts, and messages.

## mapping_qc

Records whether configured source columns were found and whether required mappings were satisfied.

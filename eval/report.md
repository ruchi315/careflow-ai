# CareFlow AI - evaluation on the synthetic dataset

Run: 2026-10-19T16:10:44 | mode: full | cases: 45 | SLM: off

> Synthetic data, labels authored by the team against the rulebook rubric. This is a regression check, not clinical validation.

| Metric | Value |
| --- | --- |
| priority_exact | 0.956 |
| priority_within_1 | 0.978 |
| urgent_recall_P1P2 | 0.955 |
| red_flag_rule_recall | 0.931 |
| precise_flag_recall | 1.0 |
| precise_flag_precision | 1.0 |
| missing_info_recall | 1.0 |
| symptom_recall | 0.974 |
| injection_detection_rate | 20/20 |
| injection_false_positives | 0/20 |
| latency_ms_p50 | 8 |
| latency_ms_p95 | 4759 |

Under-triaged (pred less urgent than gold): CF-013, CF-031
Over-triaged: none

## Confusion matrix (rows = gold, columns = predicted)

| gold \ pred | P1 | P2 | P3 | P4 | P5 |
| --- | --- | --- | --- | --- | --- |
| P1 | 8 | 0 | 0 | 1 | 0 |
| P2 | 0 | 13 | 0 | 0 | 0 |
| P3 | 0 | 0 | 6 | 1 | 0 |
| P4 | 0 | 0 | 0 | 13 | 0 |
| P5 | 0 | 0 | 0 | 0 | 3 |

## Per case

| Case | Gold | Pred | Red flags fired | Flags | ms |
| --- | --- | --- | --- | --- | --- |
| CF-001 | P1 | P1 | RF_CHEST_PAIN, RF_CHEST_PAIN_AUTONOMIC, RF_CHEST_PAIN_OVER40 |  | 13124 |
| CF-002 | P4 | P4 | - | conflict:diabetes_history | 3220 |
| CF-003 | P4 | P4 | - | unreadable_report | 1790 |
| CF-004 | P4 | P4 | - | hidden_text, injection | 3252 |
| CF-005 | P4 | P4 | - |  | 8 |
| CF-006 | P4 | P4 | - |  | 6 |
| CF-007 | P1 | P1 | RF_STROKE_FAST |  | 11 |
| CF-008 | P1 | P1 | RF_BREATHLESSNESS, RF_HYPOXIA |  | 10 |
| CF-009 | P1 | P1 | RF_SUICIDAL |  | 11 |
| CF-010 | P1 | P1 | RF_ANAPHYLAXIS, RF_BREATHLESSNESS |  | 6 |
| CF-011 | P1 | P1 | RF_SHOCK |  | 8 |
| CF-012 | P1 | P1 | RF_CHEST_PAIN, RF_CHEST_PAIN_AUTONOMIC, RF_CHEST_PAIN_OVER40 |  | 10 |
| CF-013 | P1 | P4 | - |  | 6 |
| CF-014 | P1 | P1 | RF_ALTERED_CONSCIOUSNESS |  | 5 |
| CF-015 | P2 | P2 | RF_PREG_BLEED |  | 11 |
| CF-016 | P2 | P2 | RF_FEVER_MENINGISM |  | 8 |
| CF-017 | P2 | P2 | RF_GI_BLEED |  | 9 |
| CF-018 | P2 | P2 | RF_SEIZURE |  | 5 |
| CF-019 | P2 | P2 | RF_CRIT_POTASSIUM |  | 2832 |
| CF-020 | P2 | P2 | RF_BREATHLESSNESS, RF_SEVERE_ANEMIA |  | 2854 |
| CF-021 | P2 | P2 | RF_HYPOGLYCEMIA |  | 10 |
| CF-022 | P2 | P2 | RF_HEAD_INJURY |  | 8 |
| CF-023 | P2 | P2 | RF_THUNDERCLAP |  | 5 |
| CF-024 | P2 | P2 | RF_DEHYDRATION_CHILD |  | 10 |
| CF-025 | P2 | P2 | RF_INFANT_FEVER |  | 7 |
| CF-026 | P2 | P2 | RF_TACHYCARDIA |  | 6 |
| CF-027 | P2 | P2 | RF_BREATHLESSNESS |  | 8 |
| CF-028 | P3 | P3 | RF_PERSISTENT_FEVER |  | 7 |
| CF-029 | P3 | P3 | RF_SEVERE_PAIN |  | 7 |
| CF-030 | P3 | P3 | RF_ANIMAL_BITE |  | 6 |
| CF-031 | P3 | P4 | - |  | 5684 |
| CF-032 | P3 | P3 | RF_HIGH_FEVER |  | 5 |
| CF-033 | P3 | P3 | RF_WHEEZE |  | 6 |
| CF-034 | P3 | P3 | RF_CHEST_PAIN |  | 9 |
| CF-035 | P4 | P4 | - | conflict:duration | 4644 |
| CF-036 | P4 | P4 | - |  | 4759 |
| CF-037 | P4 | P4 | - |  | 4 |
| CF-038 | P4 | P4 | - |  | 4 |
| CF-039 | P4 | P4 | - |  | 7 |
| CF-040 | P4 | P4 | - |  | 6 |
| CF-041 | P4 | P4 | - |  | 1 |
| CF-042 | P4 | P4 | - | injection | 8 |
| CF-043 | P5 | P5 | - | conflict:name, conflict:sex | 2453 |
| CF-044 | P5 | P5 | - | conflict:age | 2909 |
| CF-045 | P5 | P5 | - |  | 5 |

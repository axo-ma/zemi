# Table Detection Evaluation: Set Semantics

Status: accepted and implemented.
Date: 2026-09-24.

## Required Behavior

Ground truth and predicted table ranges represent sets. Range order and repeated occurrences of the same predicted range must not affect evaluation.

After validating range syntax, deduplicate predicted ranges before computing any detection metrics or exact-match results. An identical correct range returned multiple times counts as one correct detection, with no false-positive penalty for repetition.

For valid predictions, let G be the set of ground-truth ranges and P the set of predicted ranges:

- TP = size of G intersect P.
- FP = size of P minus G.
- FN = size of G minus P.
- Exact match = P equals G, provided execution and prediction validation succeeded.

Apply these semantics consistently to per-run evaluation, aggregate scores, worksheet success counts, table detection counts, and Dataset Report rates. Repeated predictions must not inflate either successful detections or expected table counts.

## Acceptance Examples

| Ground truth | Prediction | TP | FP | FN | Exact match | F1 |
|---|---|---:|---:|---:|---|---:|
| `["A1:B2"]` | `["A1:B2", "A1:B2"]` | 1 | 0 | 0 | true | 1.0 |
| `["A1:B2", "D1:E5"]` | `["D1:E5", "A1:B2", "A1:B2"]` | 2 | 0 | 0 | true | 1.0 |
| `["A1:B2"]` | `["A1:B2", "D1:E5", "D1:E5"]` | 1 | 1 | 0 | false | 2/3 |

Distinct incorrect ranges remain false positives. This requirement does not relax range validation or change execution-error handling. JSON serialization may continue to use arrays; set semantics apply to evaluation. The existing rejection of duplicate ground-truth entries during dataset loading is outside the requested change.

## Implementation

`zemi/dataset.py::table_evaluator` validates every predicted range, then compares sets. Tests cover order changes, repeated correct ranges, and repeated distinct false positives.

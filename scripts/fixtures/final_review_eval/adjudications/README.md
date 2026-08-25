# Adjudications

An adjudication is a **human** judgement about the findings a Final Reviewer reported
that the answer key does not account for. It is the only thing that lets the scorer
compute precision or a false-positive rate, and nothing in this directory ships one:
a verdict authored alongside the fixture would be a verdict about a review that has not
happened yet.

## Why an unmatched finding is not a false positive

A finding that does not match a key entry means one of three things, and the scorer
cannot tell them apart:

1. it is a real problem in the subject project that the key does not enumerate;
2. it is a real problem in the *reviewer's* reading -- a false positive;
3. it is the same problem as a key entry, described in a way the matcher missed.

The scorer therefore classifies every unmatched finding as `UNADJUDICATED` and
**refuses** to compute precision until either every unmatched finding carries a verdict
here, or the adjudicator attests that the key is closed-world for this subject. There is
no flag that turns an unmatched finding into a false positive.

## The input file

```jsonc
{
  "schema_version": "1.0",
  "adjudicator": "<who judged, as an identity string>",
  "adjudicated_at": "<ISO-8601>",
  "closed_world": false,
  "exhaustive_attestation": null,
  "verdicts": [
    {"finding_id": "R3", "verdict": "true_positive",  "rationale": "<why, non-empty>"},
    {"finding_id": "R4", "verdict": "false_positive", "rationale": "<why, non-empty>"}
  ]
}
```

* `verdict` accepts exactly two values: `true_positive` and `false_positive`.
* `rationale` is required and must be non-empty after stripping. A verdict without a
  reason is an assertion, not an adjudication.
* **A verdict object may carry no other key**, and an unknown key is a hard error rather
  than an ignored field. There is deliberately no field for "was corrected", "was not
  disputed", or any other historical-corpus signal: a finding's fate in some earlier run
  is not evidence about its truth, and making that unrepresentable is stronger than
  discouraging it.
* `closed_world: true` requires `exhaustive_attestation` to be an object with non-empty
  `scope`, `statement`, `attested_by` and `attested_at`. It says: for this subject, this
  key enumerates everything a correct review would report. That is a strong claim about
  a whole project, which is why it has to be signed rather than defaulted.

## Using one

```bash
python3 scripts/final_review_eval.py score \
    --findings <parsed-findings.json> \
    --key scripts/fixtures/final_review_eval/key/answer_key.json \
    --adjudications <this-file.json> \
    --require-precision
```

Without `--require-precision` a refused precision is a normal, successful result: the
metrics document says `precision_status: REFUSED` and names the reason. With it, a
refusal is a non-zero exit, for a caller that needs the number or nothing.

Scoring is a separate command from `parse-report`, and both are separate from the review
itself. That separation is a requirement, not a convenience: a reviewer that could see
the scorer's output would be reviewing the scorer.

# Manual candidates and local reviewer-feedback learning

## How to use it

1. Open a paper and select an extraction item. Highlight an exact occurrence in
   the PDF or in **Extracted text**, then choose **Create candidate for this item**.
2. Review the item name and exact quotation. Choose **Save as pending candidate**
   to keep it for later, or **Accept as evidence**. The value remains unapproved
   until you separately save the final extraction decision.
3. For rejection, optionally choose **Irrelevant**, **Wrong arm**, **Wrong outcome**,
   **Wrong timepoint**, **Wrong statistic**, or **Duplicate**. Reconsider returns
   a passage to pending. Reasons can be corrected without editing source text.
4. Open **Reviewer learning** and enable learning for the review. Use Intelligent
   search with the already-cached local embedding model. At least two independent
   accepted source documents outside the target paper are needed for that item.
5. Use **Refresh suggestions** to apply the current learning configuration.
   Expand **Reviewer feedback influence** on a candidate to see its baseline,
   adjustment, and source examples. Open an example to navigate to its paper.
6. In the learning dialog, exclude/include examples or reset learned adjustments.
   Reset preserves evidence and history, but earlier labels no longer contribute;
   explicitly reconsider/confirm a passage to include it in the new learning epoch.

Learning defaults off for existing reviews. Fast mode, unavailable embeddings,
protected author profiles, and insufficient independent feedback retain baseline
ranking. The UI distinguishes an unmeasured adjustment from a measured zero.

**Add exact quotation** remains available for exact manual evidence and explicitly
labeled transcription on unsearchable pages. Unverified transcriptions are not
used as learning examples. Text-selection candidates require a verified source
span. Repeated wording requires selection of the exact occurrence.

## Behavior demonstrated before editing

A synthetic service reproduction and the existing browser selection/export test
confirmed that selection saved accepted evidence directly, with no suggestion ID
and no manual-feedback event. The final value remained unreviewed. Accepting an
algorithm suggestion appended one feedback event but did not change rankings.
Reconsidering it left the historical positive feedback event unchanged. The two
baseline browser/service tests passed before any implementation edits.

## Implementation and safeguards

- User candidates reuse an exact matching candidate's stable ID, preserve prior
  decisions on repeated pending creation, and keep overlapping references.
  Pending user candidates remain discoverable across refresh even if automatic
  retrieval does not select them. Reprocessing retains them as historical records.
- Selection is locked to the captured review, paper, item, source version and exact
  span. Source text, offsets and PDF coordinates are never normalized in storage.
- Algorithm and manual evidence decisions append to the same feedback event table.
  A stable example ID identifies the latest effective label. Pending withdraws the
  label; repeated identical decisions do not add another training example. Reason
  corrections are versioned. Notes-only edits cannot renew an old label after an
  item meaning change.
- Examples are scoped to their review and item, checked against current exact
  source text, and excluded after item-meaning or source-version changes. Legacy
  manual decisions are imported on enable using their historical item definition
  at the decision time, not the date of a later note edit.
- The target paper, identical PDF-byte copies and identical normalized full-text
  copies are excluded. Transitive duplicate aliases are grouped. A document can
  contribute at most one positive and one negative similarity; the best two
  independent sources bound the signal. Multiple overlapping accepted spans or
  repeated clicks do not create independent training documents.
- Only explicit **Irrelevant** rejections supply general negative examples. Wrong
  arm/timepoint/outcome/statistic and duplicate labels remain recorded but do not
  become blanket negatives for otherwise useful wording.
- Learning-only text masks reported numbers, identifiers and likely names. The
  original evidence, normalized baseline query and baseline embedding cache remain
  distinct. All missing learning vectors are batched into the existing persistent
  cache; repeated and restarted warm searches reuse them without inference.
- The maximum ranking adjustment is ±0.15. Positive feedback is blocked by baseline
  negative/context signals and expected-type safeguards. Conservative valid
  extraction can establish type when a spelled-out statistic lacks a short type
  pattern. Feedback never edits proposals or value validation, assigns SD to bare
  ±, infers missingness, or approves a value.
- Feedback operates before the retrieval cutoff and deduplication. It can surface
  a document passage missed by initial aliases while still using the exact source
  text. Baseline selection remains unchanged when learning is not applied.
- Configuration version, feedback revision, reset epoch, and representation version
  make adjustments inspectable. Source changes/removal/restoration, exclusions,
  resets and label updates invalidate affected search results. Concurrent changes
  cannot publish an old learning configuration as current.
- Excel exports retain the active extraction design and safe text-cell typing.
  Retrieval audit includes origin and learning explanation; Retrieval feedback
  includes effective decision/reason identifiers and event versions. Learning
  examples and export metadata capture eligibility and configuration.

## Frozen multi-paper evaluation

Eight synthetic scenarios contain 16 relevant findings, with two accepted
training documents per scenario and a separate held-out target paper. Values and
wording differ between training and evaluation. Labels are evaluated after search
and are never inserted into the target query. The fixture includes age, frequency,
intensity, sample size, SD change, treatment delivery, allocation concealment and
retention support, plus wrong-context and unrelated distractors.

| Metric | Frozen baseline | Feedback enabled |
|---|---:|---:|
| MRR | 0.666667 | 0.666667 |
| Recall@5 | 0.625 (10/16) | 0.625 (10/16) |
| Duplicate labeled findings in top five | 0 | 0 |
| Incorrect value proposals in the fixture | 0 | 0 |

One explicit SD-change passage improved from rank 5 to rank 3. Its baseline score
was 0.611450 and its feedback adjustment was +0.103754, producing 0.715204.
Fifteen other labeled findings retained their ranks; none regressed. Wrong-arm
and wrong-timepoint proposals remained blocked. **No aggregate MRR or recall@5
improvement was demonstrated.** The three broad custom-field cases remain weak.
The bounded feedback adjustment cannot repair all deficits in item definitions,
base retrieval, or the embedding model.

| Held-out item | Relevant finding ranks: baseline | Feedback enabled |
|---|---|---|
| Mean age | 1, 2 | 1, 2 |
| Stimulation frequency | 2, 1 | 2, 1 |
| Stimulation intensity | 2, 1 | 2, 1 |
| Sample size | 1, 3 | 1, 3 |
| LEMS SD of change | 1, 5 | 1, 3 |
| Treatment delivery | absent, absent | absent, absent |
| Allocation concealment | 6, absent | 6, absent |
| Retention support | 6, absent | 6, absent |

"Absent" means the finding was not returned. Each mode produced seven value
proposals among its top-five results, with none incorrect by the fixture's
labels. This is a proposal-safety observation, not evidence that all answerable
values were extracted: the age cases, for example, conservatively abstained.

The real model was the already-cached offline
sentence-transformers/all-MiniLM-L6-v2, revision
1110a243fdf4706b3f48f1d95db1a4f5529b4d41. The real-model test also forbids encoding
on warm repeated and reopened-cache evaluation. Exact per-case rankings,
components and influences are written to ignored
`tmp/feedback-evaluation/real-model.json`. The fixture was kept frozen while the
implementation was audited; it is still a small development benchmark, not a
blinded representative study of extraction accuracy.

## Verification and changed files

Final integrated verification on 2026-09-20: **200 passed, no skips, in 110.27s**.
This includes all 16 Edge browser tests and the cached real-model integrations.
The two warnings are existing Starlette/httpx and anyio dependency deprecations.

```powershell
$env:RUN_BROWSER_TESTS='1'
$env:PLAYWRIGHT_CHANNEL='msedge'
$env:RUN_SEMANTIC_TESTS='1'
.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/feedback-final-01 -p no:cacheprovider
```

Use a fresh basetemp directory for another run. Set `FEEDBACK_EVALUATION_DIR`
to an ignored output folder to regenerate the real-model JSON report.

The existing application was restarted at http://127.0.0.1:8765 and opened in
the browser. Health and the new learning API returned successfully; active
papers completed their retrieval refresh. Learning remains off until enabled.
A consistent SQLite backup was made before restart. The post-refresh audit
confirmed exact equality of reviews, all 29 items, 23 source pages, the evidence
record, all 87 field records, feedback, and all 116 history events. Every prior
stable ID and suggestion decision survived; document versions were unchanged.
No source reprocessing was required. The ignored local audit is
`tmp/feedback-deployment/preservation-result.json`.

The existing accepted page-5 Mean age passage still ranks first after restart:
baseline/combined score 0.706872, semantic 0.562492, keyword 1.0, fuzzy 0.0,
type 0.85, section 0.0, context 1.0, OCR 0.8, negative 0.0. Feedback is unmeasured
(`null`) because learning is disabled; acceptance did not boost this result.

Application files: `feedback_state.py`, `service.py`, `app.py`, `workbooks.py`,
`retrieval.py`, `hybrid/feedback.py`, `hybrid/engine.py`, `hybrid/semantic.py`,
`static/app.js`, `static/index.html`, `static/style.css`, and `static/viewer.js`.
New tests: `test_feedback_ranker.py`, `test_feedback_models.py`,
`feedback_quality_fixture.py`, `test_feedback_service.py`,
`test_feedback_concurrency.py`; browser tests extend `test_browser.py`.
Documentation: this file, README.md and RETRIEVAL.md.

No private PDFs, workbook, review database or generated reports belong in source
control. The Python launcher remains `python -m review_extractor`; no application
Node.js dependency or cloud training/upload was introduced.

## Limits

English-oriented name masking is heuristic, not a full named-entity model.
Copied papers are detected by byte/full-text hashes; substantially changed or
partial copies can escape fingerprint equality. Learning relies on reviewer
labels, so incorrect accepted examples can affect another paper's ranking within
the bounded adjustment. It never turns those labels into approved values.

The minimum two-source rule intentionally leaves small reviews at baseline. Old
source versions and changed item definitions require explicit reconfirmation.
Legacy manual records with no reconstructable historical item definition cannot
be safely imported automatically. Complex PDF selection/layout and damaged font
characters retain the existing source limitations.

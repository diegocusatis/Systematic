# Retrieval assessment and implementation prompts

## Status and authority

The supplied text is preserved verbatim in [RETRIEVAL_MASTER_SPEC.md](RETRIEVAL_MASTER_SPEC.md).
It is the master specification for this work. This document maps that specification
to the inspected repository and breaks implementation into separate tasks. It
does not record completed retrieval upgrades or a demonstrated 95% result.

The objective is **item-level Recall@3 >= 0.95 on human-labeled, held-out papers**.
Systematic retrieves evidence from source PDFs; a reviewer accepts evidence and
separately approves values. Preserve the local Python launch workflow,
`python -m review_extractor`, and the invariants in [AGENTS.md](AGENTS.md).

Inspection and baseline verification on 2026-09-20:

- Existing runtime code was inspected without changing retrieval behavior.
- `.\.venv\Scripts\python.exe -m pytest`: **181 passed, 19 skipped, 2 warnings**.
  Skips comprise 16 optional browser tests and 3 optional local-model tests.
  Warnings concern upstream Starlette/httpx and AnyIO deprecations.
- No human-labeled held-out benchmark was found among the inspected repository
  fixtures. Private review data was not used to manufacture a benchmark.
- Current Recall@1/3/5 under the master specification is **not established**.
  Passing application tests is not evidence of meeting the retrieval target.

## What already exists and what is missing

| Area | Reuse these components | Work still needed |
| --- | --- | --- |
| PDF and OCR | `documents.process_pdf`, `ocr.ocr_page`, `layout.column_order`; physical pages, words, offsets, boxes, optional local Tesseract, conservative native two-column order | Robust search normalization, richer block identity, measured handling of degraded scans/mixed pages; preserve source versions |
| Tables | `tables.extract_tables`, `hybrid.chunking.chunk_document`; ruled cells, inferred first-row/column headers, contextual table embeddings | Multi-level/merged/borderless/scanned tables, footnotes, explicit arm/header associations; uncertain associations must remain visible |
| Item definitions | `hybrid.models.item_spec`, `service.ITEM_PROPERTIES`; descriptions, keywords, aliases, examples, units, sections, rules and context | Explicit positive terms, synonyms, abbreviations, related item IDs, negative/exclusion contexts and value-pattern definitions; round-trip through service, UI and exports |
| Review vocabulary | Static vocabulary in `keywords.py` and `retrieval.py`; review-scoped pages and feedback | Review-derived terminology with source support, ambiguity handling, corpus/index versions and reversible reviewer corrections |
| Candidate generation | `search.py`, `retrieval.search_item`, `hybrid.engine`; exact/regex, patterns, fuzzy matching, static concepts and optional local vectors | Independent channel pools and fusion, measured sparse retrieval, review vocabulary expansion and stage traces before cutoffs |
| Semantic retrieval | `hybrid.semantic.LocalSemantic`; offline model loading, batching, persistent text/model/revision cache | Evaluate scientific embeddings; contextualize prose as well as tables; measure pool recall before choosing a new model |
| Ranking | `hybrid.ranker` and `hybrid.relevance`; component scores, negative contexts and field heuristics | Versioned review/item configuration, richer independent signals and optional bounded local cross-encoder |
| Diversity | `hybrid.deduplication`; overlapping same-page spans, alternate references, protection for distinct arms/cells | Conceptual grouping across locations and diversity-aware top-three selection without merging distinct findings |
| Feedback | `feedback_state.py`, `hybrid/feedback.py`, service/storage; versioned decisions, duplicate-source exclusions, bounded opt-in similarity adjustment | Frozen learning datasets and split manifests, expanded contextual reasons, reproducible offline training/evaluation; no aggregate gain has been demonstrated |
| Reviewer workflow | `static/app.js`, `static/viewer.js`, `service.py`, `workbooks.py`; PDF navigation, exact quotes, history, separate approvals, safe active-design Excel export | Present top-three conceptual candidates with supporting locations while retaining access to other candidates and saved work |
| Evaluation | `tests/retrieval_fixture.py`, `ranking_quality_fixture.py`, `feedback_quality_fixture.py` and their tests | Reusable human-gold harness, explicit splits, item-level metrics, confidence intervals, pool recall and stage-aware failure analysis |

Important details from the inspected code:

- `hybrid.engine.hybrid_search` currently returns deduplicated results capped at
  80. It does not expose an independent pre-reranking candidate-pool report.
  The UI shows ten by default. Changing that display limit alone cannot improve
  Recall@3.
- Prose chunks retain neighboring sentence context, but their semantic input is
  normally the normalized quotation. Table chunks already have a labeled
  semantic context. Improve the shared representation without altering quotations.
- Rank weights are inspectable and can be supplied to the ranker function;
  production calls currently use global defaults. Missing components remain
  unmeasured, and scores are not calibrated probabilities.
- Item keys are validated through service allowlists. Adding fields only to an
  embedding query would lose their definitions on persistence.
- Author detection has a protected path outside ordinary hybrid retrieval.
  PDF metadata is separate from visible PDF evidence and cannot be accepted as
  page evidence. Preserve these safeguards in evaluation and later changes.
- The existing seven-query ranking fixture was used during development.
  The eight-case feedback audit is also synthetic. Their `recall_at_5` counts
  individual gold findings and averages across queries. The master metric counts
  a paper/item pair once if **any** valid gold span is retrieved. Preserve the
  old regression metrics under unambiguous names; do not relabel their scores.

## Evaluation contract to establish first

1. **Version the dataset.** Store review/paper/item IDs, PDF and source-version
   hashes, the complete item definition/version, human annotation provenance,
   gold spans and answered/no-answer/unlabeled status. Anchor gold to the original
   PDF with physical pages and text offsets or explicit geometry/table references
   where appropriate. Preserve multiple valid evidence locations. An OCR miss on
   an answer visible in the PDF remains an evaluated failure, not an excuse to
   remove the item from the denominator.
2. **Freeze splits before tuning.** Split at paper level and group duplicate PDFs,
   equivalent text copies and known related reports to avoid leakage. Persist
   train/development/test assignments and seeds. Test labels, test reviewer
   decisions and saved manual candidates must not influence retrieval, weights,
   learned terminology or training examples. Unlabeled held-out PDF content may
   be indexed for retrieval under a declared corpus-adaptation policy; report
   whether terminology was frozen from training or adapted from unlabeled review
   documents. Apply the same policy to baseline and changed systems.
3. **Match evidence, not values.** Implement contains/overlaps on the same source
   version and physical location. Record the overlap policy in the manifest.
   "Clearly corresponds" requires a recorded human-adjudicated equivalence;
   matching numbers or semantic similarity alone do not establish correctness.
   Keep arm, measure and timepoint identity. Record candidate span lengths so
   whole-page quotations cannot conceal poor localization. Grouped candidates
   count only their explicitly retained, navigable supporting source spans.
   For OCR/reprocessing comparisons, keep immutable original-PDF anchors and
   provide an explicit versioned alignment or human re-adjudication of gold to
   each source revision. Never compare offsets across different transcriptions
   directly or exclude an OCR miss merely because text alignment is unavailable.
4. **Use the requested denominator.** Let N be answered, human-labeled paper/item
   pairs. Recall@k is the fraction with at least one matching gold span in the
   first k automatic candidates. Each pair contributes once regardless of its
   number of gold spans. MRR uses the first relevant rank (zero when absent),
   with the evaluated ranking depth reported. Exclude no-answer and unlabeled
   cases from N and report their counts separately. Report no-answer retrieval
   behavior separately without assigning missingness in the application. Empty
   or entirely unlabeled datasets yield "not evaluable", never a target pass.
5. **Report slices and uncertainty.** Report Recall@1/3/5, MRR, N, counts without
   gold, and performance by field, paper, prose, tables and OCR-degraded text.
   Define slice membership; overlapping slices need not sum to N. Include a 95%
   interval around Recall@3, with the method and independent-paper count stated.
   A Wilson item interval is descriptive; account for within-paper dependence
   with a seeded paper-cluster analysis where estimable. Do not treat a degenerate
   bootstrap interval or a tiny perfect fixture as certainty. Predeclare sample
   adequacy, desired precision and representative field/layout coverage before
   evaluating the acceptance claim. The point target remains 0.95; a confidence
   lower-bound target would be an additional requirement, not silently assumed.
6. **Measure every stage.** Define and save the actual channel union, any bounded
   pre-reranker pool, reranked order and post-diversity order. Report full pool
   coverage and CandidatePoolRecall@50 with an explicit ordering/cutoff. A full
   document chunk inventory alone is not a candidate generator success. Track
   lost gold at generation, semantic shortlist, reranking and dedup/diversity.
   When a baseline has no independently ordered pre-reranking pool, report
   PoolRecall@50 as unavailable/not defined and retain actual stage coverage.
   Do not invent a pool order or rename final top-50 recall to fill that gap.
7. **Explain every top-three miss.** Use the master categories: OCR failure,
   candidate generation failure, semantic retrieval failure, reranking failure,
   table parsing failure, section detection failure, synonym/abbreviation
   failure, incorrect field definition, candidate deduplication failure,
   gold-label problem, or other. Save a primary category, supporting trace and
   optional secondary causes. Unresolved cases stay explicitly unclassified
   within "other" until reviewed; do not infer a definitive cause from rank alone.
8. **Keep the test set held out.** Use development failures to choose changes.
   Compare frozen baseline and changed configurations on the same data and
   publish split-specific before/after metrics. Repeatedly inspecting test errors
   turns that set into development data; reserve a fresh lockbox for a renewed
   target claim. Report model/revision, dependencies, dataset/config hashes,
   learning snapshot, mode/fallback, seed, hardware and cold/warm latency.

The harness must come before a defensible baseline. Synthetic fixtures can
verify its mechanics immediately. Human annotation is required before measuring
the target; never treat workbook examples or automatically accepted suggestions
as gold. Keep private papers, annotations, snapshots and generated reports in
ignored local output/data directories. Commit only schemas, code and licensed
synthetic examples.

## Sequence and scope

Start with task 1. Establish shared source, field and diagnostic contracts before
parallel OCR or corpus work. Task 2 supplies definitions and corpus infrastructure
for task 4. Choose task 3 fixes using measured OCR/table failures. Task 5 follows a
measured candidate pool; task 6 follows frozen split and feedback contracts.
Do not build every retrieval technique merely because the specification lists it.
Add mechanisms where measured failures justify their complexity.

The following prompts are intended for separate implementation tasks. Each
includes its own prerequisite and completion boundary. Proposed modules or CLI
interfaces are future work, not commands that exist today.

### 1. Benchmark harness and unchanged baseline

```text
Read AGENTS.md, RETRIEVAL_MASTER_SPEC.md and RETRIEVAL_ROADMAP.md. Implement only
the benchmark foundation and measure the existing retriever without changing
ranking behavior. Inspect tests/ranking_quality_fixture.py and
tests/feedback_quality_fixture.py, but preserve their legacy metric semantics.

Create a separate Python evaluation package and documented command. Implement
the roadmap's versioned human-gold schema, duplicate-group paper splits,
source-span/equivalence matcher, any-gold item-level Recall@1/3/5, MRR, 95%
intervals, slices, no-gold counts and explicit not-evaluable status. Make sample
adequacy policy and split identity part of the report. Save deterministic JSON
and readable local reports with configuration/dataset hashes and per-item ranks.

Add optional diagnostics to retrieval.search_item and hybrid.engine so the
harness can inspect generation, shortlist, ranking and deduplication without
changing the normal API result or order. Measure full pool coverage and
CandidatePoolRecall@50, documenting the current pipeline's actual stages.
Report the pool-at-50 metric as not defined when the unchanged baseline has no
independent pool ordering; do not substitute final ranked top-50 results.
Produce trace-backed failure records using the master taxonomy.

Test multiple gold spans, source mismatch, duplicate leakage, no-answer versus
unlabeled cases, an OCR-missed answer, cluster support matches, empty datasets,
known metric arithmetic and diagnostic-on/off ranking equivalence. Keep real
review data out of fixtures. Run python -m pytest. Report the unchanged baseline
only for supplied human-labeled data; otherwise deliver the harness and synthetic
verification with the human benchmark explicitly unmeasured. Do not invent gold,
download models or claim 95% recall.
```

### 2. Structured extraction fields and review corpus

```text
Read AGENTS.md and the retrieval master specification/roadmap. After task 1's
contracts exist, extend the current item and corpus model as one bounded change.
Reuse service.ITEM_PROPERTIES, hybrid.models.item_spec, keywords.py, storage.py,
the item editor and workbook dictionary/export paths. Preserve existing fields,
explicit empty lists, item IDs and decisions through additive migration.

Support explicit positive_terms, synonyms, abbreviations, related_fields,
negative_terms, exclusion_contexts and value_patterns alongside descriptions,
units and preferred sections. Define how existing keywords/aliases/rules map to
these fields without silently overwriting reviewer edits. Validate related item
IDs within the same review, version material versus search-only edits, and
round-trip definitions through API, persistence, UI and safe Excel output.

Build a review-scoped corpus/terminology layer using uploaded source text. Start
with supported abbreviation expansions, spelling/unit variants and recurring
phrases; retain the source locations and ambiguous alternatives. Distinguish
observed associations from reviewer-confirmed synonyms. Never infer a numeric
value or train a neural model from the uploaded corpus. Version indexes and
invalidate affected searches when sources or definitions change. Keep learned
expansions separate from editable user keywords.

Verify cross-review isolation, conflicting abbreviations, deletion/restoration,
source changes, persistence and feedback invalidation. Use task 1's declared
held-out corpus-adaptation policy. Run python -m pytest and report applicable
development measurements; do not claim a retrieval gain from schema changes.
```

### 3. OCR, layout and source context

```text
Read AGENTS.md and the retrieval master specification/roadmap. Use task 1's error
report to select the largest demonstrated OCR/layout/table failure class. Extend
documents.py, ocr.py, layout.py, tables.py and hybrid/chunking.py for that class;
do not replace working ingestion or attempt all table formats in one change.

Retain exact raw source text, physical page coordinates and source versions.
Version normalized search text and maintain a mapping back to source spans when
normalization changes characters or whitespace. Cover 10 Hz, 10Hz, l0 Hz, 10-Hz
and 10 H z only in appropriate unit context; do not globally rewrite identifiers.
Expose stable block/paragraph identity and reading order within a source version.
Represent table headers, row context, footnotes and arm links with explicit
provenance and uncertainty when the selected failure class requires them.

Preserve lazy opt-in local OCR, errors/manual-review states, original PDF
navigation, archived source maps and reconsideration after reprocessing. Existing
evidence and approved values must remain stored. Do not silently substitute a
new OCR transcription for an old accepted quotation.

Add synthetic PDF/adapter regressions for the measured defect and verify exact
quotes, offsets, boxes and context. Run python -m pytest and applicable optional
browser/OCR integration checks, reporting unavailable checks. Compare development
pool recall and Recall@3 on affected OCR/table slices and unaffected native text.
Align gold to revised OCR text using immutable PDF anchors and recorded version
mapping or human re-adjudication; retain genuine OCR misses in the denominator.
Report remaining unsupported layouts and do not promote a default on fixture
results alone.
```

### 4. High-recall semantic candidate generator

```text
Read AGENTS.md and the retrieval master specification/roadmap. Use the benchmark
harness and versioned field/corpus contracts from tasks 1-2. Improve candidate
generation in retrieval.py and hybrid/engine.py while reusing exact/regex, fuzzy,
pattern and offline embedding/cache components. Add a separate sparse retrieval
component if measured generator misses justify it; evaluate BM25 against the
existing term-coverage/rarity baseline.

Construct explicit per-channel candidate lists and a deterministic union with
source identity, retrieval method and component scores. Include terminology,
units, tables and related-field proximity where supported. Make pool sizes and
channel quotas configurable; prevent a single semantic threshold or channel
from eliminating otherwise useful candidates. Preserve exact matches and source
provenance, and trace every filtering/cutoff stage.

Build labeled context from the candidate, neighboring sentences, paragraph,
section and table/row headers, with source references. Preserve the quotation
separately. Compare the existing cached model with suitable local scientific
embedding alternatives when available, recording model revision and resource
cost. Do not assume a scientific model improves performance. Keep Fast mode and
visible offline fallback; downloading a model must remain explicit.

Measure full pool recall, CandidatePoolRecall@50, top-three recall and cold/warm
latency using the frozen harness. Use an approximately 99% pool-recall objective
to diagnose generation, without claiming achievement on synthetic data. Verify
cache invalidation, restart reuse, review isolation and source traceability.
Run python -m pytest. Deliver this stage without introducing a cross-encoder or
training a feedback model.
```

### 5. Context-aware reranking and top-three diversity

```text
Read AGENTS.md and the retrieval master specification/roadmap. Start from task
1's diagnostics and task 4's measured candidate pool. First determine whether
missed gold was generated; fix no generation miss by merely changing weights.
Reuse hybrid/ranker.py, relevance.py, extractor.py and deduplication.py.

Make ranking configuration versioned and inspectable per review/item. Separate
semantic, lexical, section, unit/pattern, table, related-concept, vocabulary and
negative-context contributions, preserving missing-signal semantics. Investigate
an optional local cross-encoder on a bounded shortlist using the full field
definition and labeled candidate context. Keep a deterministic baseline and
visible unavailable-model fallback. Never run an expensive cross-encoder over
every OCR block or use a generative answer as evidence.

Cluster corroborating passages into conceptual candidates only when value,
measure, arm and timepoint identity support it. Retain every supporting exact
source location and existing suggestion/evidence ID. Distinct arms, timepoints,
outcomes, statistics and genuinely conflicting values must stay separate.
Select a diverse top three and report pre/post-selection ranks so deduplication
losses are measurable. Adapt the UI to show three leading candidates and their
supporting locations, with access to all other candidates and historical work.
Preserve evidence acceptance, value approval and manual search behavior.

Compare baseline, context-only, reranker and diversity ablations on development
data; test TMS-versus-EMG/EEG, discussion-versus-treatment, percentage and wrong-
arm/timepoint/statistic hard negatives. Rank scores remain scores, not confidence.
Run python -m pytest and relevant optional browser/model tests. Report Recall@1/3/5,
MRR, slices, pool recall, failure counts and latency. Tune only on development;
reserve the locked test comparison for a frozen release candidate.
```

### 6. Reproducible feedback-learning layer

```text
Read AGENTS.md, RETRIEVAL_MASTER_SPEC.md, RETRIEVAL_ROADMAP.md and
FEEDBACK_LEARNING.md. Extend the existing feedback architecture after task 1's
split/annotation contracts exist. Preserve feedback_state.py, service decision
history and the bounded opt-in adjustment in hybrid/feedback.py as the baseline.
This is an offline learning/data-versioning task, not automatic online training.

Record selected/rejected decisions and contextual reasons, adding compatible
incorrect_context, wrong_measure and OCR_error semantics alongside existing
wrong_arm, wrong_timepoint, wrong_outcome, wrong_statistic and duplicate reasons.
Keep labels conditional on the complete field/context definition: a wrong-arm
passage is not a universal negative. Preserve latest-effective decisions,
withdrawal, exclusions, resets and source/meaning invalidation. Include selection
rank and ranking/config versions so highly ranked rejected passages can be mined
as hard negatives without inventing exposure information for old events.

Produce immutable, versioned training snapshots with provenance, source hashes,
item versions, effective labels, exclusion reasons and paper-group splits.
Exclude target-paper copies and all held-out labels from training; identical
clicks/spans do not count as independent documents. Keep existing minimum-data
and context safeguards. Two accepted documents enabling the present similarity
adjustment are not proof of enough data to train or fine-tune a model.

When labeled data is adequate, compare a lightweight ranker or optional reranker
fine-tuning with the frozen baseline on development papers. Record reproducible
training configuration, artifact hash, promotion/rollback state and held-out
evaluation. Do not automatically promote a model from a few selections. If data
is inadequate, deliver validated snapshots and the evaluation path with training
explicitly deferred. Feedback must never fabricate evidence, change approved
values, infer missingness or bypass source/context validation.

Run python -m pytest and relevant optional model tests. Verify cross-review and
split isolation, duplicate-document handling, resets, reason edits, reprocessing,
concurrency and baseline equivalence with learning disabled/unavailable. Report
item-level before/after metrics and uncertainty; do not relabel the existing
synthetic span-level feedback results as achievement of the 95% target.
```

## Completion rule for each implementation task

Deliver the bounded change, applicable tests, reproducible measurements and
remaining limitations. A functional implementation can be complete while its
retrieval benefit remains unmeasured because human gold is unavailable. Claim
improvement only from an appropriate held-out comparison, and claim the 95%
objective only with the declared human-gold, sample-adequacy and uncertainty
requirements satisfied. Keep existing reports as historical evidence of their
own tests; this roadmap governs the new evaluation target.

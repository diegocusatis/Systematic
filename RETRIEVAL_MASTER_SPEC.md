You are working on my local Python application, **Systematic**, an OCR-assisted evidence extraction tool for systematic reviews.

Your primary objective is to redesign and optimize the candidate-retrieval system so that:

**Recall@3 ≥ 0.95 on a held-out labeled benchmark.**

Definition:

For each `(paper, extraction_item)` pair, there is one or more human-confirmed gold evidence spans.

A retrieval is successful if at least one of the top 3 returned candidates contains, overlaps, or clearly corresponds to a gold evidence span.

Recall@3 = successful extraction items / all extraction items with a valid gold answer.

Do NOT optimize primarily for top-1 accuracy. The purpose of Systematic is to make the correct evidence extremely likely to appear somewhere in the top 3 so that a human reviewer can verify and select it.

## Core design principle

Systematic must be an **extractive evidence retrieval system**, not a generative answering system.

Never generate evidence that does not exist in the uploaded PDFs.

Every candidate must be traceable to exact source text and retain:

- document ID
- page number
- section
- paragraph/block ID
- exact original OCR text
- surrounding context
- bounding box if available
- table/row/column metadata if applicable
- normalized candidate value if applicable
- component ranking scores
- final ranking score

The user must always be able to jump from a candidate back to its original location in the PDF.

## Review-specific corpus

When PDFs are uploaded for a systematic review, create a review-specific searchable corpus.

The system should automatically identify review-specific terminology, including:

- abbreviations
- synonyms
- intervention terminology
- outcome terminology
- units
- measurement names
- recurring phrases
- population terminology
- protocol terminology

Examples:

`RMT`, `resting motor threshold`, and `resting threshold`

or:

`10MWT`, `10-MWT`, `10 meter walk test`, and `ten-metre walk test`

should become strongly related within that review.

Do not train a neural network from scratch on a small set of uploaded PDFs. Use the uploaded PDFs for corpus adaptation, terminology discovery, embeddings, retrieval, and contextual ranking.

## Extraction-item definitions

Do not treat an Excel column name as the complete search query.

Each extraction field should support a structured definition:

```python
ExtractionField(
    name,
    description,
    positive_terms,
    synonyms,
    abbreviations,
    expected_units,
    related_fields,
    preferred_sections,
    negative_terms,
    exclusion_contexts,
    value_patterns
)
```

Example:

```text
name:
TMS intensity

description:
Intensity of therapeutic TMS stimulation, usually expressed as a percentage of resting motor threshold, active motor threshold, maximum stimulator output, or another defined stimulation threshold.

positive_terms:
RMT
AMT
motor threshold
stimulation intensity
stimulator output

expected patterns:
% RMT
% AMT
% MSO

preferred sections:
Methods
Intervention
Stimulation protocol

negative contexts:
outcome improvement percentages
retention percentages
confidence intervals
participant percentages
```

## Candidate generation

Candidate generation must favor **extremely high recall**.

Do not rely on a single retrieval method.

Generate a broad candidate pool using an ensemble including, where appropriate:

1. exact keyword search
2. regex/value-pattern search
3. abbreviation matching
4. synonym matching
5. fuzzy lexical matching
6. BM25 or equivalent sparse retrieval
7. dense scientific embeddings
8. semantic similarity
9. section-heading information
10. table cell/row retrieval
11. proximity to related extraction fields
12. review-specific terminology expansion
13. unit-aware search
14. neighboring sentence/paragraph context

Candidate generation may intentionally retrieve many candidates internally, for example 30–100 per extraction item.

Candidate generation should prioritize recall over precision.

## Context windows

Never rank isolated values without context.

For every candidate, construct a contextual representation containing, when available:

- candidate sentence
- previous sentence
- next sentence
- paragraph
- section heading
- table headers
- row headers
- nearby values
- related extraction concepts

Example:

Do not rank merely:

`90%`

Rank:

`Participants received 10-Hz rTMS over the leg motor cortex at 90% of resting motor threshold for 2,000 pulses per session.`

## Multi-stage retrieval architecture

Implement or refactor toward:

```text
PDF
↓
OCR/layout reconstruction
↓
structured blocks
↓
high-recall candidate generation
↓
candidate deduplication
↓
neural/semantic retrieval
↓
context-aware reranking
↓
field-specific evidence scoring
↓
candidate diversity selection
↓
top 3 candidates
```

The final reranker should combine multiple independent signals rather than depending entirely on embedding similarity.

Conceptually:

```text
FinalScore =
    semantic_score
  + lexical_score
  + section_score
  + unit_score
  + table_score
  + related_concept_score
  + value_pattern_score
  + review_vocabulary_score
  - negative_context_score
```

Do not assume fixed global weights are optimal.

Make component scores inspectable and configurable.

## Neural retrieval

Where practical, use a scientific-language embedding model for first-stage semantic retrieval.

For the final small candidate set, investigate a cross-encoder or equivalent context-aware reranker that jointly evaluates:

`extraction field definition + candidate + candidate context`

Do not run an expensive cross-encoder over every OCR block if a two-stage retrieval system can first reduce the candidate pool.

## Hard negatives

Hard negatives are essential.

Examples:

For `TMS frequency`:

Positive:

`10-Hz repetitive TMS was delivered over M1.`

Hard negatives:

`EMG signals were filtered at 10 Hz.`

`EEG data were analyzed in the 10-Hz band.`

`Previous studies used 10-Hz stimulation.`

For `TMS intensity`:

Positive:

`Stimulation was delivered at 90% RMT.`

Hard negatives:

`90% of participants completed treatment.`

`Scores improved by 15%.`

`The confidence interval was 90%.`

Use accepted and rejected candidates from the user as future positive and hard-negative training examples.

## Candidate diversity

Do not simply return three near-duplicate sentences.

After ranking, apply a diversity-aware selection method.

The top 3 should maximize the probability that at least one candidate is correct.

Prefer candidates that differ meaningfully in:

- location
- wording
- table vs prose
- section
- interpretation

unless multiple independent passages provide useful corroboration of the same value.

Strong duplicate candidates should be clustered together instead of occupying all three top positions.

For example:

```text
Likely value: 10 Hz

Supporting evidence:
- Methods, page 4
- Protocol table, page 5
```

should generally count as one conceptual candidate rather than two of the three available slots.

## Document structure

OCR output should preserve as much structure as possible.

Do not flatten PDFs into one plain-text string.

Represent:

```text
Document
 ├── Page
 │    ├── Section
 │    │    ├── Paragraph
 │    │    ├── Sentence
 │    │    └── Table
```

Preserve bounding boxes and reading order where available.

Section identity should affect ranking.

For intervention variables, evidence in Methods/Intervention should usually outrank the same term appearing in Introduction or Discussion.

This should be a ranking prior, not an absolute exclusion rule.

## Tables

Scientific tables are critical.

Create explicit handling for:

- column headers
- row headers
- multi-level headers
- merged cells
- footnotes
- units
- treatment-arm association

The text representation of a table value should include its row and column context before embedding or ranking it.

For example, do not embed:

`0.62 ± 0.14`

alone.

Embed something like:

`10MWT | post-treatment | rTMS group | 0.62 ± 0.14 m/s`

## Related-field reinforcement

Extraction fields should be able to reinforce each other.

Examples:

Intervention cluster:

```text
target
coil
frequency
intensity
pulse count
train duration
sessions
session frequency
```

Population cluster:

```text
sample size
AIS grade
injury level
chronicity
time since injury
```

Outcome cluster:

```text
10MWT
6MWT
WISCI-II
LEMS
TUG
SCIM
```

If several related variables occur in the same paragraph, table row, or protocol block, increase the likelihood that the block contains valid extraction evidence.

## OCR robustness

Account for realistic OCR errors.

Examples:

`10 Hz`
`10Hz`
`l0 Hz`
`10-Hz`
`10 H z`

Do not silently modify source evidence.

Maintain:

```text
raw_text
normalized_text
```

Use normalized text for search but show the original text to the reviewer.

## Evaluation framework

Build a proper evaluation harness before claiming that the system reaches the target.

Create a gold-standard dataset of human-confirmed evidence spans.

Split evaluation so that test examples are genuinely held out.

Do not evaluate only on PDFs or extraction decisions used to tune ranking parameters.

Prefer paper-level splitting so that passages from the same paper cannot leak into both training and testing.

Report at minimum:

```text
Recall@1
Recall@3
Recall@5
MRR
number of evaluated extraction items
number with no gold answer
performance by extraction field
performance by paper
performance for prose
performance for tables
performance for OCR-degraded text
```

Primary acceptance criterion:

```text
Recall@3 >= 0.95
```

on held-out extraction items that contain an extractable answer.

Also report a 95% confidence interval around Recall@3.

Do not claim the target has been achieved if the sample size is too small for a meaningful estimate.

## Error analysis

For every Recall@3 failure, classify the failure as one of:

```text
OCR failure
candidate generation failure
semantic retrieval failure
reranking failure
table parsing failure
section detection failure
synonym/abbreviation failure
incorrect field definition
candidate deduplication failure
gold-label problem
other
```

Produce counts by failure category.

Optimize the largest source of missed gold evidence first.

A gold candidate that was never generated is fundamentally different from a candidate that was generated but ranked #8.

Track both.

## Candidate-generation recall

Before final reranking, separately calculate whether the gold evidence exists anywhere in the candidate pool.

For example:

```text
CandidatePoolRecall@50
```

The candidate generator should approach ~99% recall if practical.

If Recall@3 is below target:

- first determine whether gold evidence was generated
- if not, improve retrieval/candidate generation
- if yes, improve reranking

Do not compensate for poor candidate generation merely by changing final ranking weights.

## User feedback

Record reviewer decisions such as:

```text
selected
rejected
incorrect_context
duplicate
wrong_arm
wrong_timepoint
wrong_measure
OCR_error
```

Treat accepted candidates as positives.

Treat highly ranked rejected candidates as especially valuable hard negatives.

Build the architecture so these decisions can later train a lightweight ranking model or fine-tune the reranker.

Do not automatically alter the model from a tiny number of selections.

Keep training data versioned and reproducible.

## Hallucination prevention

Systematic must not fabricate extraction values.

Every displayed candidate must correspond to evidence present in the uploaded PDF.

Normalized values may be calculated from text, but both should be retained:

```text
source_text:
"stimulation was delivered at ninety percent of resting threshold"

normalized_value:
90% RMT
```

The normalized value must never replace the source evidence.

Any AI-generated interpretation should be clearly separated from retrieved evidence.

## Confidence

Do not treat raw similarity scores as calibrated probabilities.

If the UI displays confidence, calibrate it against held-out labeled examples.

Otherwise call it a ranking score rather than a probability.

## Development process

First inspect the existing repository and understand the current OCR, candidate retrieval, ranking, UI, and Excel-export architecture.

Do not rewrite working components unnecessarily.

Then:

1. establish a baseline Recall@1/3/5
2. add the evaluation harness
3. identify the current dominant failure modes
4. improve candidate generation
5. improve contextual representation
6. implement or improve reranking
7. add diversity-aware top-3 selection
8. evaluate again
9. conduct field-level error analysis
10. iterate only where measured errors justify changes

After each major change, show before/after metrics.

Do not claim that a change improved the system unless it improves held-out performance.

## Final goal

The experience should be:

```text
User uploads review PDFs
        ↓
Systematic indexes and adapts to the review corpus
        ↓
User defines extraction items
        ↓
Systematic finds many possible evidence spans
        ↓
Systematic intelligently ranks them
        ↓
The correct evidence appears within the first 3 candidates
        ↓
Reviewer verifies/selects it
        ↓
Confirmed value is exported
        ↓
Selections improve future ranking
```

Optimize the system for:

**“The human reviewer almost never needs to manually search the PDF because the correct evidence is already somewhere in the top three candidates.”**

The numerical engineering target is:

**held-out Recall@3 ≥ 95%.**

Treat this as an empirical target that must be demonstrated through evaluation, not assumed or hard-coded.
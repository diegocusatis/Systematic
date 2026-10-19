# Implementation plan

1. Inspect the supplied workbook and preserve its exact 29 headings as an editable initial design. Its single example row is reference material, never imported as a study.
2. Build a local SQLite review service with stable identifiers, versioned extraction items and rules, recoverable deletion, evidence decisions, final values, repeated observations, and an append-only audit trail.
3. Add PDF ingestion and full-document literal/optional regular-expression search, with page coordinates, duplicate detection, processing progress, and explicit unsearchable-page warnings.
4. Build three browser sections using bundled PDF.js: setup, reading/evidence/extraction, and validation/export. Serve everything through FastAPI without Node.js.
5. Implement workbook preview/mapping and dynamic, safe Excel export with evidence, statuses, dictionary, observations, and history sheets.
6. Test the twelve requested journeys, including persistence, review isolation, rule refresh, stable values, restoring items, draft/final gating, and PDF highlight placement at different zoom levels. Deliver demo fixtures, Windows instructions, and CI.

## Assumptions

- One local reviewer and one application process. No authentication service or external API is needed; the launcher binds to loopback.
- A paper is one study record by default; inclusion is editable. Multiple groups/time points are separate observations and may be combined only in an explicitly written narrative.
- Definitions/types/allowed values/units/context/instructions changing invalidate completed approvals for reconsideration. Keyword changes refresh suggestions but retain reviewer decisions.
- Missing matches never assign a missingness status. Final exports require resolved included fields, evidence for approved values, and explicit acknowledgment of manual review on unsearchable content.
- All bundled demonstration papers are synthetic and labeled as such. No real source PDFs were supplied for factual verification of the example row.

## Reference inspection

`Systematic Example.xlsx` contains `Sheet1`, two rows and 29 columns (A:AC). The header fill is `FFDCE6F1`. The example uses group-labeled narratives, units, follow-up timing, discrepancy notes, and some numeric cells. Its `Link` cell is a HYPERLINK formula; imported examples are not executed or treated as evidence.

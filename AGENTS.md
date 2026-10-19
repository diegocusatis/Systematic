# Repository guidance

Build and test the local single-reviewer workflow. Keep Python modules for storage, documents, search, decisions, exports, and HTTP separate. End users run `python -m review_extractor`; do not introduce a Node.js runtime dependency.

Use stable review/paper/item IDs. Never infer missingness from absent matches. Never import template example rows as evidence. Keep exact source text distinct from normalized final values. Treat evidence decisions separately from value approval; preserve history across item changes and searching. Always scope paper and item access to their review. Excel exports must treat untrusted text as text and use the current active design.

Run `python -m pytest` after meaningful changes. Browser integration tests use optional Playwright and synthetic PDFs. Do not commit local review databases, user PDFs, virtual environments, generated test reports, or the user's original workbook. Bundled demo files are synthetic and licensed CC0. Bundled PDF.js retains its Apache-2.0 license.

For candidate-retrieval work, read `RETRIEVAL_MASTER_SPEC.md` as the master specification and `RETRIEVAL_ROADMAP.md` for the repository assessment, evaluation contract, and bounded implementation prompts. The primary target is human-labeled, held-out item-level Recall@3 >= 0.95; synthetic regression scores do not establish that target. Preserve working components and implement one measured stage at a time.

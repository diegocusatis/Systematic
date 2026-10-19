"""Time unchanged retrieval on bundled synthetic PDFs; no private review data.

Run with Python from the repository root. --compare checks complete result parity.
These timings and synthetic results do not establish held-out retrieval accuracy.
"""
import argparse
import cProfile
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from review_extractor.documents import process_pdf
from review_extractor.hybrid.chunking import chunk_document
from review_extractor.retrieval import search_item
from review_extractor.keywords import suggest_keywords


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    names = ['Mean age', 'Stimulation frequency', 'Stimulation intensity',
             'Sample size', 'LEMS SD of change', 'Trial registration',
             'Adverse events', 'Treatment duration', 'Total sessions',
             'Coil type', 'Motor threshold', 'Outcome measure', 'Study design',
             'Injury level', 'Control group']
    pages = []
    for letter in 'ABD':
        for page in process_pdf(ROOT / 'demo' / f'study-{letter}.pdf'):
            page = deepcopy(page)
            page['page_number'] = len(pages) + 1
            pages.append(page)
    items = [dict(name=name, definition='', instructions='', data_type='text',
                  keywords=suggest_keywords({'name': name}), rules=[], search_mode='fast') for name in names]
    timings, results = [], []
    profiler = cProfile.Profile() if args.profile else None
    if profiler: profiler.enable()
    for _ in range(2):
        start = perf_counter()
        chunks = chunk_document(pages)
        results.append([search_item(pages, item, chunks=chunks) for item in items])
        timings.append(perf_counter() - start)
    if profiler:
        profiler.disable()
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(args.profile)
    assert results[0] == results[1], 'Rescan changed results'
    report = dict(pages=len(pages), items=len(items), seconds=timings, results=results[0])
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding='utf-8'))
        assert baseline['results'] == report['results'], 'Evidence, scores or ranking changed'
        report['speedup'] = [a / b for a, b in zip(baseline['seconds'], timings)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, indent=2))


if __name__ == '__main__':
    main()

"""Measure full rescans on isolated copies of a local SQLite snapshot.

No source database is opened for writing. Outputs may contain private evidence;
keep the snapshot and output directory under ignored outputs/.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from review_extractor.service import ReviewService
from review_extractor import search


def clone(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--embeddings', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--paper-id', action='append', required=True)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory so earlier measurements are retained.')
    clone(args.snapshot, args.output / 'reviews.sqlite3')
    if args.embeddings:
        clone(args.embeddings, args.output / 'retrieval-cache' / 'embeddings.sqlite3')
    if args.timeout is not None:
        search.REGEX_TIMEOUT_SECONDS = args.timeout
    service = ReviewService(args.output)
    report = {'papers': [], 'snapshot_sha256': hashlib.sha256(args.snapshot.read_bytes()).hexdigest()}
    try:
        for paper_id in args.paper_id:
            paper = service.store.get('papers', paper_id)
            review_id = paper['review_id']
            before_fields = service.store.list('fields', review_id=review_id, paper_id=paper_id)
            before_evidence = service.store.list('evidence', review_id=review_id, paper_id=paper_id)
            timing = []
            for _ in range(2):
                start = perf_counter()
                service.refresh_search(review_id, paper_id)
                timing.append(perf_counter() - start)
            assert service.store.list('fields', review_id=review_id, paper_id=paper_id) == before_fields
            assert service.store.list('evidence', review_id=review_id, paper_id=paper_id) == before_evidence
            result = service.get_paper(review_id, paper_id)
            rows = service.store.list('suggestions', review_id=review_id, paper_id=paper_id)
            # IDs of newly discovered suggestions and timestamps differ across
            # independent copies. Compare all retrieval content and decisions.
            rows = [{k: v for k, v in row.items() if k not in {'id', 'created_at'}} for row in rows if row['active']]
            rows.sort(key=lambda row: (row['item_id'], row.get('rank', 0), row['page_number'] or 0, row['start'] or 0, row['quote']))
            entry = dict(paper_id=paper_id, pages=paper['page_count'], seconds=timing,
                         status=result['search_status'], errors=result.get('search_errors', []), results=rows)
            report['papers'].append(entry)
            print(json.dumps({k: v for k, v in entry.items() if k != 'results'}), flush=True)
        if args.compare:
            baseline = json.loads(args.compare.read_text(encoding='utf-8'))
            assert baseline['snapshot_sha256'] == report['snapshot_sha256']
            assert [(p['paper_id'], p['results']) for p in baseline['papers']] == [(p['paper_id'], p['results']) for p in report['papers']], 'Retrieval results changed'
            print('All complete candidate results and saved decisions match the comparison run.', flush=True)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        service.store.close()


if __name__ == '__main__':
    main()

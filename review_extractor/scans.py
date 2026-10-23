"""Explicit, deduplicated scan jobs. Opening the app never starts a scan."""
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import threading

from .documents import process_pdf, read_pdf_metadata
from .retrieval import ENGINE_VERSION

log = logging.getLogger(__name__)


class ScanQueue:
    def __init__(self, service, *, background=True):
        self.service = service
        self.background = background
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='review-scan')
        self.lock = threading.Lock()
        self.pending = set()

    def recover(self):
        """Make interrupted work visibly pending, retaining all saved results."""
        for review in self.service.list_reviews():
            self.service.initialize_keywords(review['id'])
            for paper in self.service.list_papers(review['id']):
                updates = {}
                if paper['processing_status'] in {'queued', 'processing'}:
                    updates['processing_status'] = 'pending'
                if (paper['search_status'] in {'queued', 'refreshing'} or
                        paper.get('retrieval_engine_version') != ENGINE_VERSION or
                        paper.get('retrieval_model_config') != self.service.retrieval_model_config()):
                    updates['search_status'] = 'stale'
                    updates['search_message'] = ''
                if updates:
                    self.service.update_paper(review['id'], paper['id'], updates)

    def busy(self, review_id):
        with self.lock:
            return any(key[0] == review_id for key in self.pending)

    def submit(self, review_id, paper_id, *, reprocess=False):
        # Validate ownership before reserving work. A duplicate click must not
        # restart progress or create another expensive scan behind the first.
        paper = self.service.get_paper(review_id, paper_id)
        if paper.get('removed'):
            raise ValueError('Restore this paper before scanning it.')
        key = (review_id, paper_id)
        with self.lock:
            if key in self.pending:
                return False
            self.pending.add(key)
        try:
            updates = {'search_status': 'queued', 'search_progress': 0,
                       'search_message': 'Waiting to scan'}
            if reprocess or paper['processing_status'] != 'ready':
                updates['processing_status'] = 'queued'
            self.service.update_paper(review_id, paper_id, updates)
            if self.background:
                self.executor.submit(self._run, review_id, paper_id, reprocess)
            else:
                self._run(review_id, paper_id, reprocess)
        except BaseException:
            with self.lock:
                self.pending.discard(key)
            raise
        return True

    def _run(self, review_id, paper_id, reprocess):
        stage = 'processing'
        try:
            paper = self.service.get_paper(review_id, paper_id)
            if paper.get('removed'):
                return
            if reprocess or paper['processing_status'] != 'ready':
                self.service.update_paper(review_id, paper_id, {
                    'processing_status': 'processing', 'processing_progress': 0,
                    'processing_error': '', 'manual_review_ack': False})
                pages = process_pdf(Path(paper['path']), lambda amount:
                    self.service.update_paper(review_id, paper_id, {'processing_progress': amount}))
                self.service.set_pages(review_id, paper_id, pages)
                self.service.update_paper(review_id, paper_id, {
                    'processing_status': 'ready', 'processing_progress': 100})
            stage = 'search'
            if reprocess or paper.get('pdf_metadata') is None:
                try:
                    metadata = read_pdf_metadata(Path(paper['path'])) if paper.get('path') else {}
                except Exception:
                    log.warning('PDF metadata unavailable for paper %s', paper_id)
                    metadata = {}
                with self.service.store.transaction():
                    current = self.service.get_paper(review_id, paper_id)
                    current['pdf_metadata'] = metadata
                    self.service.store.put('papers', current)
            self.service.refresh_search(review_id, paper_id)
        except Exception as exc:
            log.exception('Manual %s scan failed for paper %s', stage, paper_id)
            with self.service.store.transaction():
                current = self.service.get_paper(review_id, paper_id)
                message = f'{type(exc).__name__}: {exc}'
                current.update(search_status='error', search_errors=[message], search_message='')
                if stage == 'processing':
                    current.update(processing_status='error', processing_error=message)
                self.service.store.put('papers', current)
        finally:
            with self.lock:
                self.pending.discard((review_id, paper_id))

    def close(self):
        # Do not drain a whole queued review when the user exits. Unstarted jobs
        # become pending on reopening and require another explicit scan request.
        self.executor.shutdown(wait=True, cancel_futures=True)

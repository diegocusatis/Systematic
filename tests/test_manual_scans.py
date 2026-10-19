"""Explicit scans, durable results, review isolation and duplicate requests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from review_extractor.app import create_app
from review_extractor.scans import ScanQueue
from review_extractor.service import ReviewService
from test_api_journeys import upload, review, add_item, ok


def test_upload_edit_restore_and_restart_never_start_search(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = review(client)
        i = add_item(client, r)['id']
        paper = upload(client, r)
        p = paper['id']
        service = client.app.state.service
        assert paper['processing_status'] == 'pending'
        assert service.get_pages(r, p) == []
        assert service.get_suggestions(r, p, i) == []
        ok(client.post(f'/api/reviews/{r}/refresh'))
        original = service.store.list('suggestions', review_id=r, paper_id=p)
        assert original
        source = service.get_pages(r, p)
        # Any accidental automatic refresh from now on fails this test.
        monkeypatch.setattr(service, 'refresh_search', lambda *a: pytest.fail('Automatic search'))
        ok(client.patch(f'/api/reviews/{r}/items/{i}', json={'keywords': ['week']}))
        extra = add_item(client, r, name='Additional item')['id']
        ok(client.delete(f'/api/reviews/{r}/items/{extra}'))
        ok(client.post(f'/api/reviews/{r}/items/{extra}/restore'))
        ok(client.post(f'/api/reviews/{r}/items/import', json={'items': [{'name': 'Imported'}]}))
        assert service.store.list('suggestions', review_id=r, paper_id=p) == original
        ok(client.delete(f'/api/reviews/{r}/papers/{p}'))
        assert upload(client, r)['restored']
        original = [dict(row, active=False) for row in original]
        assert service.store.list('suggestions', review_id=r, paper_id=p) == original
        # Simulate interruption/upgrade. Reopening must keep both sources/results.
        service.update_paper(r, p, {'search_status': 'refreshing', 'retrieval_engine_version': 'old'})
    monkeypatch.setattr(ReviewService, 'refresh_search', lambda *a: pytest.fail('Startup search'))
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        service = client.app.state.service
        assert service.get_paper(r, p)['search_status'] == 'stale'
        assert service.get_pages(r, p) == source
        assert service.store.list('suggestions', review_id=r, paper_id=p) == original


def test_selected_paper_rescan_reuses_pages_and_preserves_decisions(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = review(client)
        i = add_item(client, r)['id']
        p = upload(client, r)['id']
        other = upload(client, r, 'study-B.pdf')['id']
        endpoint = f'/api/reviews/{r}/refresh'
        assert ok(client.post(endpoint, params={'paper_id': p}))['queued_count'] == 1
        service = client.app.state.service
        assert service.get_paper(r, other)['processing_status'] == 'pending'
        candidate = service.get_suggestions(r, p, i)[0]
        ok(client.patch(f'/api/reviews/{r}/papers/{p}/suggestions/{candidate["id"]}', json={'decision': 'rejected'}))
        pages = service.get_pages(r, p)
        version = service.get_paper(r, p)['document_version']
        monkeypatch.setattr('review_extractor.scans.process_pdf', lambda *a: pytest.fail('Unnecessary PDF extraction'))
        ok(client.post(endpoint, params={'paper_id': p}))
        assert service.get_pages(r, p) == pages
        assert service.get_paper(r, p)['document_version'] == version
        assert next(s for s in service.get_suggestions(r, p, i) if s['id'] == candidate['id'])['decision'] == 'rejected'
        r2 = review(client, 'Other review')
        assert client.post(f'/api/reviews/{r2}/refresh', params={'paper_id': p}).status_code == 404
        assert client.post(f'/api/reviews/{r2}/papers/{p}/reprocess').status_code == 404


def test_duplicate_clicks_do_not_queue_repeated_scans(tmp_path, monkeypatch):
    service = ReviewService(tmp_path)
    r = service.create_review({'name': 'Concurrent requests'})['id']
    p = service.add_paper(r, {'processing_status': 'ready', 'pdf_metadata': {}})['id']
    entered, release = Event(), Event()
    calls = []

    def slow_search(*args):
        calls.append(args)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(service, 'refresh_search', slow_search)
    scans = ScanQueue(service)
    try:
        assert scans.submit(r, p)
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=4) as requests:
            assert not any(requests.map(lambda _: scans.submit(r, p), range(8)))
        assert len(calls) == 1
    finally:
        release.set()
        scans.close()
        service.store.close()


def test_failed_search_can_be_retried_without_reextracting_pdf(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = review(client)
        add_item(client, r)
        p = upload(client, r)['id']
        service = client.app.state.service
        original = service.refresh_search
        def fail(*args):
            raise RuntimeError('Search interrupted')
        monkeypatch.setattr(service, 'refresh_search', fail)
        ok(client.post(f'/api/reviews/{r}/refresh'))
        assert service.get_paper(r, p)['processing_status'] == 'ready'
        assert service.get_paper(r, p)['search_status'] == 'error'
        monkeypatch.setattr(service, 'refresh_search', original)
        monkeypatch.setattr('review_extractor.scans.process_pdf', lambda *a: pytest.fail('Re-extracted'))
        ok(client.post(f'/api/reviews/{r}/refresh'))
        assert service.get_paper(r, p)['search_status'] == 'current'


def test_partial_status_and_results_survive_restart_without_auto_retry(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = review(client)
        i = add_item(client, r)['id']
        p = upload(client, r)['id']
        ok(client.post(f'/api/reviews/{r}/refresh'))
        service = client.app.state.service
        original = service.get_suggestions(r, p, i)
        service.update_paper(r, p, {'search_status': 'partial'})
    monkeypatch.setattr(ReviewService, 'refresh_search', lambda *a: pytest.fail('Automatic retry'))
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        service = client.app.state.service
        assert service.get_paper(r, p)['search_status'] == 'partial'
        assert [s['id'] for s in service.get_suggestions(r, p, i)] == [s['id'] for s in original]

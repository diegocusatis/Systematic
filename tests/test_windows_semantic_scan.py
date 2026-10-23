"""Real desktop stdin + offline model + background scan regression.

RUN_SEMANTIC_TESTS=1 and SYSTEMATIC_TEST_MODEL_CACHE select an existing local
Hugging Face cache. No downloads or personal review data are used.
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from review_extractor.service import ReviewService


@pytest.mark.semantic
@pytest.mark.skipif(os.name != 'nt' or os.environ.get('RUN_SEMANTIC_TESTS') != '1'
                    or not os.environ.get('SYSTEMATIC_TEST_MODEL_CACHE'),
                    reason='Opt-in Windows test with an existing offline model')
def test_desktop_background_semantic_scan_with_stop_pipe_open(tmp_path):
    root = Path(__file__).resolve().parents[1]
    home, data = tmp_path / 'program', tmp_path / 'reviews'
    home.mkdir()
    service = ReviewService(data)
    review_id = service.create_review({'name': 'Synthetic desktop scan'})['id']
    item_id = service.add_item(review_id, {'name': 'Stimulation frequency',
                                         'keywords': ['Hz'], 'search_mode': 'intelligent'})['id']
    paper_id = service.add_paper(review_id, {'filename': 'study-A.pdf',
                                           'path': str(root / 'demo' / 'study-A.pdf')})['id']
    service.store.close()
    state = dict(data=str(data), semantic_model=dict(
        repo=os.environ.get('SYSTEMATIC_EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2'),
        revision=os.environ.get('SYSTEMATIC_EMBEDDING_REVISION', 'main'),
        cache=os.environ['SYSTEMATIC_TEST_MODEL_CACHE']))
    (home / 'current.json').write_text(json.dumps(state), encoding='utf-8')
    code = (f'import sys; sys.path[:0] = {list(map(str, [root, root / "windows"]))!r}; '
            'from windows import runner; runner.main()')
    python = os.environ.get('SYSTEMATIC_TEST_PYTHON', sys.executable)
    child = subprocess.Popen([python, '-u', '-c', code, '--home', str(home)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', env=dict(os.environ, PYTHONUTF8='1'))
    lines, output = queue.Queue(), []
    def consume():
        for line in child.stdout:
            lines.put(line.strip())
        lines.put(None)
    threading.Thread(target=consume, daemon=True).start()
    def request(path, method='GET'):
        with urllib.request.urlopen(urllib.request.Request(url + path, method=method), timeout=5) as response:
            return json.load(response)
    try:
        deadline = time.monotonic() + 60
        url = None
        while time.monotonic() < deadline:
            line = lines.get(timeout=max(.1, deadline - time.monotonic()))
            output.append(line)
            assert line is not None, output
            if line.startswith('SYSTEMATIC_READY='):
                url = line.split('=', 1)[1]
                break
        assert url, output
        request(f'/api/reviews/{review_id}/refresh?paper_id={paper_id}', 'POST')
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            paper = request(f'/api/reviews/{review_id}/papers/{paper_id}')
            if paper['search_status'] not in {'queued', 'refreshing'}:
                break
            time.sleep(.1)
        assert paper['search_status'] == 'current', json.dumps(paper)
        assert paper['search_progress'] == 100
        assert not paper.get('retrieval_warnings'), paper
        rows = request(f'/api/reviews/{review_id}/papers/{paper_id}/suggestions?item_id={item_id}')
        assert any(row.get('scores', {}).get('semantic_score') is not None for row in rows)
        child.stdin.write('stop\n')
        child.stdin.flush()
        assert child.wait(timeout=20) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

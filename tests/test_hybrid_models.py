"""Opt-in integration with actual, already-cached local sentence embeddings."""
import os
import pytest
from review_extractor.hybrid.semantic import LocalSemantic
from review_extractor.retrieval import search_item
from test_retrieval import page, item
from ranking_quality_fixture import evaluate

pytestmark = [pytest.mark.semantic, pytest.mark.skipif(
    os.environ.get('RUN_SEMANTIC_TESTS') != '1', reason='Set RUN_SEMANTIC_TESTS=1 with a cached local model')]


def test_real_local_model_ranking_and_warm_cache(tmp_path, monkeypatch):
    semantic=LocalSemantic(tmp_path)
    examples=[
        ('Stimulation frequency','Surface EMG was sampled at 2000 Hz.','Repetitive TMS was administered over M1 at 10 Hz.'),
        ('Stimulation intensity','The intensity of rehabilitation increased.','Stimulation intensity was 90% of resting motor threshold.'),
        ('Sample size','Participants completed 24 treatment sessions.','Twenty-four participants were randomized.'),
        ('LEMS SD of change','Baseline LEMS was 32.1 ± 4.2.','The change in LEMS was 4.6 ± 2.7.'),
    ]
    for name,bad,good in examples:
        source=[page('Methods\n'+bad+'\n\n'+good)]
        result=search_item(source,item(name),semantic=semantic)
        assert not result['warnings'], result['warnings']
        rows=result['matches']
        assert rows[0]['quote']==good
        assert rows[0]['scores']['semantic_score'] is not None
    # A repeated query must not run model inference at all.
    def forbidden(*args,**kwargs): raise AssertionError('Warm search re-embedded text')
    monkeypatch.setattr(semantic.encoder,'encode',forbidden)
    cached=search_item(source,item(name),semantic=semantic)
    assert not cached['warnings'] and cached['matches'][0]['quote']==good


def test_real_model_quality_fixtures_and_legacy_spans(tmp_path, monkeypatch):
    semantic = LocalSemantic(tmp_path)
    metrics = evaluate(search_item, page, item, semantic)
    assert semantic.encoder is not None, semantic.error
    assert metrics['mrr'] == 1 and metrics['recall_at_5'] == 1, metrics
    assert metrics['duplicate_findings_at_5'] == 0, metrics
    # Every candidate has a measured score, including wrapped legacy excerpts.
    source = [page('Adults were enrolled and\naged 44.3 ± 8.1 years.\n\nAge affected recovery.')]
    result = search_item(source,item('Mean age'),semantic=semantic)
    assert not result['warnings']
    assert all(r['scores']['semantic_score'] is not None for r in result['matches'])
    def forbidden(*args, **kwargs): raise AssertionError('Warm search encoded text')
    monkeypatch.setattr(semantic.encoder,'encode',forbidden)
    assert evaluate(search_item,page,item,semantic) == metrics
    assert search_item(source,item('Mean age'),semantic=semantic)['matches'] == result['matches']

"""Source and feedback changes cannot publish outdated learned rankings."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from review_extractor.service import ReviewService
from review_extractor.hybrid.semantic import LocalSemantic
from test_hybrid import Encoder
from test_retrieval import page


@pytest.fixture
def learning_review(tmp_path):
    s=ReviewService(tmp_path)
    s.semantic=LocalSemantic(tmp_path/'cache',encoder=Encoder())
    r=s.create_review({'name':'Synthetic feedback concurrency'})['id']
    i=s.add_item(r,{'name':'Mean age'})['id']
    papers=[]
    for n in range(3):
        p=s.add_paper(r,{'title':f'Synthetic {n}','sha256':f'synthetic-bytes-{n}','processing_status':'ready'})['id']
        text=f'The mean age was {31+n} years in cohort {n}.'
        s.set_pages(r,p,[page(text)])
        if n<2:
            s.add_candidate(r,p,i,{'page_number':1,'start':0,'end':len(text),'quote':text,
                'document_version':1,'decision':'accepted'})
        papers.append(p)
    s.update_learning(r,{'enabled':True})
    s.refresh_search(r,papers[-1])
    yield s,r,i,papers
    s.store.close()


@pytest.mark.parametrize('edit',['source','remove','exclude','reset','toggle','reconsider','meaning','allowed_values'])
def test_training_changes_during_target_refresh_mark_result_stale(learning_review,monkeypatch,edit):
    import review_extractor.service as module
    s,r,i,papers=learning_review
    entered, proceed=threading.Event(),threading.Event()
    original=module.search_item
    def delayed(*a,**k):
        entered.set(); assert proceed.wait(8)
        return original(*a,**k)
    monkeypatch.setattr(module,'search_item',delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(s.refresh_search,r,papers[-1])
        assert entered.wait(8)
        if edit=='source': s.set_pages(r,papers[0],[page('Changed source wording.')])
        elif edit=='remove': s.remove_paper(r,papers[0])
        elif edit=='exclude': s.exclude_learning_example(r,s.get_learning(r)['examples'][0]['example_id'],{'excluded':True})
        elif edit=='reset': s.update_learning(r,{'reset':True})
        elif edit=='toggle': s.update_learning(r,{'enabled':False})
        elif edit=='reconsider': s.decide_suggestion(r,papers[0],s.get_suggestions(r,papers[0],i)[0]['id'],{'decision':'pending'})
        elif edit=='meaning': s.update_item(r,i,{'name':'Median age'})
        else: s.update_item(r,i,{'allowed_values':['99']})
        proceed.set(); future.result(timeout=8)
    assert s.get_paper(r,papers[-1])['search_status']=='stale'
    assert all(row['stale'] for row in s.get_suggestions(r,papers[-1],i))


def test_training_source_remove_restore_invalidates_other_papers(learning_review):
    s,r,i,papers=learning_review
    revision=s.get_learning(r)['config']['revision']
    s.remove_paper(r,papers[0])
    assert s.get_paper(r,papers[-1])['search_status']=='stale'
    assert s.get_learning(r)['config']['revision']>revision
    assert not next(e for e in s.get_learning(r)['examples'] if e['paper_id']==papers[0])['eligible']
    s.refresh_search(r,papers[-1])
    assert all(row['learning']['status']=='sparse_feedback' for row in s.get_suggestions(r,papers[-1],i))
    restored=s.add_paper(r,{'sha256':'synthetic-bytes-0'})
    assert restored['id']==papers[0] and restored['restored']
    assert s.get_paper(r,papers[-1])['search_status']=='stale'


def test_legacy_manual_import_uses_decision_time_not_later_notes(tmp_path):
    s=ReviewService(tmp_path)
    r=s.create_review({'name':'Synthetic legacy import'})['id']
    i=s.add_item(r,{'name':'Mean age'})['id']
    p=s.add_paper(r,{'title':'Synthetic','processing_status':'ready'})['id']
    text='The mean age was 47 years.'
    s.set_pages(r,p,[page(text)])
    e=s.add_evidence(r,p,i,{'page_number':1,'quote':text})
    # Emulate a database predating manual feedback, not an action exposed to users.
    s.store.connection.execute('DELETE FROM feedback')
    s.update_item(r,i,{'name':'Mean time since injury'})
    s.update_evidence(r,p,e['id'],{'notes':'Review later'})
    before=deepcopy(s.get_evidence(r,p,i))
    result=s.update_learning(r,{'enabled':True})
    assert len(result['examples'])==1 and not result['examples'][0]['eligible']
    assert 'meaning changed' in result['examples'][0]['excluded_reason']
    assert s.get_evidence(r,p,i)==before
    s.store.close()

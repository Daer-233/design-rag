from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.manuals.retrieval import HybridRetriever
from app.manuals.service import ManualService
from app.manuals.langbot import KnowledgeRetrieval, retrieve


def document(eid, title='账号申请指南', content=None, version='2026', family=None, count=2):
    return {'id': eid, 'title': title, 'product': '新员工', 'version': version, 'family_key': family or eid,
            'category_id': 'IT', 'status': 'published', 'processing_status': 'ready', 'index_status': 'indexed',
            'source_name': title + '.pdf', 'source_path': None, 'document_version_id': 'v-' + eid,
            'data': {'steps': [{'order': i + 1, 'instruction': content or f'{title}，选择账号申请按钮 {i}',
                               'evidence_id': f'{eid}-s{i}', 'asset_ids': [], 'page': i + 1}
                              for i in range(count)]}}


class Store:
    def __init__(self, entries, pictures=None): self.rows, self.pictures = entries, pictures or []
    def entries(self): return deepcopy(self.rows)
    def assets(self, eid): return [deepcopy(a) for a in self.pictures if a['entry_id'] == eid]


def engine(entries, *, scores=None, vector_error=False, rerank_error=False, extra_points=None):
    queries, reranks, embeds = [], [], []
    points = [NS(id=s['evidence_id'], score=.9, payload={'manual_entry_id': e['id'],
                 'document_version_id': e['document_version_id'], 'chunk_level': 'child'})
              for e in entries for s in e['data']['steps']]
    class Provider:
        collection_name = 'manual_rag_chunks_v1'
        def __init__(self): self.client = self
        def query_points(self, **kwargs):
            queries.append(kwargs)
            return NS(points=points + (extra_points or []))
    def embed(texts):
        embeds.append(texts)
        if vector_error: raise RuntimeError('Embedding offline')
        return {'dense_vectors': [[1.0]], 'sparse_vectors': [{'indices': [1], 'values': [1.0]}]}
    def rerank(question, texts):
        reranks.append((question, texts))
        if rerank_error: raise RuntimeError('Reranker offline')
        return [{'index': i, 'score': scores[i] if scores is not None else 5.0 - i / 100}
                for i in range(len(texts))]
    return HybridRetriever(NS(embed=embed), NS(rerank=rerank), Provider), queries, reranks, embeds


def test_exact_title_always_runs_embedding_both_vectors_and_unified_rerank():
    rows = [document('one')]
    retriever, queries, reranks, embeds = engine(rows)
    result = retriever.search('账号申请指南', rows)
    assert len(embeds) == 1 and len(queries) == 2 and len(reranks) == 1
    assert {q['using'] for q in queries} == {'dense', 'sparse'}
    assert result.mode == 'hybrid_rerank'
    assert all(result.metrics[k] > 0 for k in ['dense_count', 'sparse_count', 'keyword_count'])


def test_candidates_deduplicated_capped_and_room_for_another_document(monkeypatch):
    monkeypatch.setattr(settings, 'retrieval_rerank_candidate_top_k', 8)
    rows = [document('one', count=16), document('two', count=3)]
    retriever, _, reranks, _ = engine(rows)
    result = retriever.search('账号申请', rows)
    assert len(reranks[0][1]) == 8
    assert len({h.evidence_id for h in result.hits}) == len(result.hits)
    assert {h.entry['id'] for h in result.hits} == {'one', 'two'}


def test_stale_vector_ids_and_wrong_version_payloads_cannot_enter_candidates():
    row = document('one')
    stale = NS(id='deleted-child', score=10, payload={'manual_entry_id': 'one', 'document_version_id': 'old'})
    retriever, queries, reranks, _ = engine([row], extra_points=[stale])
    result = retriever.search('账号申请', [row])
    assert len(queries) == 2
    assert all(q['query_filter'].must[0].match.any == ['v-one'] for q in queries)
    assert all(h.evidence_id != 'deleted-child' for h in result.hits)
    assert result.metrics['dense_count'] == 2
    assert all('deleted-child' not in text for text in reranks[0][1])


def test_negative_rerank_logits_return_no_evidence_even_with_keyword_match():
    row = document('one')
    retriever, _, _, _ = engine([row], scores=[-10, -8])
    result = ManualService(Store([row]), retriever=retriever).ask('账号申请', source_only=True)
    assert result['status'] == 'insufficient_evidence' and result['sources'] == []
    assert result['retrieval_mode'] == 'hybrid_rerank'


def test_embedding_failure_still_reranks_keyword_candidates_and_marks_degradation():
    row = document('one')
    retriever, _, reranks, _ = engine([row], vector_error=True)
    result = retriever.search('账号申请', [row])
    assert result.mode == 'keyword_rerank' and len(reranks) == 1 and result.hits
    assert result.limitations and result.metrics['dense_count'] == 0


def test_rerank_failure_never_labels_fallback_as_full_hybrid():
    row = document('one')
    retriever, _, _, _ = engine([row], rerank_error=True)
    result = retriever.search('账号申请', [row])
    assert result.mode == 'keyword_fallback' and result.hits
    assert any('重排序不可用' in s for s in result.limitations)


def test_multi_document_evidence_keeps_sources_versions_and_step_images():
    a, b = document('onboard', title='入职账号指南'), document('policy', title='IT 权限规范', version='3.1')
    a['data']['steps'][0]['asset_ids'] = ['a1']
    b['data']['steps'][0]['asset_ids'] = ['b1']
    images = [{'id': asset, 'entry_id': eid, 'name': asset, 'page_number': 1, 'metadata': {}}
              for eid, asset in [('onboard', 'a1'), ('policy', 'b1')]]
    retriever, _, _, _ = engine([a, b])
    model = NS(complete=lambda *args: pytest.fail('LangBot must own final generation'))
    service = ManualService(Store([a, b], images), model, retriever)
    response = service.ask('新员工如何申请账号及权限？', source_only=True)
    assert response['source_count'] == 2 and not response['generated']
    assert {s['entry']['version'] for s in response['sources']} == {'2026', '3.1'}
    for source in response['sources']:
        owner = source['entry']['id']
        assert all(step['source']['entry_id'] == owner for step in source['steps'])
        assert {a['asset_id'] for step in source['steps'] for a in step['images']} == ({'a1'} if owner == 'onboard' else {'b1'})
    assert '冲突' in response['synthesis_policy']
    packet = retrieve(service, KnowledgeRetrieval(query='账号申请', top_k=8))
    assert len(packet['results']) == 2
    assert {r['metadata']['entry_id'] for r in packet['results']} == {'onboard', 'policy'}
    assert all('规定冲突' in r['content'] for r in packet['results'])


def test_different_versions_of_same_family_require_selection():
    rows = [document('old', family='vpn', version='1'), document('new', family='vpn', version='2')]
    retriever, _, _, _ = engine(rows)
    response = ManualService(Store(rows), retriever=retriever).ask('账号申请', source_only=True)
    assert response['status'] == 'choose_version' and response['steps'] == []
    assert {e['version'] for e in response['options']} == {'1', '2'}


def test_unpublished_versions_do_not_reach_vector_filters_or_rerank():
    live, hidden = document('live'), document('hidden')
    hidden['status'] = 'superseded'
    retriever, queries, reranks, _ = engine([live, hidden])
    response = ManualService(Store([live, hidden]), retriever=retriever).ask('账号申请', source_only=True)
    assert all(q['query_filter'].must[0].match.any == ['v-live'] for q in queries)
    assert all(s['entry']['id'] == 'live' for s in response['sources'])


def test_late_hit_returns_its_parent_context_not_document_beginning():
    row = document('long', count=45)
    retriever, _, _, _ = engine([row])
    retriever.search = lambda *args: NS(mode='hybrid_rerank', limitations=[], metrics={}, hits=[
        __import__('app.manuals.retrieval', fromlist=['EvidenceHit']).EvidenceHit(row, 42, 'long-s42', 4, ['dense'])])
    response = ManualService(Store([row]), retriever=retriever).ask('后段操作', source_only=True)
    source = response['sources'][0]
    assert source['page'] == 3 and source['previous_page'] == 2
    assert source['steps'][0]['step_index'] == 40
    assert source['matched_evidence_ids'] == ['long-s42']
    assert source['steps'][2]['is_match'] is True


def test_explicit_groups_isolate_context_and_continue_within_procedure():
    from app.manuals.retrieval import EvidenceHit
    row = document('grouped', count=28)
    row['data']['groups'] = [
        {'id':'onboarding','title':'入职','start':0,'end':3,'confirmed':True},
        {'id':'leave','title':'请假','start':3,'end':28,'confirmed':True},
    ]
    retriever, _, _, _ = engine([row])
    retriever.search = lambda *args: NS(mode='hybrid_rerank', limitations=[], metrics={}, hits=[
        EvidenceHit(row, 5, 'grouped-s5', 4, ['dense'])])
    service = ManualService(Store([row]), retriever=retriever)
    first = service.ask('请假申请', source_only=True)['sources'][0]
    assert first['group_title'] == '请假'
    assert [s['step_index'] for s in first['steps']] == list(range(3,23))
    assert first['has_more'] and first['previous_page'] is None
    second = service.ask('请假申请',entry_id='grouped',page=3,source_only=True)['sources'][0]
    assert [s['step_index'] for s in second['steps']] == list(range(23,28))
    assert second['previous_page'] == 2 and not second['has_more']


def test_separate_hits_in_one_document_keep_distinct_groups():
    from app.manuals.retrieval import EvidenceHit
    row = document('grouped', count=4)
    row['data']['groups'] = [{'id':'a','title':'入职','start':0,'end':2,'confirmed':True},
                             {'id':'b','title':'请假','start':2,'end':4,'confirmed':True}]
    retriever, _, _, _ = engine([row])
    retriever.search = lambda *args: NS(mode='hybrid_rerank', limitations=[], metrics={}, hits=[
        EvidenceHit(row, 0, 'grouped-s0', 4, ['dense']),EvidenceHit(row, 2, 'grouped-s2', 3, ['dense'])])
    sources=ManualService(Store([row]),retriever=retriever).ask('制度',source_only=True)['sources']
    assert [(s['group_title'],[x['step_index'] for x in s['steps']]) for s in sources] == [('入职',[0,1]),('请假',[2,3])]


def test_context_budget_never_truncates_or_silently_omits_original_paragraphs():
    row = document('long', content='原文' * 8000, count=3)
    retriever, _, _, _ = engine([row])
    response = ManualService(Store([row]), retriever=retriever).ask('账号申请', source_only=True)
    assert response['limitations'] and response['retrieval_metrics']['omitted_anchors'] > 0
    assert all(step['instruction'] == '原文' * 8000 for step in response['steps'])
    assert sum(len(s['instruction']) for s in response['steps']) <= 24000


def test_conflicting_statements_keep_both_original_sources():
    a = document('policy-a', title='测试规则 A', content='仅供测试：额度上限为 1000。')
    b = document('policy-b', title='测试规则 B', content='仅供测试：额度上限为 2000。')
    retriever, _, _, _ = engine([a, b])
    service = ManualService(Store([a, b]), retriever=retriever)
    packet = retrieve(service, KnowledgeRetrieval(query='额度上限', top_k=8))
    assert len(packet['results']) == 2
    by_owner = {r['metadata']['entry_id']: r for r in packet['results']}
    assert '1000' in by_owner['policy-a']['content'] and '2000' not in by_owner['policy-a']['content']
    assert '2000' in by_owner['policy-b']['content'] and '1000' not in by_owner['policy-b']['content']
    assert all('不自行裁决' in r['content'] for r in packet['results'])


def test_wrong_version_payload_is_not_a_vector_hit_even_when_chunk_id_exists():
    row = document('one')
    retriever, _, _, _ = engine([row])
    class WrongProvider:
        collection_name = 'manual_rag_chunks_v1'
        def __init__(self): self.client = self
        def query_points(self, **kwargs):
            return NS(points=[NS(id='one-s0', payload={'manual_entry_id':'one',
                'document_version_id':'old-version','chunk_level':'child'})])
    retriever.provider_factory = WrongProvider
    result = retriever.search('账号申请', [row])
    assert result.metrics['dense_count'] == result.metrics['sparse_count'] == 0
    assert all(hit.lanes == ['keyword'] for hit in result.hits)


def test_incomplete_rerank_cannot_be_reported_as_success():
    row = document('one')
    retriever, _, _, _ = engine([row])
    retriever.reranker = NS(rerank=lambda *args: [{'index':0,'score':4.0}])
    result = retriever.search('账号申请', [row])
    assert result.mode == 'keyword_fallback'
    assert result.metrics['rerank_error_type'] == 'ValueError'


def test_index_queue_waits_for_retrieval_and_drains_one_entry_at_a_time():
    rows = [document('one'), document('two')]
    for row in rows: row['index_status'] = 'queued'
    service = ManualService(Store(rows))
    processed = []
    def index(eid):
        processed.append(eid)
        next(e for e in rows if e['id']==eid)['index_status']='indexed'
    service.index_entry = index
    service.lock.acquire()
    try: service.index_next()
    finally: service.lock.release()
    assert processed == []
    service.index_next()
    assert processed == ['one']
    service.index_next()
    assert processed == ['one','two']

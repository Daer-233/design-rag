from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.manuals.langbot import KnowledgeRetrieval, retrieve


def invoke(answer, **payload):
    captured = {}
    def ask(**kwargs):
        captured.update(kwargs)
        return answer
    return retrieve(SimpleNamespace(ask=ask), KnowledgeRetrieval(query='VPN', **payload)), captured


def test_no_evidence_returns_no_fabricated_hit():
    result, called = invoke({'status': 'insufficient_evidence'})
    assert result['results'] == []
    assert called['source_only'] is True


def test_version_choices_remain_explicit():
    result, _ = invoke({'status': 'choose_version', 'options': [
        {'id': 'v1', 'title': 'VPN', 'version': '1'}, {'id': 'v2', 'title': 'VPN', 'version': '2'}]})
    hit = result['results'][0]
    assert hit['metadata']['result_type'] == 'version_choice'
    assert '不得混合' in hit['content'] and '版本 2' in hit['content']


def test_parent_packet_preserves_order_sources_images_and_pagination():
    answer = {'status': 'ok', 'entry': {'id': 'entry', 'title': 'VPN'}, 'page': 1, 'has_more': True,
              'total_steps': 23, 'steps': [
        {'order': 1, 'instruction': '申请账号', 'evidence_ids': ['e1'],
         'source': {'file_name': '入职.pdf', 'page': 3},
         'images': [{'asset_id': 'image1', 'name': '界面', 'preview_url': '/signed'}]},
        {'order': None, 'instruction': '适用条件说明', 'evidence_ids': ['e2'],
         'section_images': [{'asset_id': 'image2', 'name': '示意图'}]}]}
    result, _ = invoke(answer, top_k=1)
    hit = result['results'][0]
    assert len(result['results']) == 1
    assert hit['content'].index('申请账号') < hit['content'].index('适用条件说明')
    assert '入职.pdf · 第 3 页' in hit['content'] and '"page": 2' in hit['content']
    assert [a['association'] for a in hit['metadata']['images']] == ['step', 'section']
    assert hit['metadata']['steps'] == answer['steps']
    assert hit['metadata']['score_kind'].endswith('not_confidence')
    assert '原文件未提供页码' in hit['content']
    assert '检索批次不是原文件页码' in hit['content']
    assert 'page' not in hit['metadata'] and hit['metadata']['retrieval_page'] == 1


def test_filters_and_page_are_passed_to_authoritative_service():
    eid = '11111111-1111-4111-8111-111111111111'
    _, called = invoke({'status': 'insufficient_evidence'}, filters={'version': '2026', 'entry_id': eid, 'page': 2})
    assert str(called['entry_id']) == eid and called['version'] == '2026' and called['page'] == 2


@pytest.mark.parametrize('payload', [{'query': ''}, {'query': 'x', 'filters': {'published': False}},
                                     {'query': 'x', 'top_k': 100}, {'query': 'x', 'source_only': False}])
def test_retrieval_cannot_widen_scope_or_enable_model(payload):
    with pytest.raises(ValidationError): KnowledgeRetrieval(**payload)


def test_query_envelope_is_read_only_and_keeps_query_and_scope():
    eid = '11111111-1111-4111-8111-111111111111'
    captured = {}
    def ask(**kwargs):
        captured.update(kwargs)
        return {'status': 'insufficient_evidence'}
    retrieve(SimpleNamespace(ask=ask), KnowledgeRetrieval(query='@knowledge {"entry_id":"' + eid + '","page":2}\nVPN 申请'))
    assert str(captured['entry_id']) == eid and captured['page'] == 2
    assert captured['question'] == 'VPN 申请' and captured['source_only'] is True
    with pytest.raises(ValueError):
        retrieve(SimpleNamespace(ask=ask), KnowledgeRetrieval(query='@knowledge {"status":"draft"}\nVPN'))


def test_busy_service_is_not_misrepresented_as_no_knowledge():
    def busy(**kwargs): raise ValueError('正在处理另一条检索，请稍后重试')
    response = retrieve(SimpleNamespace(ask=busy), KnowledgeRetrieval(query='VPN'))
    assert response['status'] == 'busy'
    assert response['results'][0]['metadata']['result_type'] == 'retrieval_notice'
    assert '不能据此判断资料不存在' in response['results'][0]['content']

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app.core.config import settings
from app.manuals.content import extract_local, source_steps, save_image
from app.manuals.isolation import DATA, inside_data, validate_isolation
from app.manuals.service import ManualService, keyword_score


def entry(eid='one',version='1',status='published',count=2):
    return {'id':eid,'title':'导出手册','product':'示例软件','version':version,'family_key':'export',
            'category_id':None,'status':status,'processing_status':'ready','index_status':'pending',
            'source_name':'导出说明.pdf','source_path':'not-used','document_version_id':'v'+eid,
            'data':{'steps':[{'order':i+1,'instruction':'选择导出选项 '+str(i),
                             'evidence_id':eid+'-s'+str(i),'asset_ids':[],'page':i+1} for i in range(count)]}}


class FakeStore:
    def __init__(self,entries,assets=None): self.rows,self.pictures=entries,assets or []
    def entries(self): return deepcopy(self.rows)
    def get(self,eid): return deepcopy(next(e for e in self.rows if e['id']==eid))
    def assets(self,eid): return [deepcopy(a) for a in self.pictures if a['entry_id']==eid]


@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    monkeypatch.setattr(settings,'llm_api_key',None)
    monkeypatch.setattr(settings,'openai_api_key',None)
    from app.manuals.retrieval import HybridRetriever
    from app.clients.inference import RerankClient
    def unavailable(*args,**kwargs): raise RuntimeError('Offline unit fixture')
    monkeypatch.setattr(HybridRetriever,'vector_lanes',unavailable)
    monkeypatch.setattr(RerankClient,'rerank',unavailable)


def test_isolation_rejects_design_database(monkeypatch):
    monkeypatch.setattr(settings,'postgres_dsn','postgresql://design_rag:REPLACE_ME@127.0.0.1/design_rag')
    with pytest.raises(RuntimeError): validate_isolation()


def test_valid_manual_profile_and_cross_root_rejected(monkeypatch):
    monkeypatch.setattr(settings,'postgres_dsn','postgresql://manual_rag:REPLACE_ME@127.0.0.1/manual_rag')
    monkeypatch.setattr(settings,'qdrant_collection','manual_rag_chunks_v1')
    monkeypatch.setattr(settings,'data_root',DATA)
    monkeypatch.setattr(settings,'vision_analysis_derivative_root',DATA/'vision-analysis-derivatives')
    monkeypatch.setattr(settings,'design_rag_asset_signing_secret','test-only')
    assert validate_isolation()['dbname']=='manual_rag'
    with pytest.raises(ValueError): inside_data(DATA/'..'/'design.txt')


def test_source_numbering_is_not_invented():
    assert [s['order'] for s in source_steps([('1. 打开设置',1,[]),('2. 选择导出',2,[])])]==[1,2]
    assert all(s['order'] is None for s in source_steps([('打开设置',1,[]),('选择导出',2,[])]))
    assert all(s['order'] is None for s in source_steps([('1. 操作',1,[]),('1. 其他流程',2,[])]))


def test_docx_preserves_text_and_order_without_external_parser(tmp_path):
    path=tmp_path/'manual.docx'
    with ZipFile(path,'w') as z:
        z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>打开设置。</w:t></w:r></w:p><w:p><w:r><w:t>选择导出。</w:t></w:r></w:p></w:body></w:document>''')
    steps=extract_local(path,lambda *args:pytest.fail('No image expected'))
    assert [s['instruction'] for s in steps]==['打开设置。','选择导出。']
    assert all(s['order'] is None for s in steps)


def test_draft_and_superseded_entries_do_not_enter_default_search():
    service=ManualService(FakeStore([entry('old',status='superseded'),entry('draft',status='draft'),entry('active')]))
    result=service.ask('导出手册')
    assert result['entry']['id']=='active'
    assert result['generated'] is False
    assert result['steps'][0]['instruction']=='选择导出选项 0'


def test_multiple_software_versions_require_choice():
    service=ManualService(FakeStore([entry('one','1'),entry('two','2')]))
    assert service.ask('导出手册')['status']=='choose_version'
    assert service.ask('导出手册',version='2')['entry']['id']=='two'


def test_no_evidence_does_not_call_model(monkeypatch):
    service=ManualService(FakeStore([]),SimpleNamespace(complete=lambda *a:pytest.fail('No model call')))
    assert service.ask('紫色飞船')['status']=='insufficient_evidence'


def test_long_procedure_is_paginated_without_silent_truncation():
    service=ManualService(FakeStore([entry(count=25)]))
    first=service.ask('导出手册')
    second=service.ask('导出手册',page=2)
    assert first['total_steps']==25 and first['has_more']
    assert len(first['steps'])==20 and len(second['steps'])==5
    assert second['steps'][0]['order']==21 and not second['has_more']


def test_fabricated_model_citation_falls_back_to_original(monkeypatch):
    monkeypatch.setattr(settings,'llm_api_key','test-only')
    service=ManualService(FakeStore([entry()]),SimpleNamespace(complete=lambda *a:{'summary':'invalid','evidence_ids':['foreign']}))
    result=service.ask('导出手册')
    assert not result['generated']
    assert result['summary']!='invalid'
    assert result['steps'][0]['evidence_ids']==['one-s0']


def test_images_remain_bound_to_their_own_step():
    e=entry();e['data']['steps'][0]['asset_ids']=['a']
    picture={'id':'a','entry_id':'one','name':'截图','page_number':1,'metadata':{},'storage_path':'private','thumbnail_path':'private'}
    service=ManualService(FakeStore([e],[picture]))
    result=service.ask('导出手册')
    assert result['steps'][0]['images'][0]['asset_id']=='a'
    assert result['steps'][1]['images']==[]
    assert 'storage_path' not in result['steps'][0]['images'][0]


def test_cross_manual_image_cannot_be_saved():
    service=ManualService(FakeStore([entry(status='draft')]))
    with pytest.raises(ValueError,match='其他手册'):
        service.save_steps('one',[{'order':1,'instruction':'操作','asset_ids':['foreign']}])


def test_search_is_single_flight():
    service=ManualService(FakeStore([entry()]))
    service.lock.acquire()
    try:
        with pytest.raises(ValueError,match='另一条检索'): service.ask('导出手册')
    finally: service.lock.release()


def test_invalid_image_fails_with_clear_validation_error(tmp_path):
    with pytest.raises(ValueError,match='不是有效图片'):
        save_image(b'not a png',tmp_path,'file.png')


def test_source_only_does_not_call_configured_model(monkeypatch):
    monkeypatch.setattr(settings,'llm_api_key','test-only')
    model=SimpleNamespace(complete=lambda *args:pytest.fail('Source-only must not call model'))
    result=ManualService(FakeStore([entry()]),model).ask('导出手册',source_only=True)
    assert result['status']=='ok' and not result['generated']


@pytest.mark.parametrize('succeeds',[True,False])
def test_screenshot_recognition_records_explicit_approval_and_outcome(monkeypatch,tmp_path,succeeds):
    import app.manuals.service as module
    path=tmp_path/'safe.png';path.write_bytes(b'local-image-derivative')
    monkeypatch.setattr(module,'inside_data',lambda _:path)
    store=FakeStore([entry()]);records=[]
    store.asset=lambda _: {'id':'a','entry_id':'one','storage_path':str(path)}
    store.record_analysis=lambda aid,data:records.append(data)
    def complete(*args):
        if not succeeds: raise RuntimeError('unavailable')
        return {'visible_text':'导出','observations':'可见按钮','limitations':[]}
    service=ManualService(store,SimpleNamespace(complete=complete))
    if succeeds:
        assert service.understand('one','a')['status']=='succeeded'
    else:
        with pytest.raises(ValueError,match='原图片和文字已保留'):service.understand('one','a')
    assert records[0]['explicit_external_export_approved'] is True
    assert records[0]['status']=='processing'
    assert records[-1]['status']==('succeeded' if succeeds else 'failed')

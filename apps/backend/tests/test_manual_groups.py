import pytest
from copy import deepcopy
from types import SimpleNamespace

from app.manuals.groups import batches, suggest_groups, validate_groups


def test_heading_suggestions_require_confirmation_and_preserve_source_order():
    steps = [{'instruction': text} for text in ['第一章 入职', '提交材料', '第二章 请假', '填写申请']]
    groups = suggest_groups(steps, '手册')
    assert [(g['start'], g['end'], g['confirmed']) for g in groups] == [(0, 2, False), (2, 4, False)]
    assert validate_groups(groups, 4) == groups


def test_rejects_overlapping_and_missing_boundaries():
    with pytest.raises(ValueError):
        validate_groups([{'id': 'a', 'title': '流程', 'start': 0, 'end': 1},
                         {'id': 'b', 'title': '流程', 'start': 0, 'end': 2}], 2)


def test_batches_do_not_cross_procedure_boundaries():
    entry = {'title': '手册', 'data': {'steps': [{}] * 27, 'groups': [
        {'id': 'a', 'title': '入职', 'start': 0, 'end': 3},
        {'id': 'b', 'title': '请假', 'start': 3, 'end': 27}]}}
    assert [(b['start'], b['end']) for b in batches(entry)] == [(0, 3), (3, 23), (23, 27)]


def test_legacy_entries_preserve_fixed_batches():
    entry = {'title': '旧资料', 'data': {'steps': [{}] * 21}}
    assert [(b['start'], b['end']) for b in batches(entry)] == [(0, 20), (20, 21)]


def test_saving_groups_persists_child_ownership_and_both_image_associations():
    from app.manuals.service import ManualService
    class Store:
        def __init__(self):
            self.row={'id':'entry','title':'手册','status':'draft','source_name':None,'data':{}}
        def get(self, _): return deepcopy(self.row)
        def assets(self, _): return [{'id':'image1'},{'id':'image2'}]
        def update(self, _, **kwargs): self.row.update(deepcopy(kwargs)); return self.get(None)
        def chunks(self, _): return [{'id':f'child-{i}','chunk_level':'child','metadata':{'step_index':i}} for i in range(2)]
    class Repository:
        def store_document(self, **kwargs):
            self.payload=kwargs['payload']
            return SimpleNamespace(document_id='doc', document_version_id='version')
    store=Store(); service=ManualService(store=store); service.repository=Repository()
    steps=[{'order':1,'instruction':'入职','asset_ids':['image1'],'section_asset_ids':['image2'],'page':1},
           {'order':2,'instruction':'请假','asset_ids':[],'section_asset_ids':[],'page':2}]
    groups=[{'id':'a','title':'入职','start':0,'end':1,'confirmed':True},
            {'id':'b','title':'请假','start':1,'end':2,'confirmed':True}]
    result=service.save_steps('entry',steps,groups)
    assert result['data']['groups']==groups
    assert result['data']['steps'][0]['section_asset_ids']==['image2']
    assert result['data']['steps'][1]['evidence_id']=='child-1'
    assert [p['section_path'] for p in service.repository.payload['chunks']]==['入职','请假']
    assert [[c['metadata']['step_index'] for c in p['children']] for p in service.repository.payload['chunks']]==[[0],[1]]

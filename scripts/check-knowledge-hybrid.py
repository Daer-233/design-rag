"""Local, source-only acceptance of the deployed three-lane knowledge retriever."""
import json
from pathlib import Path
import sys
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/knowledge-hybrid-20261029'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    questions = [
        ('source', '图文操作条目演示中如何添加截图并发布？'),
        ('multiple', '请结合日常手册库使用手册和图文操作条目演示，说明新建、添加截图和发布资料的流程。'),
        ('missing', '财务付款审批额度上限和审批人是谁？')]
    report = []
    with httpx.Client(base_url='http://127.0.0.1:8060', headers={'X-Manual-Request':'1'},
                      trust_env=False, timeout=420) as client:
        entries = client.get('/api/v1/manuals/entries').json()
        assert all(e['index_status'] == 'indexed' for e in entries if e['status'] == 'published')
        allowed = {e['id'] for e in entries if e['status'] == 'published'}
        for name, question in questions:
            start = perf_counter()
            response = client.post('/api/v1/manuals/ask', json={'question':question,'source_only':True})
            response.raise_for_status()
            answer = response.json()
            (OUT / (name + '-response.json')).write_text(json.dumps(answer,ensure_ascii=False,indent=2),encoding='utf-8')
            metrics = answer['retrieval_metrics']
            record = {'case':name,'status':answer['status'],'mode':answer['retrieval_mode'],
                      'source_count':answer.get('source_count',0),'metrics':metrics,
                      'seconds':round(perf_counter()-start,3),'limitations':answer['limitations']}
            report.append(record)
            print(json.dumps(record,ensure_ascii=False),flush=True)
            assert answer['retrieval_mode'] == 'hybrid_rerank'
            assert 0 < metrics['rerank_input_count'] <= 8
            assert metrics['dense_count'] > 0 and metrics['sparse_count'] > 0
            if name == 'missing':
                assert answer['status'] == 'insufficient_evidence' and not answer['steps']
            else:
                assert answer['status'] == 'ok' and not answer['generated']
                assert metrics['keyword_count'] > 0
                assert {s['entry']['id'] for s in answer['sources']} <= allowed
                assert any(step['is_match'] for step in answer['steps'])
                if name == 'multiple': assert answer['source_count'] == 2
                for source in answer['sources']:
                    assert all(step['source']['entry_id']==source['entry']['id'] for step in source['steps'])
                    for step in source['steps']:
                        for image in step['images'] + step['section_images']:
                            image_response=client.get(image['thumbnail_url'])
                            assert image_response.status_code==200 and image_response.headers['content-type'].startswith('image/')
    result={'status':'passed','cases':report,'external_answer_model_calls':0,
            'note':'Existing self-authored acceptance fixtures only; not a production accuracy benchmark.'}
    (OUT/'local-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()

"""Live local integration acceptance. Uses source_only=True; no external models are called."""
from pathlib import Path
import json
import os
from time import perf_counter
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/manual-library-20261029'
sys.path.insert(0,str(ROOT/'apps/backend'))


def main():
    from dotenv import dotenv_values
    import psycopg
    source=dotenv_values(ROOT/'.env')
    original_dsn=source['POSTGRES_DSN'].replace('postgresql+psycopg://','postgresql://',1)
    with psycopg.connect(original_dsn,connect_timeout=5) as c:
        baseline=c.execute('SELECT (SELECT count(*) FROM projects),(SELECT count(*) FROM documents),(SELECT count(*) FROM chunks)').fetchone()
        forbidden=c.execute("SELECT has_table_privilege('manual_rag','public.documents','SELECT')").fetchone()[0]
        assert forbidden is False,'Manual account must not read design documents'
    facts=json.loads((OUT/'sample-facts.json').read_text(encoding='utf-8'))
    report={'authorship':facts['authorship'],'external_model_calls':0,'cases':[]}
    with httpx.Client(base_url='http://127.0.0.1:8060',headers={'X-Manual-Request':'1'},timeout=90) as client:
        def request(method,path,**kwargs):
            response=client.request(method,'/api/v1/manuals'+path,**kwargs)
            response.raise_for_status()
            return response.json()
        categories=request('GET','/categories')
        category=next((c for c in categories if c['name']=='验收示例'),None) or request('POST','/categories',json={'name':'验收示例'})
        entry=request('POST','/entries',json={'title':facts['title'],'category_id':category['id'],
                                            'product':'日常手册库 V1','version':'1.0'})
        with (OUT/'manual-library-v1-acceptance.pdf').open('rb') as f:
            request('POST',f"/entries/{entry['id']}/files",files={'file':('manual-library-v1-acceptance.pdf',f,'application/pdf')})
        parsed=request('POST',f"/entries/{entry['id']}/parse-local")
        assert parsed['processing_status']=='ready' and len(parsed['steps'])>=10
        assert len(parsed['assets'])>=1,'PDF extraction should retain its image assets'
        request('POST',f"/entries/{entry['id']}/publish")
        for fact in facts['facts']:
            question=f"{facts['title']}：如何{fact['topic']}？"
            started=perf_counter()
            result=request('POST','/ask',json={'question':question,'source_only':True})
            assert result['status']=='ok' and result['entry']['id']==entry['id']
            assert any(fact['topic'] in s['instruction'] and s['source']['page']==fact['page'] for s in result['steps'])
            report['cases'].append({'question':question,'expected_page':fact['page'],'topic':fact['topic'],
                'passed':True,'latency_ms':round((perf_counter()-started)*1000,2),'mode':result['retrieval_mode']})
        detail=request('GET',f"/entries/{entry['id']}")
        asset=detail['assets'][0]
        assert client.get(asset['preview_url']).status_code==200
        assert client.get(asset['preview_url'].split('?')[0]+'?access_token=invalid').status_code==403
        assert client.get(f"/api/v1/manuals/entries/{entry['id']}/source").content[:4]==b'%PDF'
        assert client.get('/api/v1/agent/runs').status_code==404
        assert client.post('/api/v1/manuals/categories',json={'name':'must-not-create'},headers={'X-Manual-Request':''}).status_code==403
        assert client.post('/api/v1/manuals/categories',json={'name':'must-not-create'},headers={'Origin':'https://example.com'}).status_code==403
        report['manual_entry_id']=entry['id']
        report['asset_count']=len(detail['assets'])
        report['boundary_checks']=['signed_images','invalid_signature_rejected','source_pdf',
            'no_agent_routes','mutation_header_required','foreign_origin_rejected','database_privilege_isolation']
    with psycopg.connect(original_dsn,connect_timeout=5) as c:
        final=c.execute('SELECT (SELECT count(*) FROM projects),(SELECT count(*) FROM documents),(SELECT count(*) FROM chunks)').fetchone()
    assert baseline==final,'Original design catalog was modified'
    report['design_counts_unchanged']=list(final)
    report['status']='passed'
    (OUT/'api-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','verified_questions':len(report['cases']),'asset_count':report['asset_count'],
                      'design_counts_unchanged':report['design_counts_unchanged']}))


if __name__=='__main__':main()

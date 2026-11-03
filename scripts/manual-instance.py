"""Provision/start the isolated manual library without modifying design-rag configuration."""
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'instances/manuals/.env'
sys.path.insert(0,str(ROOT/'apps/backend'))


def load():
    from dotenv import load_dotenv
    if not CONFIG.is_file(): raise RuntimeError('Run manual-instance.py init first')
    load_dotenv(CONFIG,override=True)
    os.environ['DESIGN_RAG_ENV_FILE']=str(CONFIG)
    from app.manuals.isolation import validate_isolation
    validate_isolation()


def initialize():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from dotenv import dotenv_values
    if not CONFIG.exists():
        source = dotenv_values(ROOT/'.env')
        dsn = str(source.get('POSTGRES_DSN') or '').replace('postgresql+psycopg://','postgresql://',1)
        connection_info = conninfo_to_dict(dsn)
        if connection_info.get('host','localhost') not in {'127.0.0.1','localhost','::1'}:
            raise RuntimeError('Provisioning requires the existing local PostgreSQL service')
        password = secrets.token_urlsafe(32)
        with psycopg.connect(dsn,autocommit=True,connect_timeout=5) as c:
            if c.execute("SELECT 1 FROM pg_database WHERE datname='manual_rag'").fetchone() or c.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname='manual_rag'").fetchone():
                raise RuntimeError('manual_rag database/role already exists without this instance config; refusing to overwrite')
            c.execute(sql.SQL('CREATE ROLE manual_rag LOGIN PASSWORD {}').format(sql.Literal(password)))
            c.execute('CREATE DATABASE manual_rag OWNER manual_rag')
        # Explicit allowlist only; never clone the original instance configuration.
        values = {'APP_NAME':'manual-rag','APP_HOST':'127.0.0.1','APP_PORT':'8060',
            'POSTGRES_DSN':make_conninfo(host=connection_info.get('host','127.0.0.1'),
                port=connection_info.get('port','5432'),dbname='manual_rag',user='manual_rag',password=password),
            'QDRANT_URL':source.get('QDRANT_URL') or 'http://127.0.0.1:6333',
            'QDRANT_COLLECTION':'manual_rag_chunks_v1','DATA_ROOT':str(ROOT/'data/manuals'),
            'VISION_ANALYSIS_DERIVATIVE_ROOT':str(ROOT/'data/manuals/vision-analysis-derivatives'),
            'DESIGN_RAG_ASSET_SIGNING_SECRET':secrets.token_urlsafe(48),
            'DESIGN_RAG_MCP_ENABLED':'false','AGENT_ENABLED':'false','ANSWER_API_ENABLED':'false',
            'AGENT_COOKIE_NAME':'manual_rag_anonymous_id','RETRIEVAL_RERANK_CANDIDATE_TOP_K':'8',
            'RETRIEVAL_EVIDENCE_TOP_K':'8'}
        for key in ['EMBEDDING_SERVICE_URL','RERANK_SERVICE_URL','EMBEDDING_MODEL_ID','EMBEDDING_MODEL_REVISION',
                    'LLM_BASE_URL','LLM_MODEL_ID','LLM_API_KEY','OPENAI_API_KEY','LLM_TIMEOUT_SECONDS',
                    'MINERU_API_TOKEN','MINERU_API_BASE_URL','SOFFICE_PATH','PDFTOPPM_PATH']:
            if source.get(key): values[key]=source[key]
        CONFIG.parent.mkdir(parents=True,exist_ok=True)
        # Single quotes preserve Windows paths and libpq DSNs; generated secrets contain no quotes.
        CONFIG.write_text('\n'.join(k+"='"+str(v).replace("'","\\'")+"'" for k,v in values.items())+'\n',encoding='utf-8')
    load()
    from app.db.migrations import apply_migrations
    from app.db.connection import connection
    from app.manuals.isolation import DATA
    from app.providers.vector.qdrant_provider import QdrantVectorProvider
    DATA.mkdir(parents=True,exist_ok=True)
    migrations = apply_migrations()
    with connection() as c:
        c.execute((ROOT/'apps/backend/app/manuals/schema.sql').read_text(encoding='utf-8'))
        c.commit()
    provider=QdrantVectorProvider()
    provider.ensure_collection(1024)
    from qdrant_client.http import models
    for field in ['document_version_id','manual_entry_id']:
        provider.client.create_payload_index(collection_name=provider.collection_name,field_name=field,
                                             field_schema=models.PayloadSchemaType.KEYWORD)
    print('Manual library initialized: database=manual_rag, collection=manual_rag_chunks_v1; migrations='+str(len(migrations)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','serve'])
    args=parser.parse_args()
    if args.command=='init': initialize()
    else:
        load()
        import uvicorn
        uvicorn.run('app.manual_main:app',host='127.0.0.1',port=8060,workers=1)


if __name__=='__main__': main()

"""Local-only manual library entry point; never imports app.api.dependencies or legacy Agent."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.manuals.isolation import ROOT, validate_isolation

validate_isolation()

from app.manuals.api import router, reconcile_cloud, service
from app.db.connection import connection


async def worker():
    while True:
        try: await asyncio.to_thread(reconcile_cloud)
        except Exception as error:
            logging.getLogger(__name__).warning('Manual reconciliation failed (%s)',type(error).__name__)
        await asyncio.sleep(4)


async def index_worker():
    while True:
        try:
            await asyncio.to_thread(service.index_next)
        except Exception as error:
            logging.getLogger(__name__).warning('Knowledge indexing failed (%s)',type(error).__name__)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    validate_isolation()
    with connection() as c:
        identity = c.execute('SELECT current_database() AS db,current_user AS owner').fetchone()
        if identity != {'db':'manual_rag','owner':'manual_rag'}:
            raise RuntimeError('Manual database identity mismatch')
        # Local processing is retryable; external submitted jobs are reconciled separately.
        c.execute("UPDATE manual_entries SET processing_status='failed',error_message='本地提取被中断，可重新提取' WHERE processing_status='processing'")
        c.execute("UPDATE manual_entries SET index_status='queued' WHERE index_status='indexing' OR (status='published' AND processing_status='ready' AND index_status IN ('pending','failed'))")
        c.execute("""UPDATE manual_assets SET metadata=jsonb_set(metadata,'{analysis,status}','"interrupted"'::jsonb)
                     WHERE metadata->'analysis'->>'status'='processing'""")
        c.commit()
    task = asyncio.create_task(worker())
    indexing = asyncio.create_task(index_worker())
    try: yield
    finally:
        task.cancel()
        indexing.cancel()
        with suppress(asyncio.CancelledError): await task
        with suppress(asyncio.CancelledError): await indexing


app = FastAPI(title='工作知识助手',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])


@app.middleware('http')
async def local_boundary(request:Request,call_next):
    origin = request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:8060','http://localhost:8060'}:
        return JSONResponse({'detail':'仅允许本机手册页面访问'},403)
    if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('X-Manual-Request')!='1':
        return JSONResponse({'detail':'缺少本机请求标识'},403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response


@app.exception_handler(ValueError)
async def invalid(request,error): return JSONResponse({'detail':str(error)},400)


@app.exception_handler(IntegrityError)
async def conflict(request,error): return JSONResponse({'detail':'名称重复或引用的分类不存在'},409)


@app.get('/health')
def health():
    validate_isolation()
    with connection() as c: c.execute('SELECT 1')
    return {'status':'ok','library':'manuals','database':'manual_rag','collection':'manual_rag_chunks_v1'}


@app.get('/')
def root(): return RedirectResponse('/knowledge/')


app.include_router(router)
app.mount('/knowledge',StaticFiles(directory=ROOT/'apps'/'manual-web',html=True),name='knowledge-web')
app.mount('/manuals',StaticFiles(directory=ROOT/'apps'/'manual-web',html=True),name='manual-web')

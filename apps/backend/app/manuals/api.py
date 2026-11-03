from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import settings
from app.db.connection import connection
from app.manuals.isolation import DATA, inside_data
from app.manuals.service import ManualService
from app.manuals.store import ManualStore
from app.manuals.langbot import KnowledgeRetrieval, retrieve
from app.services.image_asset_access import verify_asset_access

router = APIRouter(prefix='/api/v1/manuals')
service = ManualService()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Category(Strict):
    name: str = Field(min_length=1,max_length=120)

    @field_validator('name')
    @classmethod
    def trim(cls,v):
        if not v.strip(): raise ValueError('名称不能为空')
        return v.strip()


class Entry(Strict):
    title: str = Field(min_length=1,max_length=300)
    category_id: UUID | None = None
    product: str = Field(default='',max_length=200)
    version: str = Field(default='',max_length=120)
    family_key: str | None = Field(default=None,max_length=300)

    @field_validator('title')
    @classmethod
    def trim(cls,v):
        if not v.strip(): raise ValueError('标题不能为空')
        return v.strip()


class Step(Strict):
    order: int | None = Field(default=None,ge=1,le=10000)
    title: str = Field(default='',max_length=300)
    instruction: str = Field(min_length=1,max_length=20000)
    asset_ids: list[UUID] = Field(default_factory=list,max_length=20)
    section_asset_ids: list[UUID] = Field(default_factory=list,max_length=20)
    page: int | None = Field(default=None,ge=1)


class ProcedureGroup(Strict):
    id: str = Field(min_length=1,max_length=80)
    title: str = Field(min_length=1,max_length=300)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    confirmed: bool = False


class Steps(Strict):
    steps: list[Step] = Field(min_length=1,max_length=2000)
    groups: list[ProcedureGroup] | None = None


class Ask(Strict):
    question: str = Field(min_length=1,max_length=4000)
    category_id: UUID | None = None
    product: str | None = Field(default=None,max_length=200)
    version: str | None = Field(default=None,max_length=120)
    entry_id: UUID | None = None
    page: int = Field(default=1,ge=1,le=1000)
    context_page: int | None = Field(default=None,ge=1,le=1000)
    source_only: bool = True


class Approval(Strict):
    explicit_external_export_approved: Literal[True]


@router.get('/categories')
def categories(): return service.store.categories()


@router.post('/categories',status_code=201)
def create_category(payload:Category): return service.store.create_category(payload.name)


@router.get('/entries')
def entries(): return [service.public(e) for e in service.store.entries()]


@router.post('/entries',status_code=201)
def create_entry(payload:Entry): return service.public(service.store.create(payload),True)


@router.get('/entries/{entry_id}')
def get_entry(entry_id:UUID): return service.public(service.store.get(entry_id),True)


@router.put('/entries/{entry_id}/steps')
def save_steps(entry_id:UUID,payload:Steps):
    data=payload.model_dump(mode='json')
    return service.public(service.save_steps(entry_id,data['steps'],data['groups']),True)


@router.post('/entries/{entry_id}/publish')
def publish(entry_id:UUID):
    entry=service.store.publish(entry_id)
    if entry['index_status']!='indexed':
        entry=service.store.update(entry_id,index_status='queued')
    return service.public(entry,True)


@router.post('/entries/{entry_id}/files')
async def upload(entry_id:UUID,file:UploadFile=File(...)):
    entry = service.store.get(entry_id)
    if entry['status']!='draft': raise ValueError('请为已发布资料创建新版')
    name = Path((file.filename or 'file').replace('\\','/')).name
    suffix = Path(name).suffix.lower()
    if suffix not in {'.pdf','.doc','.docx','.ppt','.pptx','.png','.jpg','.jpeg','.webp'}:
        raise ValueError('支持 PDF、Word、PPT 和 PNG/JPG/WEBP 截图')
    is_image = suffix in {'.png','.jpg','.jpeg','.webp'}
    limit = 20*1024*1024 if is_image else 200*1024*1024
    target = DATA/'originals'/str(entry_id)/str(uuid4())/name
    target.parent.mkdir(parents=True,exist_ok=True)
    total = 0
    try:
        with target.open('wb') as out:
            while chunk := await file.read(1024*1024):
                total += len(chunk)
                if total>limit: raise ValueError('文件超出大小限制：截图 20MiB，文档 200MiB')
                out.write(chunk)
        if is_image:
            aid = await asyncio.to_thread(service.register_image,entry_id,target.read_bytes(),name)
            return service.asset_public(service.store.asset(aid))
        service.store.update(entry_id,source_path=str(target),source_name=name,processing_status='uploaded')
        return service.public(service.store.get(entry_id),True)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post('/entries/{entry_id}/parse-local')
def parse_local(entry_id:UUID): return service.public(service.parse_local(entry_id),True)


@router.post('/entries/{entry_id}/index',status_code=202)
def index(entry_id:UUID):
    entry=service.store.get(entry_id)
    if not entry['document_version_id']: raise ValueError('请先整理内容')
    service.store.update(entry_id,index_status='queued')
    return {'status':'queued','entry_id':entry_id}


@router.post('/ask')
def ask(payload:Ask):
    return service.ask(payload.question,payload.category_id,payload.product,payload.version,payload.entry_id,payload.page,payload.source_only,context_page=payload.context_page)


@router.post('/retrieve')
def retrieve_for_langbot(payload:KnowledgeRetrieval):
    return retrieve(service, payload)


@router.post('/entries/{entry_id}/assets/{asset_id}/understand')
def understand(entry_id:UUID,asset_id:UUID,payload:Approval): return service.understand(entry_id,asset_id)


@router.get('/assets/{asset_id}')
def asset(asset_id:UUID,access_token:str,variant:Literal['preview','thumbnail']='preview'):
    row = service.store.asset(asset_id)
    if not verify_asset_access(access_token,str(asset_id),str(row['entry_id'])):
        from fastapi import HTTPException
        raise HTTPException(403,'图片引用已失效，请重新查询')
    path = inside_data(row['thumbnail_path'] if variant=='thumbnail' else row['storage_path'])
    if not path.is_file():
        from fastapi import HTTPException
        raise HTTPException(404,'图片文件缺失；文字和来源仍可查看')
    return FileResponse(path,headers={'X-Content-Type-Options':'nosniff'})


@router.get('/entries/{entry_id}/source')
def source(entry_id:UUID):
    row = service.store.get(entry_id)
    if not row['source_path'] or not inside_data(row['source_path']).is_file():
        from fastapi import HTTPException
        raise HTTPException(404,'没有可预览的原文件，请查看图文条目')
    path = inside_data(row['source_path'])
    return FileResponse(path,filename=row['source_name'],
        content_disposition_type='inline' if path.suffix.lower()=='.pdf' else 'attachment',
        headers={'X-Content-Type-Options':'nosniff'})


def cloud_jobs():
    from app.clients.inference import ModelIdentity
    from app.providers.parser.factory import get_parser_provider
    from app.repositories.rag_repository import RagRepository
    from app.services.ingestion_job_service import IngestionJobService
    from app.services.mineru_result_normalizer import MinerUResultNormalizer

    class SourceOnlyIngestion:
        def ingest(self,payload):
            identity = ModelIdentity('embedding','modelscope',settings.embedding_model_id,
                                     settings.embedding_model_revision,None,{'identity_source':'configuration'})
            result = service.repository.store_document(payload=payload,embedding_model=identity,
                provider_name='qdrant',collection_name=settings.qdrant_collection)
            return {'project_id':result.project_id,'document_id':result.document_id,
                    'document_version_id':result.document_version_id,'index_status':'pending'}

    class ManualNormalizer(MinerUResultNormalizer):
        def _material_items_from_table(self,**kwargs): return []

    return IngestionJobService(service.repository,get_parser_provider(),SourceOnlyIngestion(),ManualNormalizer())


@router.post('/entries/{entry_id}/submit-mineru',status_code=202)
async def submit_mineru(entry_id:UUID,payload:Approval):
    entry = service.store.get(entry_id)
    if entry['status']!='draft' or not entry['source_path']: raise ValueError('需要已上传原文档的草稿')
    jobs = cloud_jobs()
    with inside_data(entry['source_path']).open('rb') as original:
        job = await jobs.create_from_upload(file=StarletteUploadFile(original,filename=entry['source_name']),
            project_external_key='manual-library',project_name='日常手册',document_external_key='manual-source-'+str(entry_id),
            access_scope_json='{}',document_metadata_json='{}',processing_profile='document')
    service.store.update(entry_id,ingestion_job_id=job['id'],processing_status='submitted',error_message=None)
    try: jobs.enqueue(str(job['id']))
    except Exception as error:
        service.store.update(entry_id,processing_status='failed',error_message=str(error)[:400])
        raise
    return {'status':'submitted','entry_id':entry_id}


def reconcile_cloud():
    from app.services.ingestion_reconciler import IngestionReconciler
    jobs = cloud_jobs()
    IngestionReconciler(jobs).run_once()
    for entry in service.store.entries():
        if entry['processing_status']!='submitted' or not entry['ingestion_job_id']: continue
        try:
            job = jobs.get(str(entry['ingestion_job_id']))
            if job['status']=='failed':
                service.store.update(entry['id'],processing_status='failed',error_message='MinerU 解析失败，可重新提交')
            elif job['document_version_id']:
                with connection() as c:
                    chunks = c.execute("""SELECT content,page_start FROM chunks
                        WHERE document_version_id=%s AND chunk_level='child' ORDER BY chunk_ordinal,id""",
                        (job['document_version_id'],)).fetchall()
                    images = c.execute('''SELECT a.page_number,d.storage_path,d.file_name FROM image_assets a
                        JOIN document_assets d ON d.id=a.document_asset_id WHERE a.document_version_id=%s''',
                        (job['document_version_id'],)).fetchall()
                by_page = {}
                for image in images:
                    try:
                        path = inside_data(image['storage_path'])
                        aid = service.register_image(entry['id'],path.read_bytes(),image['file_name'],image['page_number'])
                        by_page.setdefault(image['page_number'],[]).append(aid)
                    except (ValueError,OSError): continue
                from app.manuals.content import source_steps
                steps = source_steps([(r['content'],r['page_start'],by_page.get(r['page_start'],[])) for r in chunks])
                service.save_steps(entry['id'],steps)
        except Exception:
            service.store.update(entry['id'],processing_status='failed',error_message='解析产物整理失败，原件已保留，可重试')

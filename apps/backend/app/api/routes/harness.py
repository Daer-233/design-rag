from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.repositories.harness_repository import HarnessRepository
router=APIRouter(prefix="/api/v1/admin",tags=["harness"])
class RunRequest(BaseModel):
    project_id: UUID|None=None
    run_type: str=Field(pattern="^(assets|rag|mcp|ragas|all)$")
    test_set_version: str="v1"
class GenerationRequest(BaseModel):
    project_id: UUID
    prompt: str=Field(min_length=1,max_length=16000)
    prompt_version: str="v1"
    reference_asset_ids: list[UUID]=[]
    citation_ids: list[str]=[]
    provider: str="chatgpt_browser"
def repo(): return HarnessRepository()
@router.get("/harness/projects")
def projects(r=Depends(repo)): return {"items":r.list_projects()}
@router.get("/harness/test-cases")
def cases(): return {"version":"v1","items":[]}
@router.post("/harness/runs",status_code=201)
def create_run(payload:RunRequest,r=Depends(repo)): return r.create_run(str(payload.project_id) if payload.project_id else None,payload.run_type,payload.test_set_version)
@router.get("/harness/runs")
def list_runs(project_id:UUID|None=None,r=Depends(repo)): return {"items":r.list_runs(str(project_id) if project_id else None)}
@router.get("/harness/runs/{run_id}")
def get_run(run_id:UUID,r=Depends(repo)):
    out=r.get_run(str(run_id))
    if not out: raise HTTPException(404,"Harness run not found")
    return out
@router.post("/harness/runs/{run_id}/cancel")
def cancel_run(run_id:UUID,r=Depends(repo)): return r.get_run(str(run_id))
@router.post("/image-generation/jobs",status_code=201)
def create_generation(payload:GenerationRequest,r=Depends(repo)): return r.create_generation(payload.model_dump(mode="json"))
@router.get("/image-generation/jobs/{job_id}")
def get_generation(job_id:UUID,r=Depends(repo)):
    out=r.get_generation(str(job_id))
    if not out: raise HTTPException(404,"Image generation job not found")
    return out
@router.post("/image-generation/jobs/{job_id}/confirm")
def confirm_generation(job_id:UUID,r=Depends(repo)):
    current = r.get_generation(str(job_id))
    if not current: raise HTTPException(404,"Image generation job not found")
    if current["status"] not in {"awaiting_confirmation", "failed"}:
        raise HTTPException(409,"Image generation job is not awaiting confirmation")
    r.set_generation_status(str(job_id),"confirmed")
    return r.get_generation(str(job_id))
@router.post("/image-generation/jobs/{job_id}/cancel")
def cancel_generation(job_id:UUID,r=Depends(repo)): r.set_generation_status(str(job_id),"cancelled"); return r.get_generation(str(job_id))

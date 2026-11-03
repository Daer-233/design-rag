from __future__ import annotations
from uuid import UUID, uuid4
from psycopg.types.json import Jsonb
from app.db.connection import connection

class HarnessRepository:
    def list_projects(self):
        with connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,name,external_key FROM projects WHERE COALESCE(is_hidden,FALSE)=FALSE ORDER BY name").fetchall()]
    def create_run(self, project_id, run_type, version="v1"):
        run_id=uuid4()
        with connection() as conn:
            conn.execute("INSERT INTO harness_runs(id,project_id,run_type,test_set_version) VALUES(%s,%s,%s,%s)",(run_id,UUID(project_id) if project_id else None,run_type,version)); conn.commit()
        return self.get_run(str(run_id))
    def list_runs(self, project_id=None):
        with connection() as conn:
            rows=conn.execute("SELECT r.*,p.name AS project_name FROM harness_runs r LEFT JOIN projects p ON p.id=r.project_id WHERE (%s IS NULL OR r.project_id=%s) ORDER BY r.created_at DESC LIMIT 100",(UUID(project_id) if project_id else None,UUID(project_id) if project_id else None)).fetchall()
        return [dict(x) for x in rows]
    def get_run(self, run_id):
        with connection() as conn:
            row=conn.execute("SELECT r.*,p.name AS project_name FROM harness_runs r LEFT JOIN projects p ON p.id=r.project_id WHERE r.id=%s",(UUID(run_id),)).fetchone()
            if not row:return None
            results=conn.execute("SELECT * FROM harness_results WHERE run_id=%s ORDER BY created_at",(UUID(run_id),)).fetchall()
        out=dict(row); out['results']=[dict(x) for x in results]; return out
    def create_generation(self,payload):
        job_id=uuid4()
        with connection() as conn:
            conn.execute("INSERT INTO image_generation_jobs(id,project_id,prompt,prompt_version,reference_asset_ids,citation_ids,provider) VALUES(%s,%s,%s,%s,%s,%s,%s)",(job_id,UUID(payload['project_id']),payload['prompt'],payload.get('prompt_version','v1'),Jsonb(payload.get('reference_asset_ids',[])),Jsonb(payload.get('citation_ids',[])),payload.get('provider','chatgpt_browser'))); conn.commit()
        return self.get_generation(str(job_id))
    def get_generation(self, job_id):
        with connection() as conn:
            row=conn.execute("SELECT j.*,p.name AS project_name FROM image_generation_jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=%s",(UUID(job_id),)).fetchone()
        return dict(row) if row else None
    def set_generation_status(self,job_id,status):
        with connection() as conn:
            conn.execute("UPDATE image_generation_jobs SET status=%s,confirmed_at=CASE WHEN %s='confirmed' THEN NOW() ELSE confirmed_at END,completed_at=CASE WHEN %s IN('failed','cancelled','succeeded') THEN NOW() ELSE completed_at END WHERE id=%s",(status,status,status,UUID(job_id))); conn.commit()

    def list_confirmed_generation_jobs(self, limit=5):
        with connection() as conn:
            rows = conn.execute("SELECT id FROM image_generation_jobs WHERE status='confirmed' ORDER BY confirmed_at NULLS LAST, created_at LIMIT %s", (limit,)).fetchall()
        return [str(row['id']) for row in rows]

    def claim_generation_job(self, job_id):
        with connection() as conn:
            row = conn.execute("UPDATE image_generation_jobs SET status='running' WHERE id=%s AND status='confirmed' RETURNING *", (UUID(job_id),)).fetchone()
            conn.commit()
        return dict(row) if row else None

    def finish_generation_job(self, job_id, status, error=None):
        self.set_generation_status(job_id, status)
        with connection() as conn:
            conn.execute("UPDATE image_generation_jobs SET error_message=%s WHERE id=%s", (error, UUID(job_id)))
            conn.commit()

    def add_generation_output(self, job_id, job, output):
        from uuid import uuid4
        output_id = uuid4()
        with connection() as conn:
            conn.execute("INSERT INTO image_generation_outputs (id,job_id,project_id,asset_type,storage_path,content_sha256,content_type,size_bytes,width,height) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (output_id, UUID(job_id), job['project_id'], output.get('asset_type','ai_concept_image'), output['output_path'], output['sha256'], output['content_type'], output['size_bytes'], output.get('width'), output.get('height')))
            conn.execute("UPDATE image_generation_jobs SET output_asset_ids = output_asset_ids || %s::jsonb WHERE id=%s", (Jsonb([str(output_id)]), UUID(job_id)))
            conn.commit()
        return str(output_id)

    def get_generation(self, job_id):
        with connection() as conn:
            row=conn.execute("SELECT j.*,p.name AS project_name FROM image_generation_jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=%s",(UUID(job_id),)).fetchone()
            if not row: return None
            outputs=conn.execute("SELECT * FROM image_generation_outputs WHERE job_id=%s ORDER BY created_at",(UUID(job_id),)).fetchall()
        data=dict(row); data['outputs']=[dict(x) for x in outputs]; return data

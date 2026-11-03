from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connection


class ManualStore:
    def categories(self):
        with connection() as c:
            return c.execute('SELECT id,name FROM manual_categories ORDER BY name').fetchall()

    def create_category(self, name):
        with connection() as c:
            row = c.execute('INSERT INTO manual_categories(id,name) VALUES(%s,%s) RETURNING *',
                            (uuid4(), name.strip())).fetchone()
            c.commit()
            return row

    def entries(self):
        with connection() as c:
            return c.execute('SELECT * FROM manual_entries ORDER BY updated_at DESC,id').fetchall()

    def get(self, entry_id):
        with connection() as c:
            row = c.execute('SELECT * FROM manual_entries WHERE id=%s', (entry_id,)).fetchone()
            if not row:
                raise ValueError('手册不存在')
            return row

    def create(self, payload):
        with connection() as c:
            row = c.execute('''INSERT INTO manual_entries(id,title,category_id,product,version,family_key,data)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (uuid4(), payload.title, payload.category_id, payload.product.strip(), payload.version.strip(),
                 payload.family_key or payload.title.strip().casefold(), Jsonb({'steps': []}))).fetchone()
            c.commit()
            return row

    def update(self, entry_id, **fields):
        allowed = {'title','product','version','data','source_path','source_name','ingestion_job_id',
                   'processing_status','error_message','document_id','document_version_id','index_status'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid update fields')
        params = [Jsonb(v) if k == 'data' else v for k, v in fields.items()]
        with connection() as c:
            c.execute('UPDATE manual_entries SET '+','.join(k+'=%s' for k in fields)+
                      ',updated_at=now() WHERE id=%s', (*params, entry_id))
            c.commit()
        return self.get(entry_id)

    def publish(self, entry_id):
        with connection() as c:
            # Serialize publication of all versions to keep family replacement atomic.
            c.execute("SELECT pg_advisory_xact_lock(8060)")
            row = c.execute('SELECT * FROM manual_entries WHERE id=%s FOR UPDATE', (entry_id,)).fetchone()
            if not row or row['processing_status'] != 'ready' or not row['document_version_id']:
                raise ValueError('请先完成内容整理，再发布')
            if row['data'].get('groups') is not None and any(not g.get('confirmed') for g in row['data']['groups']):
                raise ValueError('请先检查并确认所有流程分块')
            c.execute("""UPDATE manual_entries SET status='superseded',updated_at=now()
                         WHERE family_key=%s AND product=%s AND version=%s AND id<>%s AND status='published'""",
                      (row['family_key'],row['product'],row['version'],entry_id))
            c.execute("UPDATE manual_entries SET status='published',updated_at=now() WHERE id=%s", (entry_id,))
            c.commit()
        return self.get(entry_id)

    def assets(self, entry_id):
        with connection() as c:
            return c.execute('SELECT * FROM manual_assets WHERE entry_id=%s ORDER BY created_at,id', (entry_id,)).fetchall()

    def asset(self, asset_id):
        with connection() as c:
            row = c.execute('SELECT * FROM manual_assets WHERE id=%s', (asset_id,)).fetchone()
            if not row:
                raise ValueError('图片不存在')
            return row

    def add_asset(self, entry_id, name, storage_path, thumbnail_path, page=None, metadata=None):
        with connection() as c:
            row = c.execute('''INSERT INTO manual_assets(id,entry_id,name,storage_path,thumbnail_path,page_number,metadata)
                               VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                            (uuid4(),entry_id,name,str(storage_path),str(thumbnail_path),page,Jsonb(metadata or {}))).fetchone()
            c.commit()
            return row

    def record_analysis(self, asset_id, analysis):
        with connection() as c:
            c.execute("""UPDATE manual_assets SET metadata=jsonb_set(metadata,'{analysis_history}',
                         COALESCE(metadata->'analysis_history','[]'::jsonb) ||
                         CASE WHEN metadata ? 'analysis' THEN jsonb_build_array(metadata->'analysis') ELSE '[]'::jsonb END)
                         || %s WHERE id=%s""",
                      (Jsonb({'analysis':analysis}), asset_id))
            c.commit()

    def chunks(self, entry):
        with connection() as c:
            return c.execute('''SELECT id,content,chunk_level,parent_chunk_id,chunk_type,metadata,
                                       page_start,page_end,section_path,source_locator
                                FROM chunks WHERE document_version_id=%s ORDER BY chunk_ordinal,id''',
                             (entry['document_version_id'],)).fetchall()

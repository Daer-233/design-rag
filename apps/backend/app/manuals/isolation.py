from pathlib import Path
from psycopg.conninfo import conninfo_to_dict
from urllib.parse import urlsplit

from app.core.config import settings
from app.db.connection import normalized_postgres_dsn

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / 'data' / 'manuals'


def validate_isolation():
    dsn = conninfo_to_dict(normalized_postgres_dsn(settings.postgres_dsn))
    if (dsn.get('dbname') != 'manual_rag' or dsn.get('user') != 'manual_rag'
            or dsn.get('host', 'localhost') not in {'localhost','127.0.0.1','::1'}
            or settings.qdrant_collection != 'manual_rag_chunks_v1'
            or urlsplit(settings.qdrant_url).hostname not in {'localhost','127.0.0.1','::1'}
            or settings.data_root.resolve() != DATA.resolve()
            or settings.vision_analysis_derivative_root.resolve() != (DATA / 'vision-analysis-derivatives').resolve()
            or not settings.design_rag_asset_signing_secret):
        raise RuntimeError('Manual instance isolation check failed; refusing to access another library')
    return dsn


def inside_data(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(DATA.resolve()):
        raise ValueError('文件不属于手册库')
    return resolved

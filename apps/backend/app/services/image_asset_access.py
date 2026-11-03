from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import settings

_PROCESS_SECRET = secrets.token_bytes(32)


def _secret() -> bytes:
    configured = settings.design_rag_asset_signing_secret or settings.design_rag_api_token
    return str(configured).encode("utf-8") if configured else _PROCESS_SECRET


def sign_asset_access(
    asset_id: str,
    project_id: str | None = None,
    *,
    run_id: str | None = None,
    client_id: str | None = None,
    ttl_seconds: int = 3600,
) -> str:
    payload = {
        "asset_id": str(asset_id),
        "project_id": str(project_id) if project_id else None,
        "run_id": str(run_id) if run_id else None,
        "client_id": str(client_id) if client_id else None,
        "exp": int(time.time()) + max(30, int(ttl_seconds)),
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).digest()
    return raw + "." + base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


def verify_asset_access(
    token: str,
    asset_id: str,
    project_id: str | None = None,
    *,
    run_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        raw, supplied = str(token).split(".", 1)
        expected = hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).digest()
        got = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
        if not hmac.compare_digest(expected, got):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        if payload.get("asset_id") != str(asset_id) or int(payload.get("exp", 0)) < int(time.time()):
            return None
        if project_id and payload.get("project_id") != str(project_id):
            return None
        if run_id and payload.get("run_id") != str(run_id):
            return None
        if client_id and payload.get("client_id") != str(client_id):
            return None
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        return None

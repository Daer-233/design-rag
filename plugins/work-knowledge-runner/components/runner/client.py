"""Read-only client for the isolated local knowledge service."""
from __future__ import annotations

from urllib.parse import urlsplit, parse_qs
from uuid import UUID

import httpx


class KnowledgeError(Exception):
    """Safe error message suitable for a chat reply."""


class KnowledgeClient:
    def __init__(self, config, transport=None):
        raw = str(config.get('service_url') or 'http://host.docker.internal:8060').rstrip('/')
        parsed = urlsplit(raw)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', 'host.docker.internal'}
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise KnowledgeError('知识服务地址必须是本机 HTTP 地址，不能包含路径、账号或查询参数。')
        try:
            port = parsed.port or 80
            timeout = max(5, min(300, int(config.get('timeout_seconds', 120))))
        except (ValueError, TypeError) as error:
            raise KnowledgeError('知识服务端口或超时配置无效。') from error
        self.http = httpx.AsyncClient(base_url=raw, timeout=timeout, trust_env=False,
                                     follow_redirects=False, transport=transport,
                                     headers={'Host': f'127.0.0.1:{port}', 'X-Manual-Request': '1'})

    async def __aenter__(self):
        await self.http.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.http.__aexit__(*args)

    async def ask(self, payload):
        try:
            health = await self.http.get('/health')
            health.raise_for_status()
            identity = health.json()
            if (identity.get('library') != 'manuals' or identity.get('database') != 'manual_rag'
                    or identity.get('collection') != 'manual_rag_chunks_v1'):
                raise KnowledgeError('服务身份不匹配，已停止查询。请确认连接的是独立工作知识库。')
            result = await self.http.post('/api/v1/manuals/ask', json={**payload, 'source_only': True})
            if result.status_code == 400:
                detail = result.json().get('detail', '')
                if '另一条检索' in str(detail):
                    raise KnowledgeError('知识库正在处理另一条查询，请稍后重试。')
                raise KnowledgeError('当前查询无法处理，请检查版本、资料状态或分页。')
            result.raise_for_status()
            answer = result.json()
            if answer.get('status') not in {'ok', 'choose_version', 'insufficient_evidence'}:
                raise ValueError('Unknown answer status')
            return answer
        except httpx.TimeoutException as error:
            raise KnowledgeError('知识库查询超时，未生成替代答案；请稍后重试。') from error
        except httpx.HTTPError as error:
            raise KnowledgeError('暂时无法连接工作知识库，请检查本机知识服务是否已启动。') from error
        except (ValueError, TypeError, AttributeError) as error:
            raise KnowledgeError('知识服务响应异常，未生成替代答案。') from error

    async def image(self, asset):
        """Fetch only a signed thumbnail from this exact service; never follow redirects."""
        try:
            aid = str(UUID(str(asset['asset_id'])))
            raw = asset['thumbnail_url']
            parts = urlsplit(raw)
            query = parse_qs(parts.query)
            if (parts.scheme or parts.netloc or parts.fragment
                    or parts.path != f'/api/v1/manuals/assets/{aid}'
                    or set(query) != {'variant', 'access_token'}
                    or query['variant'] != ['thumbnail'] or len(query['access_token']) != 1):
                return None
            async with self.http.stream('GET', raw) as response:
                mime = response.headers.get('content-type', '').split(';')[0]
                if response.status_code != 200 or mime not in {'image/png', 'image/jpeg', 'image/webp'}:
                    return None
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        return None
                    chunks.append(chunk)
            binary = b''.join(chunks)
            valid = ((mime == 'image/png' and binary.startswith(b'\x89PNG\r\n\x1a\n'))
                     or (mime == 'image/jpeg' and binary.startswith(b'\xff\xd8\xff'))
                     or (mime == 'image/webp' and binary.startswith(b'RIFF') and binary[8:12] == b'WEBP'))
            return (mime, binary) if valid else None
        except (KeyError, ValueError, TypeError, httpx.HTTPError):
            return None

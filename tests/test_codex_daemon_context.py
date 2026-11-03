"""Regression for invocation ownership on an official CodexAgent Daemon socket."""
import asyncio
from contextvars import copy_context
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'data/manuals/runtime/langbot-codex'
sys.path[:0] = [str(RUNTIME / 'sdk'), str(RUNTIME / 'official-plugin')]
from pkg import daemon_relay
from langbot_plugin.api.proxies.invocation import bind_invocation, current_invocation

spec = spec_from_file_location('context_patch', ROOT / 'scripts/patch-langbot-codex-context.py')
patch = module_from_spec(spec); spec.loader.exec_module(patch)


@pytest.mark.parametrize('revoke_job', [False, True])
def test_reused_socket_uses_current_job_authority_and_honors_revocation(monkeypatch, revoke_job):
    # Execute the exact patched source against the installed SDK contracts.
    source = patch.patch_source((RUNTIME / 'official-plugin/pkg/daemon_relay.py').read_text(encoding='utf-8'))
    namespace = {'__name__': 'local_context_test'}
    exec(compile(source, 'patched-daemon-relay', 'exec'), namespace)
    handler = object()
    async def invoke(tools, payload):
        authority = current_invocation(handler)
        return {'owner': authority.config['owner']}
    namespace['handle_agent_runtime_mcp_payload'] = invoke

    async def scenario():
        hub = namespace['AgentRuntimeDaemonHub']()
        hub._server = object()
        owner = {'daemon_id': 'test', 'active_jobs': set(), 'last_seen_at': 0}
        hub._connections['test'] = owner
        started = asyncio.Event(); sent = []
        async def send(connection, message):
            sent.append(message)
            if message['type'] == 'run.start': started.set()
        hub._send = send
        with bind_invocation(handler, config={'owner': 'old-run'}):
            old_socket_context = copy_context()
        with bind_invocation(handler, config={'owner': 'current-run'}):
            stream = hub.run_job(daemon_id='test', payload={}, tools=object(), timeout=5)
            consumer = asyncio.create_task(anext(stream))
            await started.wait()
            session = next(iter(hub._jobs.values()))
            if revoke_job: current_invocation(handler).active = False
            request = json.dumps({'type': 'mcp.request', 'job_id': session['job_id'],
                                  'request_id': 'mcp-1', 'payload': {}})
            await asyncio.create_task(hub._handle_message(owner, request), context=old_socket_context)
            response = next(m for m in sent if m['type'] == 'mcp.response')['payload']
            if revoke_job:
                assert 'ended' in response['error']['message']
            else:
                assert response == {'owner': 'current-run'}
            await hub._handle_message(owner, json.dumps({'type': 'run.finished', 'job_id': session['job_id']}))
            with pytest.raises(StopAsyncIteration): await consumer
            assert not hub._jobs and not owner['active_jobs']
    asyncio.run(scenario())

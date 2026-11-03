"""Build/install a minimal, auditable local fix for CodexAgent's reused Daemon socket."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import httpx
import yaml

from importlib.util import spec_from_file_location, module_from_spec

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts/langbot-work-knowledge-20261029'
VERSION = '0.1.20+local.knowledge.1'


def patch_source(text):
    edits = [
        ('import contextlib\n', 'import contextlib\nimport contextvars\n'),
        ('            tools=tools,\n            done=asyncio.Event(),',
         '            tools=tools,\n            invocation_context=contextvars.copy_context(),\n            done=asyncio.Event(),'),
        ('                    payload = await handle_agent_runtime_mcp_payload(tools, message.get("payload"))',
         '                    # The socket predates later runs. Use the live job authority,\n'
         '                    # retaining SDK revocation checks and per-run isolation.\n'
         '                    payload = await asyncio.create_task(\n'
         '                        handle_agent_runtime_mcp_payload(tools, message.get("payload")),\n'
         '                        context=session["invocation_context"].copy(),\n'
         '                    )'),
    ]
    for old, new in edits:
        if text.count(old) != 1:
            raise RuntimeError('Official CodexAgent source differs; refusing an unreviewed patch.')
        text = text.replace(old, new)
    return text


def build():
    runtime = ROOT / 'data/manuals/runtime/langbot-codex'
    stage = runtime / ('patch-build-' + uuid4().hex)
    stage.mkdir(parents=True)
    discover = "from pathlib import Path; import yaml; print(next(str(p.parent) for p in Path('/app/data/plugin-runtime/artifacts/sha256').glob('*/code/manifest.yaml') if yaml.safe_load(p.read_text()).get('metadata',{}).get('name')=='CodexAgent' and yaml.safe_load(p.read_text()).get('metadata',{}).get('version')=='0.1.20'))"
    source = subprocess.check_output(['docker', 'exec', 'langbot_plugin_runtime', '/app/.venv/bin/python', '-c', discover], text=True).strip()
    if not source.startswith('/app/data/plugin-runtime/artifacts/sha256/'):
        raise RuntimeError('Unexpected source artifact.')
    subprocess.run(['docker', 'cp', f'langbot_plugin_runtime:{source}/.', str(stage)], check=True)
    target = stage / 'pkg/daemon_relay.py'
    before = target.read_text(encoding='utf-8')
    after = patch_source(before)
    target.write_text(after, encoding='utf-8')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                   fromfile='CodexAgent-0.1.20/pkg/daemon_relay.py', tofile='local/pkg/daemon_relay.py'))
    patch = ROOT / 'patches/langbot-codex-daemon-context.patch'
    patch.parent.mkdir(exist_ok=True)
    patch.write_text(diff, encoding='utf-8')
    manifest = yaml.safe_load((stage / 'manifest.yaml').read_text(encoding='utf-8'))
    manifest['metadata']['version'] = VERSION
    manifest['metadata']['description']['zh_Hans'] = '本地修订：修复 Daemon 连续任务的调用上下文；保留 SDK 权限撤销和任务隔离。'
    (stage / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding='utf-8')
    (stage / 'LOCAL_PATCH.md').write_text(
        '# Local CodexAgent revision\n\nBased on the installed upstream 0.1.20 artifact. '
        'This package is a local revision, not an official or certified release.\n\n'
        'The only runtime change captures the active Context at job dispatch and uses a copy '
        'for each MCP request belonging to that job. Completed invocation authorities remain revoked. '
        'No authentication or permission checks are removed. Original licenses are retained.\n', encoding='utf-8')
    package = ARTIFACTS / f'langbot-team-CodexAgent-{VERSION}.lbpkg'
    with ZipFile(package, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob('*')):
            if path.is_file() and not any(x in path.parts for x in ('__pycache__', '.git', '.pytest_cache')) and path.suffix not in {'.pyc', '.lbpkg'} and path.name != '.env':
                archive.write(path, path.relative_to(stage).as_posix())
    report = {'package': str(package), 'stage': str(stage), 'base_version': '0.1.20', 'version': VERSION,
              'source_artifact': source, 'original_relay_sha256': hashlib.sha256(before.encode()).hexdigest(),
              'patched_relay_sha256': hashlib.sha256(after.encode()).hexdigest()}
    (ARTIFACTS / 'codex-patch-build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def install():
    spec = spec_from_file_location('knowledge_connection', ROOT / 'scripts/connect-langbot-knowledge.py')
    module = module_from_spec(spec); spec.loader.exec_module(module)
    report = json.loads((ARTIFACTS / 'codex-patch-build.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://127.0.0.1:5300', headers={'X-API-Key': module.credential()}, trust_env=False, timeout=90) as client:
        with open(report['package'], 'rb') as handle:
            result = module.data(client.post('/api/v1/plugins/install/local', files={'file': (Path(report['package']).name, handle, 'application/zip')}))
    return {'status': 'submitted', 'task_id': result.get('task_id'), 'version': VERSION}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'install'])
    args = parser.parse_args()
    print(json.dumps(build() if args.command == 'build' else install(), ensure_ascii=False, indent=2))

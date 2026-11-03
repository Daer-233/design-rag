"""Launch the unmodified installed CodexAgent daemon with private local settings."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'data/manuals/runtime/langbot-codex'


async def main():
    # The official plugin sources and its license are copied from the installed
    # artifact into this ignored directory; no credentials enter command arguments.
    sys.path[:0] = [str(RUNTIME / 'sdk'), str(RUNTIME / 'official-plugin')]
    from pkg.native_cli import NativeCodexDaemon
    shared = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    seed = RUNTIME / 'codex-auth-home'
    seed.mkdir(exist_ok=True)
    auth = shared / 'auth.json'
    if not auth.is_file():
        raise RuntimeError('Local Codex authentication is missing. Sign in to Codex first.')
    shutil.copy2(auth, seed / 'auth.json')
    source_config = shared / 'config.toml'
    raw = source_config.read_text(encoding='utf-8-sig') if source_config.is_file() else ''
    sections = re.split(r'(?m)(?=^\[)', raw)
    filtered = ''.join(part for part in sections if not re.match(r'\[mcp_servers(?:\.|\])', part))
    if tomllib.loads(filtered).get('mcp_servers'):
        raise RuntimeError('Unable to isolate the Codex management MCP configuration.')
    (seed / 'config.toml').write_text(filtered, encoding='utf-8')
    (seed / 'sessions').mkdir(exist_ok=True)
    # This private seed supplies authentication/model preferences, while each job
    # receives only its Host-authorized MCP bridge and fresh per-run Codex home.
    os.environ['CODEX_HOME'] = str(seed)
    os.environ.pop('LANGBOT_API_KEY', None)
    config = json.loads((RUNTIME / 'daemon.private.json').read_text(encoding='utf-8'))
    daemon = NativeCodexDaemon(url=config['url'], daemon_id=config['daemon_id'], token=config['token'], reconnect_delay=5)
    await daemon.run_forever()


if __name__ == '__main__':
    with suppress(KeyboardInterrupt):
        asyncio.run(main())

"""Codex metadata discovery uses a stub CLI, never the user's login or MCP servers."""
from decompnet.agents import _models
from decompnet.agents.codex import Codex


def test_catalog_handshake_pagination_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'config.toml').write_text('[mcp_servers.example]\ncommand="must-not-run"')
    script = tmp_path / 'codex'
    script.write_text('''#!/usr/bin/env python3
import json,sys
assert sys.argv[1:] == ['-c', 'mcp_servers.example.enabled=false', 'app-server']
initialized=False
for line in sys.stdin:
    m=json.loads(line)
    if m['method']=='initialize':
        result={}
    elif m['method']=='initialized':
        initialized=True
        continue
    else:
        assert initialized and m['method']=='model/list'
        assert m['params']['includeHidden'] is False
        page=m['params'].get('cursor')
        result={'data':[{'id':'catalog-id','model':'example-model' if not page else 'second-model',
                        'displayName':'Example model','isDefault':not page,
                        'supportedReasoningEfforts':[{'reasoningEffort':'high'}],
                        'defaultReasoningEffort':'high'}], 'nextCursor':None if page else 'next'}
    print(json.dumps({'id':m['id'],'result':result}),flush=True)
''')
    script.chmod(0o755)
    agent = Codex()
    agent.forget_models()
    try:
        models = agent.models(str(script))
        assert [m['id'] for m in models] == ['example-model', 'second-model']
        assert models[0]['efforts'] == ['high']
        assert models[0]['note'] == 'Codex default'
        assert models[0]['default_effort'] == 'high'
        assert agent.models('/missing', run=False) == models
        assert agent.models('/missing') == models
        agent.forget_models()
        assert agent.models('/missing') == []
    finally:
        _models.pop('codex', None)


def test_timeout_and_failed_discovery_keep_cached_catalog(monkeypatch):
    import queue
    agent = Codex()
    previous = [{'id': 'previous'}]
    _models['codex'] = (0, previous)
    monkeypatch.setattr(agent, '_discover_models', lambda path: (_ for _ in ()).throw(queue.Empty()))
    try:
        assert agent.models('stub') == previous
    finally:
        agent.forget_models()


def test_unresponsive_cli_is_terminated(tmp_path, monkeypatch):
    import time

    from decompnet.agents import codex

    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(codex, 'MODELS_TIMEOUT', .1)
    script = tmp_path / 'codex'
    script.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
    script.chmod(0o755)
    agent = Codex()
    agent.forget_models()
    started = time.monotonic()
    try:
        assert agent.models(str(script)) == []
        assert time.monotonic() - started < 5
    finally:
        agent.forget_models()


def test_discovered_ultra_effort_reaches_session_command(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    cmd, _ = Codex().session_command('codex', tmp_path, 'http://127.0.0.1/mcp', 'test', 'example#ultra')
    assert 'model_reasoning_effort="ultra"' in cmd

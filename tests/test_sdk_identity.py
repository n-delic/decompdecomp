"""SDK provenance affects queue order, never matching progress or project settings."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_identity import identify


@pytest.fixture(autouse=True)
def _only_indexed_libraries(monkeypatch):
    """These tests are about the libraries the user indexes; the N64's built-in table (test_sdk_builtin.py) stays out."""
    from decompnet import consoles
    monkeypatch.setattr(consoles.get("n64"), "sdk_table", "")


@pytest.mark.parametrize('strategy', ['easiest', 'closest', 'finish'])
def test_sdk_queue_follows_game_code_but_explicit_requests_work(tmp_path, strategy):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = json.dumps({'kind': 'rom'})
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Demo', 'mips', 1, str(tmp_path), cfg)).lastrowid
        for name, insns, meta in [('library', 8, {'naming': {'method': 'sdk_signature', 'name': 'library',
                                                          'source_corpora': ['Example SDK']}}),
                                 ('game', 200, {}), ('osFamiliarName', 300, {})]:
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,meta) VALUES(?,?,?,?,?)',
                       (pid, name, insns * 4, insns, json.dumps(meta)))
    agents = [net.local_agent(f'worker{i}')[0] for i in range(3)]
    assert net.claim_row(agents[0], 'demo', strategy=strategy)['name'] == 'game'
    assert net.claim_row(agents[1], 'demo', strategy=strategy)['name'] == 'osFamiliarName'
    assert net.claim_row(agents[2], 'demo', strategy=strategy)['name'] == 'library'
    net.release(agents[2], 'library', '', 'demo')
    assert net.claim_row(agents[2], 'demo', 'library')['name'] == 'library'
    rows = {r['name']: r for r in net.function_map('demo')}
    assert rows['library']['sdk']['corpora'] == ['Example SDK']
    assert rows['game']['sdk'] is None
    assert rows['library']['status'] != 'matched'
    assert net.project_row('demo')['config'] == cfg


def test_exact_sdk_identity_reaches_imported_and_rom_targets(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Demo', 'mips', 1, str(tmp_path), '{}')).lastrowid
        for key, size, name in [('exact', 40, 'sdkFunction'), ('tiny', 16, 'getter'),
                                ('ambiguous', 40, 'one'), ('ambiguous', 40, 'two')]:
            db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                       ('Example SDK', 'mips', 1, name, key, size))
    prints = {i: SimpleNamespace(key=key, size=size, asm=asm) for i, (key, size, asm) in enumerate([
        ('exact', 40, False), ('tiny', 16, False), ('ambiguous', 40, False), ('exact', 40, True),
        ('exact', 44, False)])}
    monkeypatch.setattr(net.twins, 'prints', lambda proj: prints)
    proj = net.project_row('demo')
    result = identify(net, proj, {})
    assert list(result) == [0]
    assert result[0]['names'] == ['sdkFunction']
    assert result[0]['corpora'] == ['Example SDK']
    assert net.sdk_functions(proj) == result
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps({'sdk_libraries': []}), pid))
    assert identify(net, net.project_row('demo'), {}) == {}


def test_sdk_badge_explains_identity_without_changing_match_status():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for GUI helper tests')
    source = (Path(__file__).parents[1] / 'src/decompnet/app/static/js/function.js').read_text(encoding='utf-8')
    helper = source[source.index('function sdkBadge('):source.index('function renderList(')]
    script = '''const assert = require('node:assert/strict');
const esc = s => s.replaceAll('<', '&lt;').replaceAll('"', '&quot;');
''' + helper + '''
assert.equal(sdkBadge({}), '');
const html = sdkBadge({sdk: {corpora: ['<SDK>']}});
assert.ok(html.includes('SDK library'));
assert.ok(html.includes('&lt;SDK>'));
assert.ok(html.includes('Agents try game code first'));
assert.ok(html.includes('not a verified C match'));
'''
    subprocess.run([node, '-e', script], check=True, capture_output=True)


def test_game_batches_do_not_pull_in_nearby_sdk_code(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('demo', 'Demo', 'mips', 1, str(tmp_path), '{}')).lastrowid
        for i, name in enumerate(['first', 'sdk', 'game']):
            meta = {'naming': {'method': 'sdk_signature', 'name': name}} if name == 'sdk' else {}
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,vram,meta) VALUES(?,?,?,?,?,?)',
                       (pid, name, 40, 10, 4096 + i * 40, json.dumps(meta)))
    agent, _ = net.local_agent('worker')
    first = net.claim_row(agent, 'demo', 'first')
    assert [r['name'] for r in net._batch_more(agent, net.project_row('demo'), first, 4)] == ['game']


def test_sdk_corpus_index_is_shared_between_projects_and_invalidated(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'sdk.db',tmp_path)
    with net.store.tx() as db:
        for slug in ('a','b'):
            db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,'mips',1,?,'{}')",(slug,slug,str(tmp_path)))
        db.execute("INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES('SDK','mips',1,'name','same',40)")
    monkeypatch.setattr(net.twins,'prints',lambda proj:{1:SimpleNamespace(key='same',size=40,asm=False)})
    queries=[]
    net.store._reader().set_trace_callback(queries.append)
    assert identify(net,net.project_row('a'),{})[1]['names']==['name']
    assert identify(net,net.project_row('b'),{})[1]['names']==['name']
    assert sum('SELECT corpus,name,fingerprint,size' in q for q in queries)==1
    with net.store.tx() as db:db.execute("UPDATE sdk_signatures SET name='newName'")
    assert identify(net,net.project_row('a'),{})[1]['names']==['newName']

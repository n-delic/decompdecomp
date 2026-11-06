"""Only explicitly mapped local source is read, with byte layout preserved and normal judging on promotion."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import source_preparation as prep
from decompnet.network import Agent, Network, NetworkError
from decompnet.store import IN_MEMORY
from tests.test_permute import FakeNet


def config(tmp_path):
    return {'root': str(tmp_path), 'mappings': 'f = original @ game.c', 'overrides': 'LIMIT=128',
            'header': str(tmp_path / 'types.h'), 'include_roots': '', 'enabled': True}


def test_preserves_body_line_breaks_literals_and_original_line_number(tmp_path):
    cfg = config(tmp_path)
    original = '// first\n// second\nint original(int a)\n{\n    return a + LIMIT; /* original */\n}\n'
    (tmp_path / 'game.c').write_text(original)
    (tmp_path / 'types.h').write_text('#define LIMIT 2\nstruct Item { int values[LIMIT]; };\n')
    source, header = prep.prepare(prep.Reader(cfg), cfg, prep.mappings(cfg['mappings'])[0], {'original': 'f'}, '')
    assert source == '#line 3 "reference.c"\n' + original[original.index('int original'):].replace('int original(', 'int f(')
    assert '#define LIMIT 128\nstruct Item' in header
    assert '/* original */' in source
    assert prep.rename('original("original"); // original', {'original': 'f'}) == 'f("original"); // original'


def test_flatten_explicit_includes_retail_macro_before_struct(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / 'types.h').write_text('#include "config.h"\nstruct Item { int values[LIMIT]; };')
    (tmp_path / 'config.h').write_text('#define LIMIT 2\n')
    assert prep.Reader(cfg).flatten(tmp_path / 'types.h').startswith('#define LIMIT 128\n')
    (tmp_path / 'types.h').write_text('#include "missing.h"')
    with pytest.raises(NetworkError, match='include root'):
        prep.Reader(cfg).flatten(tmp_path / 'types.h')


def test_no_reads_outside_selected_roots_or_through_symlinks(tmp_path):
    root = tmp_path / 'selected'
    root.mkdir()
    (tmp_path / 'private.h').write_text('private')
    (root / 'link.h').symlink_to(tmp_path / 'private.h')
    cfg = config(root)
    for path in (tmp_path / 'private.h', root / 'link.h'):
        with pytest.raises(NetworkError, match='leaves the selected'):
            prep.Reader(cfg).file(path)
    cfg['include_roots'] = str(tmp_path)
    assert prep.Reader(cfg).file(root / 'link.h') == 'private'


def test_configuration_explicit_and_machine_local(tmp_path):
    net = FakeNet()
    cfg = config(tmp_path)
    (tmp_path / 'game.c').write_text('int original(void) { return 1; }')
    (tmp_path / 'types.h').write_text('')
    old = net.project_row('game')['config']
    saved = prep.configure(net, net.project_row('game'), cfg)
    assert prep.settings(net, net.project_row('game')) == saved
    assert net.project_row('game')['config'] == old
    for bad in ('f = original', 'f = original @ x.c\nf = other @ y.c', 'f = original @ x.c\ng = original @ y.c'):
        with pytest.raises(NetworkError):
            prep.mappings(bad)
    with pytest.raises(NetworkError):
        prep.overrides('LIMIT=128\n#include "private.h"')


def test_body_removal_retains_layout_and_declarations():
    text = 'struct X { int n; };\nint f(void) {\n return 1;\n}\nint g(void) { return 2; }\n'
    header = prep.declarations(text)
    assert header.count('\n') == text.count('\n')
    assert 'struct X { int n; };' in header
    assert 'return' not in header
    assert 'int f(void) ;' in header


def prepared_net():
    net = Network(IN_MEMORY)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(1,'game','Game','mips','.',?)", (json.dumps({'compiler': {}}),))
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(1,'autopilot','x')")
        db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) VALUES(1,1,'old',1,'initial',1,'')")
        db.execute("INSERT INTO functions(id,project_id,name,n_insns,best_score,best_source,vram,permuted) VALUES(1,1,'f',10,40,'old source',4096,4)")
        db.execute('INSERT INTO claims VALUES(1,1,1,9999999999)')
    fn = net.store.one('SELECT * FROM functions WHERE id=1')
    proj = net.store.one('SELECT * FROM projects WHERE id=1')
    return net, proj, fn


def test_private_header_and_candidate_promoted_atomically_without_shared_edit():
    net, proj, fn = prepared_net()
    expected = prep.inputs(net, proj, fn)
    net._record(fn, Agent(1, 'autopilot', None), 20, 'new source', 0, prepared_header='new', prepared_inputs=expected)
    current = net.store.one('SELECT * FROM functions WHERE id=1')
    assert current['best_source'] == 'new source' and current['best_score'] == 20
    assert current['permuted'] == 0 and current['unit'] == 'local_source_1'
    assert net._context(1, '')['header'] == 'old'
    assert net._context(1, current['unit'])['header'] == 'new'
    assert net.store.one('SELECT context_version FROM attempts')['context_version'] == 2


def test_changed_inputs_or_worse_candidate_never_installs_header():
    for change in ('config', 'target', 'source', 'header', 'symbols', 'matched', 'worse', 'claim'):
        net, proj, fn = prepared_net()
        expected = prep.inputs(net, proj, fn)
        with net.store.tx() as db:
            if change == 'config':
                db.execute("UPDATE projects SET config='{}'")
            elif change == 'target':
                db.execute("UPDATE functions SET target=?", (b'changed',))
            elif change == 'source':
                db.execute("UPDATE functions SET best_source='changed'")
            elif change == 'header':
                db.execute("UPDATE context_versions SET version=2")
            elif change == 'symbols':
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'value',8192,4,'data')")
            elif change == 'claim':
                db.execute('DELETE FROM claims')
            elif change == 'matched':
                db.execute("UPDATE functions SET status='matched'")
        with pytest.raises(NetworkError):
            net._record(fn, Agent(1, 'autopilot', None), 50 if change == 'worse' else 20, 'new source', 0,
                        prepared_header='new', prepared_inputs=expected)
        assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n'] == 0
        assert net.store.one('SELECT COUNT(*) AS n FROM context_versions')['n'] == 1
        assert net.store.one('SELECT unit FROM functions')['unit'] in ('', None)


def test_imported_mode_uses_existing_judge_without_editing_headers():
    calls = []
    net = SimpleNamespace(forks=SimpleNamespace(attempt=lambda *args, **kwargs: calls.append((args, kwargs)) or {'score': 0}))
    fn = {'name': 'f', 'best_score': 40}
    assert prep.attempt(SimpleNamespace(net=net), {}, fn, 'int f(void){return 1;}', 'reference header', 'agent', 'game') == 0
    assert calls[0][1] == {'keep': False, 'quiet': 40}
    assert 'reference header' not in calls[0][0]


def test_changed_header_renews_cached_trial_and_existing_match_is_skipped(tmp_path, monkeypatch):
    import threading

    from decompnet.app.permute import Permuter, State
    net, proj, fn = prepared_net()
    net.cfg = lambda proj: json.loads(proj['config'])
    net.project_row = lambda slug: net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug = lambda slug: None
    net.links = {}
    net.local_agent = lambda name: (Agent(1, 'autopilot', None), '')
    cfg = config(tmp_path)
    (tmp_path / 'game.c').write_text('int original(void) { return LIMIT; }')
    (tmp_path / 'types.h').write_text('#define LIMIT 2\n')
    prep.configure(net, proj, cfg)
    calls = []
    monkeypatch.setattr(prep, 'attempt', lambda *args: calls.append(args) or 100)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    permuter = Permuter(net)
    assert prep.run(permuter, proj, State(), only={'unrelated'})['tested'] == 0
    assert calls == []
    assert prep.run(permuter, proj, State(), only={'f'})['tested'] == 1
    assert prep.run(permuter, proj, State())['tested'] == 0
    (tmp_path / 'types.h').write_text('#define LIMIT 2\nextern int new_type;\n')
    assert prep.run(permuter, proj, State())['tested'] == 1
    assert len(calls) == 2
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched'")
    assert prep.run(permuter, proj, State())['tested'] == 0
    stop = threading.Event()
    stop.set()
    assert prep.run(permuter, proj, State(stop=stop))['tested'] == 0


def test_failed_compile_never_changes_saved_candidate_or_shared_header(monkeypatch):
    net, proj, fn = prepared_net()
    net._compile = lambda *args: SimpleNamespace(ok=False, log='missing struct declaration')
    with pytest.raises(NetworkError, match='missing struct declaration'):
        prep.attempt(SimpleNamespace(net=net), proj, fn, 'new source', 'bad header', Agent(1, 'autopilot', None), None)
    assert net.store.one('SELECT best_source,unit FROM functions')['best_source'] == 'old source'
    assert net._context(1, '')['header'] == 'old'


def test_reference_aliases_are_explicit_and_token_safe():
    assert prep.symbol_aliases('gReference = D_80010000') == {'gReference': 'D_80010000'}
    with pytest.raises(NetworkError):
        prep.symbol_aliases('gReference = first\ngReference = second')



def test_static_entry_point_is_emitted_without_changing_body_layout(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / 'game.c').write_text('static int original(int a)\n{\n  return a;\n}\n')
    (tmp_path / 'types.h').write_text('')
    source, _ = prep.prepare(prep.Reader(cfg), cfg, prep.mappings(cfg['mappings'])[0], {'original': 'f'}, '')
    assert source == '#line 1 "reference.c"\n       int f(int a)\n{\n  return a;\n}\n'



def test_conditional_definitions_select_configured_branch_and_duplicate_active_definitions_fail(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / 'game.c').write_text('#ifdef DEBUG\nint original(void){return 1;}\n#else\nint original(void){return 2;}\n#endif\n')
    (tmp_path / 'types.h').write_text('')
    source,_=prep.prepare(prep.Reader(cfg),cfg,prep.mappings(cfg['mappings'])[0],{'original':'f'},'')
    assert 'return 2' in source and 'return 1' not in source
    (tmp_path / 'game.c').write_text('int original(void){return 1;}\nint original(void){return 2;}')
    with pytest.raises(NetworkError, match='More than one definition'):
        prep.prepare(prep.Reader(cfg), cfg, prep.mappings(cfg['mappings'])[0], {}, '')


def test_reference_preserves_reached_same_file_graphics_style_macros(tmp_path):
    cfg = config(tmp_path)
    (tmp_path/'types.h').write_text('typedef struct { unsigned w0,w1; } Packet;\n')
    original = '#define WORD 0x1234\n#define PUSH(p) do { \\\n Packet *q=(p); q->w0=WORD; q->w1=1; \\\n} while(0)\nvoid original(Packet *p) { PUSH(p++); }\n'
    (tmp_path/'game.c').write_text(original)
    source, header = prep.prepare(prep.Reader(cfg),cfg,prep.mappings(cfg['mappings'])[0],{'original':'f'},'')
    assert 'PUSH(p++)' in source and source.count('p++')==1
    assert '#define PUSH(p) do { \\\n' in header and '#define WORD 0x1234' in header
    assert 'Packet *q=(p); q->w0=WORD; q->w1=1;' in header


def test_declarations_does_not_strip_graphics_macro_braces():
    text = '#define COMMAND(p) { Packet *q=(p); q->word=1; }\nvoid real(void) { execute(); }\n'
    declarations = prep.declarations(text)
    assert '#define COMMAND(p) { Packet *q=(p); q->word=1; }' in declarations
    assert 'execute();' not in declarations

"""Object-only objdiff configurations can still supply read-only donor C."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.forks.project import ForkProject
from decompnet.forks.source_paths import readable


@pytest.mark.parametrize('suffix', ['.c', '.cpp', '.cc', '.cxx', '.cp'])
def test_exact_unit_source_is_readable_without_making_it_writable(tmp_path, suffix):
    source = tmp_path / ('src/module' + suffix)
    source.parent.mkdir()
    source.write_text('int donor(void) { return 7; }')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units':[{'name':'src/module','target_path':'expected/module.o'}]}))
    fp = ForkProject(tmp_path)
    assert fp.unit('src/module').source is None
    assert readable(tmp_path, fp.unit('src/module')) == source
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    assert net.forks._current_source(fp, {'unit':'src/module','meta':'{}','name':'donor'}) == source.read_text()
    assert fp.unit('src/module').source is None


def test_ambiguous_missing_and_escaping_sources_are_not_guessed(tmp_path):
    unit = SimpleNamespace(name='module', source=None)
    assert readable(tmp_path, unit) is None
    for suffix in ('.c', '.cpp'):(tmp_path / ('module' + suffix)).write_text('int donor;')
    assert readable(tmp_path, unit) is None
    assert readable(tmp_path, SimpleNamespace(name='../module', source=None)) is None
    assert readable(tmp_path, SimpleNamespace(name=str(tmp_path / 'module'), source=None)) is None
    outside = tmp_path.parent / (tmp_path.name + '-outside.c')
    outside.write_text('int private;')
    (tmp_path / 'link.c').symlink_to(outside)
    assert readable(tmp_path, SimpleNamespace(name='link', source=None)) is None
    assert readable(tmp_path, SimpleNamespace(name='link', source=outside)) is None
    assert readable(tmp_path, SimpleNamespace(name='module', source='module.cpp')) == tmp_path / 'module.cpp'


def test_donor_header_reader_uses_the_same_unambiguous_source(tmp_path):
    from decompnet.app.donor_sources import imported
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/module.c').write_text('typedef int Word;\nWord donor(Word x) { return x; }')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units':[{'name':'src/module'}]}))
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    pairs = imported(net, {'root':str(tmp_path)}, {'unit':'src/module','meta':'{}','name':'donor'})
    assert 'Word donor' in pairs[0][0] and 'typedef int Word;' in pairs[0][1]


def test_inferred_imported_c_reaches_the_rom_judge(tmp_path, monkeypatch):
    from decompnet.app.twins import Print
    from tests.test_similar_matches import pair as fixture
    # Use the standard two-game fixture, with an actual donor source file and
    # object-only config. Only its target-object decoder and final judge are stubbed.
    net = fixture.__wrapped__(tmp_path, monkeypatch)
    root = tmp_path / 'donor'
    (root / 'src').mkdir(parents=True)
    (root / 'src/module.c').write_text('int old(int x) { return x + 60; }')
    (root / 'objdiff.json').write_text(json.dumps({'units':[{'name':'src/module'}]}))
    with net.store.tx() as db:
        db.execute('UPDATE projects SET root=? WHERE id=1', (str(root),))
        db.execute("UPDATE functions SET unit='src/module',best_source=? WHERE id=1", ('unused saved text\n'*2000,))
    from decompnet import large_text_storage
    large_text_storage.install(net.store.conn, {'functions'})
    assert net.store.one("SELECT typeof(best_source) t FROM main.functions WHERE id=1")['t'] == 'blob'
    donor, proj = net.project_row('source'), net.project_row('destination')
    monkeypatch.setattr(net.forks, 'get', lambda slug:(donor,ForkProject(root),{'platform':'n64'}))
    pa, pb = Print('same', [], 32), Print('same', [], 32)
    monkeypatch.setattr(net.twins, '_fork_prints', lambda *a,**kw:{1:pa})
    calls=[]
    def judge(agent, project, a, b, source, **kw):
        calls.append(source)
        return 'miss', 'test rejection'
    monkeypatch.setattr(net.twins, '_attempt_twin', judge)
    # Candidate metadata does not need the imported project's stored source blobs.
    net.store._reader().create_function('payload_read',2,lambda *a:pytest.fail('Decoded unrelated saved source'))
    assert net.twins._fork_to_rom(None,proj,donor,{'same':[2]},{2:pb},None)==0
    assert calls==['int new(int x) { return x + 60; }']
    assert net.twins.cross_game_results('destination')['counts']=={'miss':1}
    assert net.twins._fork_to_rom(None,proj,donor,{'same':[2]},{2:pb},None)==0
    assert len(calls)==1

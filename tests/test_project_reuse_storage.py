"""Reuse reads donor and destination evidence from independent project files."""
import json

import pytest

from decompnet.app import repair_mappings
from decompnet.app.engine.briefs import BriefMixin
from decompnet.app.reuse_donors import Donors
from decompnet.app.twins import Print, Twins
from tests import test_project_storage as base

scoped = base.scoped


def setup(net, stores):
    net.cfg = lambda proj: json.loads(proj['config'])
    net.is_rom = lambda proj: True
    net.fork_slug = lambda slug: None
    net.links = {}
    net.project_row = net._project
    net._layout_key = BriefMixin._layout_key.__get__(net)
    for pid in (1, 2):
        with stores[pid].tx() as db:
            db.execute('UPDATE projects SET config=?', (json.dumps({'kind':'rom','compiler':{'toolchain':'test','flags':[]}}),))
            db.execute('UPDATE functions SET size=64,vram=4096')
    with stores[1].tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='int same_name(void){return 1;}',matched_at=123")
    twins = Twins(net)
    twins.prints = lambda proj: {proj['id']*10:Print('same', [], 64)}
    net.twins = twins
    return twins


def test_donor_index_shortlist_and_result_labels_follow_source_owner(scoped):
    net, stores = scoped
    twins = setup(net, stores)
    source, destination = net._project('first'), net._project('second')
    assert twins.index_project(source) == 1
    assert stores[1].one('SELECT function_id FROM cross_game_index')['function_id'] == 10
    assert not stores[2].one('SELECT 1 FROM cross_game_index')
    donors = Donors(net, destination).candidates(64, 32, 128, '', '', 5)
    assert len(donors) == 1 and donors[0]['project_id'] == 1
    with stores[2].tx() as db:
        db.execute("INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,source_function_id,outcome,reason,created_at) "
                   "VALUES(2,20,1,10,'match','reverified',123)")
    result = twins.cross_game_results('second')
    assert result['counts'] == {'match':1}
    assert result['matches'][0]['game'] == 'first'
    assert result['matches'][0]['source_slug'] == 'first'
    assert twins.cross_game_results('first')['counts'] == {}
    assert not net.store.one('SELECT 1 FROM cross_game_attempts')


def test_repair_provenance_reads_shared_ids_from_separate_donor(scoped):
    net, stores = scoped
    setup(net, stores)
    a, b = net._function('same_name', 'first'), net._function('same_name', 'second')
    repair_mappings.remember(net, a, b, 'candidate', {'same_name':'same_name'})
    assert len(repair_mappings.origins(net, b, 'candidate')) == 1
    assert not stores[1].one('SELECT 1 FROM repair_origins')
    with stores[2].tx() as db:db.execute("UPDATE functions SET status='matched',matched_source='candidate'")
    repair_mappings.accepted(net, net._function('same_name', 'second'), 'candidate')
    assert len(repair_mappings.pairs(net, net._project('second'), net._project('first'))) == 1
    with stores[1].tx() as db:db.execute("UPDATE functions SET matched_source='changed donor'")
    assert repair_mappings.origins(net, b, 'candidate') == []
    assert repair_mappings.pairs(net, net._project('second'), net._project('first')) == []


def test_shared_match_archive_waits_for_owning_project_commit(scoped):
    from decompnet.app.match_library import Library
    net, stores = scoped
    from pathlib import Path
    library = Library(net)
    assert library.path.parent == Path(net.store.path).parent
    assert library.path.parent != Path(stores[1].path).parent
    written = []
    library.snapshot = lambda proj, fid: {'function':fid}
    library.write = lambda items: written.extend(items)
    with pytest.raises(RuntimeError, match='rollback'):
        with stores[1].tx():
            library.capture(net._project('first'), 10)
            assert written == []
            raise RuntimeError('rollback')
    assert written == []
    with stores[2].tx():
        library.capture(net._project('second'), 20)
        assert written == []
    assert written == [{'function':20}]


def test_the_donor_shortlist_decompresses_no_text(scoped):
    """Shortlisting read each candidate with SELECT *, decompressing its assembly and every C column in a callback
    inside SQLite: the live app's top CPU use (365 s an hour). Ranking needs none of it; the few donors tried are
    read whole later. A donor whose C is missing from storage still fails the shortlist (the test above)."""
    from decompnet import payload_storage
    net, stores = scoped
    twins = setup(net, stores)
    store = stores[1]
    source = 'int same_name(void){return 1;}\n' * 500  # (packed: large text)
    with store.tx() as db:
        db.execute("UPDATE functions SET matched_source=?,best_source=?,asm=?", (source, source + '/**/', source + ';'))
    assert store.one("SELECT typeof(matched_source) t FROM main.functions")['t'] == 'blob'
    assert twins.index_project(net._project('first')) == 1
    decoded = []
    original = payload_storage.unpack
    reader = store._reader() or store.conn
    reader.create_function('payload_read', 2, lambda *args: decoded.append(args[0]) or original(*args), deterministic=True)
    try:
        donors = Donors(net, net._project('second')).candidates(64, 32, 128, '', '', 5)
    finally:
        reader.create_function('payload_read', 2, original, deterministic=True)
    assert len(donors) == 1 and donors[0]['has_matched'] and donors[0]['target'] is not None
    assert 'matched_source' not in donors[0] and 'asm' not in donors[0]
    assert decoded == []

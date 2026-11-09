"""Shared detection never counts as C; transfers always go through the destination judge."""

import time
from types import SimpleNamespace

import pytest

from decompnet.app import shared_reuse
from decompnet.app.twins import Job, Print
from tests.test_similar_matches import pair as pair_fixture

pair = pytest.fixture(pair_fixture.__wrapped__)


def setup(pair, monkeypatch, *, donor_fork=False, status='open'):
    with pair.store.tx() as db:
        db.execute("UPDATE projects SET arch='',big_endian=0,config=json_set(config,'$.kind','fork') WHERE id=2")
        if donor_fork:
            db.execute("UPDATE projects SET arch='',big_endian=0,config=json_set(config,'$.kind','fork') WHERE id=1")
        db.execute('UPDATE functions SET status=? WHERE id=2', (status,))
    monkeypatch.setattr(pair, 'fork_slug', lambda slug: slug if slug == 'destination' or donor_fork else None)
    monkeypatch.setattr(pair.twins, 'prints', lambda p: {p['id']: Print('same', [], 32)})
    return pair.project_row('destination')


def test_imported_empty_arch_detects_rom_and_sdk_even_without_source(pair, monkeypatch):
    proj = setup(pair, monkeypatch, status='nosource')
    with pair.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)', ('local SDK', 'mips', 1, 'library_name', 'same', 32))
    result = shared_reuse.discover(pair.twins, proj)
    assert result['detected'] == 1 and result['blocked'] == 1 and result['ready'] == 0
    assert {h['name'] for h in result['hints'][2]} == {'old', 'library_name'}
    assert pair.twins.cross_game_results('destination')['counts'] == {}
    assert pair.twins.suggestions('destination', 'new')[0]['name'] == 'old'
    assert pair.store.one('SELECT status FROM functions WHERE id=2')['status'] == 'nosource'


@pytest.mark.parametrize('donor_fork', [False, True])
def test_imported_destination_uses_judge_records_misses_and_matches(pair, monkeypatch, donor_fork):
    proj = setup(pair, monkeypatch, donor_fork=donor_fork)
    if donor_fork:
        monkeypatch.setattr(pair.forks, 'twin_source', lambda *args, **kw: 'int new(int x) { return x+60; }')
    from decompnet.app.free_candidates import budget
    reports = []
    job = Job()
    budget(job)
    job.cross_report = reports.append
    job.cross_started = time.monotonic()
    job.cross_seconds = 120
    sources = []
    def judge(agent, slug, name, source, **kw):
        sources.append(source)
        assert slug == 'destination' and name == 'new' and 'new(int' in source
        return {'matched': False, 'score': 12, 'text': 'score 12'}
    monkeypatch.setattr(pair.forks, 'attempt', judge)
    assert shared_reuse.transfer(pair.twins, proj, job) == 0
    current = next(p for p in reports if p['phase'] == 'Compiling candidate')
    assert (current['game'], current['donor'], current['function'], current['attempts']) == ('source', 'old', 'new', 1)
    assert any(p['phase'] == 'Finding shared machine code' and p['total'] == 1 for p in reports)
    assert pair.twins.cross_game_results('destination')['counts'] == {'miss': 1}
    assert shared_reuse.transfer(pair.twins, proj, Job()) == 0
    assert len(sources) == 1  # identical rejected C is not compiled repeatedly
    with pair.store.tx() as db:
        db.execute('DELETE FROM twin_misses')
    def accepted(*args, **kwargs):
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched' WHERE id=2")
        return {'matched': True, 'score': 0}
    monkeypatch.setattr(pair.forks, 'attempt', accepted)
    assert shared_reuse.transfer(pair.twins, proj, Job()) == 1
    assert pair.twins.cross_game_results('destination')['counts'] == {'miss': 1, 'match': 1}


def test_incompatible_processor_and_remote_projects_never_detected(pair, monkeypatch):
    proj = setup(pair, monkeypatch)
    pair.links['source'] = object()
    assert shared_reuse.discover(pair.twins, proj)['detected'] == 0
    pair.links.clear()
    with pair.store.tx() as db:
        db.execute("UPDATE projects SET arch='arm' WHERE id=1")
    assert shared_reuse.discover(pair.twins, proj, force=True)['detected'] == 0


def test_function_hints_do_not_crawl_other_checkouts(pair, monkeypatch):
    proj = setup(pair, monkeypatch)
    monkeypatch.setattr(shared_reuse, 'compatible', lambda *args: pytest.fail('modal crawled checkouts'))
    assert shared_reuse.hints(pair.twins, proj, 'new') == []


def test_scaffold_failure_is_reported_without_attempt_or_match(pair, monkeypatch):
    from contextlib import nullcontext

    from decompnet.forks.project import ForkError
    proj = setup(pair, monkeypatch, status='nosource')
    monkeypatch.setattr(pair.forks, 'get', lambda slug: (proj, object(), {}))
    monkeypatch.setattr(pair.forks, '_tree', lambda p: SimpleNamespace(exclusive=nullcontext))
    pair.forks._build_locks[2] = nullcontext()
    def failed(*args, **kwargs):
        raise ForkError('no C object rule')
    monkeypatch.setattr('decompnet.forks.scaffold.prepare', failed)
    assert shared_reuse.transfer(pair.twins, proj, Job()) == 0
    assert pair.twins.cross_game_results('destination')['counts'] == {}


def test_cascade_notifies_compatible_imported_destination(pair, monkeypatch):
    setup(pair, monkeypatch)
    cascade = pair.cascades
    cascade.enabled = True
    cascade.running = True  # inspect the queue without launching a worker
    cascade.notify(pair.project_row('source'), 1)
    assert cascade.pending == {'destination'}
    cascade.paused.add('destination')
    cascade.pending.clear()
    cascade.notify(pair.project_row('source'), 1)
    assert not cascade.pending


def test_restored_batch_discovers_other_projects_once_and_preserves_size_gaps(pair,monkeypatch):
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET size=10 WHERE id=1')
        db.execute("INSERT INTO functions(id,project_id,name,status,size,best_source) VALUES(3,1,'wide_donor','matched',1000,'int wide_donor(void){return 0;}')")
        db.execute("UPDATE functions SET name='unrelated',size=200 WHERE id=2")
    donor=pair.project_row('source');destination=pair.project_row('destination')
    discoveries=[]
    monkeypatch.setattr(shared_reuse,'compatible',lambda *a, **kw:discoveries.append(1) or [destination])
    pair.cascades.enabled=True;pair.cascades.running=True
    pair.store._reader().create_function('payload_read',2,lambda *_:pytest.fail('discovery inflated saved C'))
    pair.cascades.notify_many(donor,[1,3])
    assert len(discoveries)==1 and not pair.cascades.pending
    with pair.store.tx() as db:db.execute('UPDATE functions SET size=1000 WHERE id=2')
    pair.cascades.notify_many(donor,[1,3])
    assert len(discoveries)==2 and pair.cascades.pending=={'destination'}

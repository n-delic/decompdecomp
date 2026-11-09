"""A dedicated similar-code budget must survive general reuse exhaustion."""
import time

import pytest

from decompnet.app import free_candidates, similar_matches, similar_phase
from decompnet.app.twins import Job
from tests.test_similar_matches import pair as pair_fixture

pair = pytest.fixture(pair_fixture.__wrapped__)


def test_similar_phase_survives_general_exhaustion_and_reports_its_own_limits(monkeypatch):
    job = Job()
    free_candidates.budget(job, seconds=1, attempts=1)
    job.reuse_attempts = 1
    job.reuse_deadline = time.monotonic() - 1
    seen = []
    def run(twins, slug, phase):
        assert not free_candidates.exhausted(phase)
        phase.reuse_attempts += 1
        phase.reuse_matched += 1
        phase.reuse_bytes += 48
        phase.cross_tried += 1
        from decompnet.app.cross_game_progress import report
        report(phase, 'Checking similar matched functions')
        seen.append(phase)
        return 1
    monkeypatch.setattr(similar_matches, 'run', run)
    assert similar_phase.run(None, 'project', job) == 1
    assert job.reuse_attempts == 1 and job.similar_attempts == 1
    assert similar_phase.totals(job) == (2, 1, 48)
    assert job.progress['attempts'] == 1
    assert job.progress['attempt_limit'] == similar_matches.MAX_ATTEMPTS
    assert job.cross_tried == 1
    deadline = seen[0].reuse_deadline
    similar_phase.run(None, 'second-project', job)
    assert seen[0] is seen[1] and seen[1].reuse_deadline == deadline
    assert job.similar_attempts == 2  # one wave cannot reset the independent budget per project
    job.stop.set()
    assert free_candidates.exhausted(seen[0])


def test_nearby_local_sibling_is_reserved_before_global_shortlist_limit(pair):
    from decompnet.app.reuse_donors import Donors

    # More same-size global donors than the normal shortlist can hold, with lower IDs.
    with pair.store.tx() as db:
        row = pair.store.one('SELECT * FROM functions WHERE id=1')
        for fid in range(3, 405):
            db.execute('INSERT INTO functions(id,project_id,name,size,target,relocs,vram,status,matched_source) VALUES(?,?,?,?,?,?,?,?,?)',
                       (fid,1,f'global_{fid}',32,row['target'],'[]',0x80001000+fid*32,'matched',row['matched_source']))
        db.execute('INSERT INTO functions(id,project_id,name,size,target,relocs,vram,status,matched_source) VALUES(?,?,?,?,?,?,?,?,?)',
                   (1000,2,'local_sibling',32,row['target'],'[]',0x80001020,'matched',row['matched_source']))
    pair.twins.index_project(pair._project('source'))
    pair.twins.index_project(pair._project('destination'))
    donors = Donors(pair, pair._project('destination'), include_self=True)
    assert 1000 not in [r['id'] for r in donors.candidates(32,32,32,'','',400)]
    selected = donors.candidates(32,32,32,'','',400,near=0x80001000)
    assert selected[0]['id'] == 1000
    assert len(selected) == 400 and any(r['project_id']==1 for r in selected)


def test_new_match_queues_its_own_project_and_preserves_pause_and_disable(pair):
    with pair.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,size,status) VALUES(3,1,'local_target',32,'open')")
    cascade = pair.cascades
    cascade.enabled = True
    cascade.running = True  # inspect coalesced discovery without starting a worker
    donor = pair.project_row('source')
    cascade.notify(donor, 1)
    assert cascade.pending == {'source', 'destination'}
    cascade.pause('source')
    cascade.pending.clear()
    cascade.notify(donor, 1)
    assert cascade.pending == {'destination'}
    cascade.configure(False)
    cascade.notify(donor, 1)
    assert not cascade.pending


def test_improvement_without_verified_match_does_not_queue_own_project(pair):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open',best_source=matched_source WHERE id=1")
    pair.cascades.enabled = True
    pair.cascades.running = True
    pair.cascades.notify(pair.project_row('source'), 1)
    assert 'source' not in pair.cascades.pending


def test_notification_waits_for_owning_pass_and_can_be_woken(pair, monkeypatch):
    from decompnet.app import reuse_cascade

    queued = []
    monkeypatch.setattr(reuse_cascade.purpose_work, 'start',
                        lambda purpose, project, fn, *args, **kwargs: queued.append(lambda: fn(*args)))
    cascade = pair.cascades
    cascade.configure(True)
    owner = Job()
    pair.twins.jobs['destination'] = owner
    cascade.notify(pair.project_row('source'), 1)
    assert len(queued) == 1
    queued.pop()()
    assert cascade.pending == {'destination'} and not cascade.running and not queued
    owner.state = 'done'
    cascade.wake()
    assert len(queued) == 1
    calls = []
    monkeypatch.setattr(pair.twins, 'across_games', lambda *args: 0)
    monkeypatch.setattr(reuse_cascade.named_donors, 'run', lambda *args: 0)
    monkeypatch.setattr(reuse_cascade.free_candidates, 'run', lambda *args: 0)
    monkeypatch.setattr(similar_matches, 'run', lambda twins, slug, job: calls.append(slug) or 0)
    queued.pop()()
    assert calls == ['destination'] and not cascade.pending and not cascade.running


def test_cascade_runs_similarity_after_general_budget_is_used(pair, monkeypatch):

    cascade = pair.cascades
    cascade.enabled = True
    cascade.pending = {'destination'}
    job = Job()
    free_candidates.budget(job, attempts=1)
    job.reuse_attempts = 1
    calls = []
    def run(twins, slug, phase):
        calls.append(slug)
        assert not free_candidates.exhausted(phase)
        phase.reuse_attempts += 1
        return 0
    monkeypatch.setattr(similar_matches, 'run', run)
    monkeypatch.setattr(pair.twins, 'across_games', lambda *args: pytest.fail('general budget was already spent'))
    cascade.drain(job)
    assert calls == ['destination'] and job.similar_attempts == 1 and job.reuse_attempts == 1
    assert pair.store.one('SELECT attempts FROM reuse_runs')['attempts'] == 1


def test_autopilot_discovers_compiles_and_adopts_a_real_sibling_without_a_selected_donor(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    from tools.replay_parameter_transfer import autopilot

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('requires the pinned KMC GCC compiler')
    profile = {'toolchain':'gcc-2.7.2-kmc', 'flags':['-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}
    config = compiler_config(tmp_path, {'compiler':profile})
    source = ('void donor(int value, short fraction, short *output) {\n'
              'if (fraction < 50) { output[0] = value * fraction / 50; } '
              'else { output[0] = value * (fraction - 50) / 50; }\n}')
    fixture = {'platform':'n64','big_endian':True,'compiler':profile,'header':''}
    for name, scale, address in [('donor',50,0x80001000),('destination',500,0x80002000)]:
        actual = source.replace('donor',name).replace('50',str(scale))
        result = compile_source(config,'',actual)
        assert result.ok, result.log
        function = extract_function(result.obj,name)
        assert function and not function.relocs
        fixture[name] = {'name':name, 'vram':address,'target':function.code.hex(), 'relocs':'[]',
                         'source':source.replace('donor',name)}  # destination starts from the wrong scale
    result = autopilot(fixture)
    assert result['exact'] and result['destination_status']=='matched'
    assert result['similar_attempts']==1
    assert any(e['outcome']=='match' and 'parameter transfer' in e['reason'] for e in result['evidence'])

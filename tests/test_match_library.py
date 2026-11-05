"""Independent match storage survives deletion; shared identities and texts are deduplicated."""
import json
from pathlib import Path

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Print
from decompnet.store import Store


@pytest.fixture
def library(tmp_path, monkeypatch):
    net=RomNetwork(tmp_path/'decomp.db',tmp_path)
    with net.store.tx() as db:
        for i, name in enumerate(('First game','Second game'),1):
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)',
                       (i,f'game{i}',name,'mips',1,str(tmp_path/f'game{i}'),json.dumps({'platform':'n64','compiler':{'toolchain':'gcc','flags':['-O2']}})))
            db.execute("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source,matched_at) VALUES(?,?,?,'matched',32,4096,?,1)",
                       (i,i,f'f{i}',f'int f{i}(void) {{return 42;}}'))
    monkeypatch.setattr(net.twins,'prints',lambda proj:{proj['id']:Print('same-code',[],32)})
    return net,net.match_library


def test_deduplicates_functions_and_source_with_multiple_game_memberships(library):
    net,lib=library
    lib.backfill();lib.backfill()
    data=lib.summary()
    assert (data['functions'],data['solutions'],data['games'],data['occurrences'])==(1,1,2,2)
    assert data['items'][0]['games']==2 and 'First game' in data['items'][0]['game_names']
    with lib.connect() as db:
        assert db.execute('SELECT COUNT(*) n FROM texts WHERE text LIKE ?',('%return 42%',)).fetchone()['n']==1
    assert lib.summary('Second')['items'][0]['games']==2


def test_reopen_database_and_remove_project_preserves_matches(library):
    net,lib=library
    net.remove_project('game1')
    assert lib.summary()['functions']==1
    assert net.store.one("SELECT 1 FROM projects WHERE slug='game1'") is None
    reopened=RomNetwork(net.store.path,net.workspace)
    assert reopened.match_library.summary()['items'][0]['game_names']=='First game'
    assert lib.candidates('mips',1,'same-code',32)[0]['source']=='int __library_function(void) {return 42;}'


def test_only_committed_matches_are_archived(library):
    net,lib=library
    with pytest.raises(RuntimeError):
        with net.store.tx():
            lib.capture(net.project_row('game1'),1)
            assert lib.summary()['functions']==0
            raise RuntimeError('rollback')
    assert lib.summary()['functions']==0
    with net.store.tx():
        lib.capture(net.project_row('game1'),1)
    assert lib.summary()['functions']==1


def test_store_nested_commit_callbacks_run_once_and_not_on_rollback(tmp_path):
    store=Store(tmp_path/'db.sqlite');calls=[]
    with store.tx():
        with store.tx():store.after_commit(lambda:calls.append(1))
        assert calls==[]
    assert calls==[1]
    with pytest.raises(ValueError):
        with store.tx():
            store.after_commit(lambda:calls.append(2))
            raise ValueError()
    assert calls==[1]


def test_library_candidates_reach_destination_judge_after_donor_removed(library,monkeypatch):
    net,lib=library
    net.remove_project('game1')
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE id=2")
    sources=[]
    def judge(agent,proj,a,b,source,**kwargs):
        sources.append(source)
        assert 'int f2(void)' in source and 'return 42' in source
        return 'match',''
    monkeypatch.setattr(net.twins,'_attempt_twin',judge)
    assert lib.reuse(net.twins,net.project_row('game2'))==1
    assert len(sources)==1
    assert lib.candidates('ppc',1,'same-code',32)==[]
    assert lib.candidates('mips',0,'same-code',32)==[]


def test_new_verified_variants_are_retained_not_overwritten(library):
    net,lib=library
    lib.archive_project(net.project_row('game1'))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f1(void) {return 6*7;}' WHERE id=1")
    lib.archive_project(net.project_row('game1'))
    assert lib.summary()['functions']==1 and lib.summary()['solutions']==2


def test_archive_failure_prevents_project_deletion(library,monkeypatch):
    net,lib=library
    monkeypatch.setattr(lib,'write',lambda _:(_ for _ in ()).throw(OSError('Disk full')))
    with pytest.raises(OSError,match='Disk full'):net.remove_project('game1')
    assert net.project_row('game1')


def test_removed_rom_donor_still_matches_another_game_with_real_judge(tmp_path, built, monkeypatch):  # noqa: F811
    from tests.test_app_engine import CLAMP
    from tests.test_rom import ROM
    from tests.test_twins_across_games import copy_project
    net=RomNetwork(tmp_path/'real.db',tmp_path)
    slug=net.import_rom(ROM,confirmed=True,log=lambda *_:None)
    monkeypatch.setattr(net.cascades,'notify',lambda *a:None)
    monkeypatch.setattr(net.twins,'after_match',lambda *a:None)
    agent,_=net.local_agent('test')
    net.split_function(agent,slug,'func_800007C4','0x800007D0')
    copy_project(net,slug,'other-game')
    assert net.compile_and_diff(agent,'func_800007D0',CLAMP,slug).startswith('✅ MATCH')
    import shutil
    old_root=Path(net.project_row(slug)['root'])
    target_root=tmp_path/'retained-target'
    shutil.move(str(net.project_row('other-game')['root']),target_root)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET root=? WHERE slug='other-game'",(str(target_root),))
    net.remove_project(slug)
    shutil.rmtree(old_root)
    assert net.twins.across_games('other-game')>=1
    assert net._function('func_800007D0','other-game')['status']=='matched'
    assert net.match_library.summary()['games']==2


from tests.test_rom import built  # noqa: E402,F401


@pytest.mark.parametrize('destination_fork',[False,True])
def test_archived_candidate_reaches_both_destination_modes(library,monkeypatch,destination_fork):
    net,lib=library
    net.remove_project('game1')
    monkeypatch.setattr(net,'fork_slug',lambda slug:slug if slug=='game2' and destination_fork else None)
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE id=2")
    calls=[]
    def judge(*args,**options):
        calls.append(options['theirs']);return 'miss','Rejected by destination judge'
    monkeypatch.setattr(net.twins,'_attempt_twin',judge)
    assert lib.reuse(net.twins,net.project_row('game2'))==0
    assert calls==[destination_fork]
    assert net._function('f2','game2')['status']=='open'
    assert lib.reuse(net.twins,net.project_row('game2'))==0  # miss cache still applies
    assert len(calls)==1


def test_imported_match_is_retained_with_its_compiler_and_reusable_after_removal(library,monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import twins
    net,lib=library
    monkeypatch.setattr(net,'fork_slug',lambda slug:slug if slug=='game1' else None)
    monkeypatch.setattr(net.forks,'get',lambda slug:(net.project_row(slug),SimpleNamespace(),{}))
    monkeypatch.setattr(twins,'_file_compiler',lambda *args:'original-file-compiler')
    net.remove_project('game1')
    item=lib.candidates('mips',1,'same-code',32)[0]
    assert item['mode']=='fork'
    assert json.loads(item['compiler'])['toolchain']=='original-file-compiler'
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE id=2")
    monkeypatch.setattr(net.twins,'_attempt_twin',lambda *args,**kwargs:('match',''))
    assert lib.reuse(net.twins,net.project_row('game2'))==1



def test_normalized_source_name_does_not_collide_with_a_local(library):
    net,lib=library
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f1(void) {int __library_function=42;return __library_function;}' WHERE id=1")
    item=lib.snapshot(net.project_row('game1'),1)
    assert item['source']=='int __library_function_(void) {int __library_function=42;return __library_function;}'



def test_backfill_reads_verified_state_without_syncing_project_files(library,monkeypatch):
    net,lib=library
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:pytest.fail('read-only archive must not reconcile project files'))
    assert lib.backfill()['functions']==1


def test_summary_includes_database_and_sqlite_sidecars(library):
    _,lib=library
    # Keep a connection open so SQLite does not remove the live sidecars.
    with lib.connect():
        data=lib.summary()
        expected=sum(p.stat().st_size for p in lib.path.parent.glob(lib.path.name+'*'))
        assert data['disk_bytes']==expected and expected>0
    assert lib.summary()['disk_bytes']==lib.path.stat().st_size


def test_backfill_reports_game_function_and_overall_progress(library):
    _,lib=library
    updates=[]
    def log(line):pass
    log.check_cancel=lambda:None
    log.progress=updates.append
    lib.backfill(log)
    reading=[p for p in updates if p['phase']=='Reading verified matches' and p.get('function')]
    assert [(p['game'],p['game_index'],p['games_total'],p['done'],p['total'],p['function']) for p in reading]==[
        ('First game',1,2,0,2,'f1'),('Second game',2,2,1,2,'f2')]
    writing=[p for p in updates if p['phase']=='Writing matches to library']
    assert [p['done'] for p in writing]==[0,1]
    assert updates[-1]=={'phase':'Indexing complete','done':2,'total':2}
    assert [p['done'] for p in updates]==sorted(p['done'] for p in updates)

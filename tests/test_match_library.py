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
    if destination_fork:
        cfg=net.cfg
        monkeypatch.setattr(net,'cfg',lambda proj:{k:v for k,v in cfg(proj).items() if k!='compiler'} if proj['slug']=='game2' else cfg(proj))
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


def test_startup_archiving_skips_unchanged_roms_but_not_changed_inputs(library,monkeypatch):
    net,lib=library
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=json_set(config,'$.kind','rom')")
    calls=[]
    archive=lib.archive_project
    def track(proj,*args,**kwargs):
        calls.append(proj['id']);return archive(proj,*args,**kwargs)
    monkeypatch.setattr(lib,'archive_project',track)
    lib.backfill(only_changed=True)
    assert calls==[1,2]
    calls.clear();lib.backfill(only_changed=True)
    assert calls==[]
    reopened=type(lib)(net)
    monkeypatch.setattr(reopened,'archive_project',lambda *a,**kw:pytest.fail('Restart discarded persisted archive stamp'))
    reopened.backfill(only_changed=True)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f1(void){return 43;}' WHERE id=1")
    lib.backfill(only_changed=True)
    assert calls==[1]
    calls.clear();lib.backfill()
    assert calls==[1,2], 'Explicit indexing must always rescan'


def test_archive_looks_up_source_symbols_without_walking_the_inventory(library):
    from decompnet.rom.symbols import Symbol, SymbolTable
    net,lib=library
    class IndexedSymbols(dict):
        def values(self):pytest.fail('Do not scan the whole game for each function')
        def items(self):pytest.fail('Do not scan the whole game for each function')
        scans=0
        def __iter__(self):
            self.scans+=1
            assert self.scans==1, 'Build symbol order once, never per function'
            return super().__iter__()
    symbols=SymbolTable([Symbol('zebra',4),Symbol('alpha',8),Symbol('unused',12)])
    symbols.by_name=IndexedSymbols(symbols.by_name)
    net._syms[1]=symbols
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f1(void) {return zebra+alpha;}' WHERE id=1")
    lib.archive_project(net.project_row('game1'))
    saved=lib.candidates('mips',1,'same-code',32)[0]
    assert [s['name'] for s in json.loads(saved['symbols'])]==['zebra','alpha']
    lib.snapshot(net.project_row('game1'),1)
    lib.archive_project(net.project_row('game1'))
    assert symbols.by_name.scans==1


def test_archive_batches_narrow_reads_and_preserves_single_capture_evidence(library,monkeypatch):
    from decompnet.app import match_library
    net,lib=library
    with net.store.tx() as db:
        db.executemany("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source,matched_at) VALUES(?,1,?,'matched',32,4096,?,1)",
                       [(i,f'f{i}',f'int f{i}(void) {{return {i};}}') for i in range(3,43)])
    proj=net.project_row('game1')
    ids=net.store.all("SELECT id FROM functions WHERE project_id=1 ORDER BY id")
    expected={row['id']:lib.snapshot(proj,row['id']) for row in ids}
    reads=[];writes=[]
    for method in ('one','all'):
        original=getattr(net.store,method)
        def track(sql,*args,_original=original):
            reads.append(sql);return _original(sql,*args)
        monkeypatch.setattr(net.store,method,track)
    monkeypatch.setattr(match_library,'ARCHIVE_BATCH',16)
    monkeypatch.setattr(lib,'write',lambda rows:writes.extend(rows))
    lib.archive_project(proj)
    assert sorted(writes,key=lambda r:r['name'])==sorted(expected.values(),key=lambda r:r['name'])
    assert len(reads)<40, 'Shared reads must grow by batches, not by functions'
    assert not any('SELECT * FROM functions' in sql for sql in reads)


def test_archive_refreshes_shared_inputs_and_matched_status_between_batches(library,monkeypatch):
    from decompnet.app import match_library
    net,lib=library
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,'old header')")
        for i in (3,4):
            db.execute("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source) VALUES(?,1,?,'matched',32,4096,?)",
                       (i,f'f{i}',f'int f{i}(void) {{return {i};}}'))
    monkeypatch.setattr(match_library,'ARCHIVE_BATCH',1)
    saved=[]
    def write(rows):
        saved.extend(rows)
        if len(saved)==1:
            with net.store.tx() as db:
                db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',2,'new header')")
                db.execute("UPDATE functions SET status='open' WHERE id=3")
    monkeypatch.setattr(lib,'write',write)
    lib.archive_project(net.project_row('game1'),rows=[{'id':i,'name':f'f{i}'} for i in (1,3,4)])
    assert [(r['name'],r['header']) for r in saved]==[('f1','old header'),('f4','new header')]


def test_archive_uses_original_verified_header_and_compiler_without_current_profile_search(library,monkeypatch):
    from decompnet import match_bundle, project
    net,lib=library
    with net.store.tx() as db:
        db.execute(match_bundle.SCHEMA)
        db.execute('INSERT INTO verified_matches VALUES(?,?,?,?,?)',
                   (1,0,json.dumps({'compiler':{'toolchain':'verified','flags':['-O1']}}),
                    'int f1(void) {return 42;}','original verified header'))
    monkeypatch.setattr(project,'compiler_for',lambda *a:pytest.fail('Verified profile needs no fallback search'))
    monkeypatch.setattr(net,'_context',lambda *a:pytest.fail('Verified header needs no current-context lookup'))
    lib.archive_project(net.project_row('game1'))
    saved=lib.candidates('mips',1,'same-code',32)[0]
    assert json.loads(saved['compiler'])=={'toolchain':'verified','flags':['-O1']}
    assert saved['header']=='original verified header'


def test_archive_normalization_respects_reserved_tokens_in_headers(library):
    net,lib=library
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(1,'',1,?)",
                   ('extern int __library_function, __library_function_;',))
    assert lib.snapshot(net.project_row('game1'),1)['source']=='int __library_function__(void) {return 42;}'


def test_startup_archive_survives_app_updates_and_invalidates_for_archive_format(library,monkeypatch):
    from decompnet.app import build_info, match_library
    net,lib=library
    with net.store.tx() as db:db.execute("UPDATE projects SET config=json_set(config,'$.kind','rom')")
    monkeypatch.setattr(build_info,'commit',lambda:'old-build')
    lib.backfill(only_changed=True)
    calls=[]
    archive=lib.archive_project
    monkeypatch.setattr(lib,'archive_project',lambda proj,*args,**kw:(calls.append(proj['id']),archive(proj,*args,**kw))[-1])
    monkeypatch.setattr(build_info,'commit',lambda:'unrelated-new-build')
    lib.backfill(only_changed=True)
    assert calls==[]
    monkeypatch.setattr(match_library,'ARCHIVE_VERSION',match_library.ARCHIVE_VERSION+1)
    lib.backfill(only_changed=True)
    assert calls==[1,2]


def test_stopping_between_batches_keeps_completed_matches_and_retry_deduplicates(library,monkeypatch):
    from decompnet.app import maintenance, match_library
    net,lib=library
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source) VALUES(3,1,'f3','matched',32,4096,'int f3(void) {return 43;}')")
    monkeypatch.setattr(match_library,'ARCHIVE_BATCH',1)
    stopped=False
    original=lib.write
    def write(rows):
        nonlocal stopped
        original(rows);stopped=True
    monkeypatch.setattr(lib,'write',write)
    def log(line):pass
    def cancel():
        if stopped:raise maintenance.Cancelled('Stopped')
    log.check_cancel=cancel;log.progress=lambda _:None
    with pytest.raises(maintenance.Cancelled):lib.archive_project(net.project_row('game1'),log)
    assert lib.summary()['occurrences']==1
    monkeypatch.setattr(lib,'write',original)
    lib.archive_project(net.project_row('game1'))
    assert lib.summary()['occurrences']==2


def test_reindex_avoids_unchanged_writes_and_refreshes_game_and_seen_metadata(library,monkeypatch):
    from contextlib import contextmanager
    net,lib=library
    item=lib.snapshot(net.project_row('game1'),1)
    with lib.connect() as db:
        @contextmanager
        def connection():
            with db:yield db
        monkeypatch.setattr(lib,'connect',connection)
        lib.write([item])
        changes=db.total_changes
        lib.write([item,item])
        assert db.total_changes==changes
        lib.write([{**item,'game_name':'Renamed game','seen':20}])
        assert db.total_changes==changes+2
        saved=lib.candidates('mips',1,'same-code',32)[0]
        assert saved['game']=='Renamed game'
        assert db.execute('SELECT seen FROM occurrences').fetchone()['seen']==20


def test_imported_archive_shares_project_and_file_compiler_reads_per_batch(library,monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import twins
    net,lib=library
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,status,size,vram,matched_source) VALUES(3,1,'f3','matched',32,4096,'int f3(void) {return 43;}')")
    monkeypatch.setattr(net,'fork_slug',lambda slug:slug if slug=='game1' else None)
    projects=[];compilers=[]
    def get(slug):
        projects.append(slug);return net.project_row(slug),SimpleNamespace(),{}
    def compiler(*args):
        compilers.append(args);return 'file-compiler'
    monkeypatch.setattr(net.forks,'get',get)
    monkeypatch.setattr(twins,'_file_compiler',compiler)
    lib.archive_project(net.project_row('game1'))
    assert projects==['game1'] and len(compilers)==1
    assert lib.summary()['occurrences']==2


def test_archive_looks_up_used_symbols_without_scanning_table(library,monkeypatch):
    from types import SimpleNamespace

    from decompnet.rom.symbols import Symbol
    net,lib=library
    class Names(dict):
        def values(self):raise AssertionError('Archive must not scan every symbol for each function')
    names=Names({f'unused{i}':Symbol(f'unused{i}',i,4,'data') for i in range(1000)})
    names['used']=Symbol('used',4096,4,'data')
    monkeypatch.setattr(net,'symbols',lambda *_:SimpleNamespace(by_name=names))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f1(void) {return used;}' WHERE id=1")
    item=lib.snapshot(net.project_row('game1'),1)
    assert [r['name'] for r in json.loads(item['symbols'])]==['used']


def test_archive_batch_hashes_repeated_text_once_and_refreshes_project(library,monkeypatch):
    from decompnet.app import match_library
    net,lib=library
    item=lib.snapshot(net.project_row('game1'),1)
    digest=match_library.hashlib.sha256;calls=[]
    def hash_text(value):
        calls.append(value)
        return digest(value)
    with monkeypatch.context() as patch:
        patch.setattr(match_library.hashlib,'sha256',hash_text)
        lib.write([item,item])
    assert len(calls)==len({item[k] for k in ('source','header','print','symbols')})
    with net.store.tx() as db:db.execute("UPDATE projects SET name='Changed title' WHERE id=1")
    assert lib.snapshot(net.project_row('game1'),1)['game_name']=='Changed title'


def test_open_donor_matches_before_any_live_project_reindex(library,monkeypatch):
    net,lib=library
    monkeypatch.setattr(net,'is_rom',lambda _:True)
    lib.archive_project(net.project_row('game1'))
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE id=2")
    monkeypatch.setattr(net.twins,'index_project',lambda *_:pytest.fail('first match must use the saved index'))
    def accept(agent,proj,a,b,source,**kwargs):
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched' WHERE id=?",(b['id'],))
        return 'match','Destination bytes verified'
    monkeypatch.setattr(net.twins,'_attempt_twin',accept)
    assert net.twins.across_games('game2')==1
    results=net.twins.cross_game_results('game2')
    assert results['counts']['match']==1 and results['matches'][0]['game']=='First game'


def test_library_shortlists_shared_fingerprints_before_fetching_source(library,monkeypatch):
    net,lib=library
    lib.archive_project(net.project_row('game1'))
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL WHERE id=2")
        db.executemany("INSERT INTO functions(id,project_id,name,status,size,vram) VALUES(?,2,?,'open',32,4096)",[(i,f'unrelated_{i}') for i in range(3,103)])
    prints={i:Print('same-code' if i==2 else f'unrelated-{i}',[],32) for i in range(2,103)}
    monkeypatch.setattr(net.twins,'prints',lambda _:prints)
    queried=[];candidates=lib.candidates
    monkeypatch.setattr(lib,'candidates',lambda *args:(queried.append(args[2]),candidates(*args))[-1])
    monkeypatch.setattr(net.twins,'_attempt_twin',lambda *args,**kwargs:('match','verified'))
    assert lib.reuse(net.twins,net.project_row('game2'))==1
    assert queried==['same-code']

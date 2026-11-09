"""Bounded metadata and effective-header reads retain exact validation inputs."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from decompnet import match_bundle, payload_storage
from decompnet.app import source_lead_analysis
from decompnet.app.engine.briefs import BriefMixin
from tests.test_claims import _game
from tests.test_match_bundle import setup_bundle


def test_effective_header_filter_skips_unrelated_packed_bodies(tmp_path):
    net,pid=_game(tmp_path,[(name,4,'matched') for name in ('shared','fallback','orphan','local','open')])
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','local.h')",(pid,))
        for name,unit in [('shared',''),('fallback','default.c'),('orphan','orphan.c'),('local','local.h'),('open','')]:
            db.execute('UPDATE functions SET unit=?,matched_source=? WHERE name=?',(unit,'body '+name,name))
        db.execute("UPDATE functions SET status='open' WHERE name='open'")
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(2,'other','Other','mips','.','{}')")
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(2,'other_shared','','matched')")
        db.execute("UPDATE functions SET asm=? WHERE name='local'",('corrupt unrelated assembly '*100,))
        db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=?",(payload_storage.digest('corrupt unrelated assembly '*100),))
    proj=net.project_row('game')
    assert {f['name'] for f in net._matched_using(proj,'')}=={'shared','fallback','orphan'}
    assert net._matched_using(proj,'orphan.c')==[]
    # Filtering must not conceal damage in a body that really is affected.
    with pytest.raises(sqlite3.OperationalError):net._matched_using(proj,'local.h')
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,2,'','orphan.c')",(pid,))
            assert {f['name'] for f in net._matched_using(proj,'')}=={'shared','fallback'}
            assert [f['name'] for f in net._matched_using(proj,'orphan.c')]==['orphan']
            raise ValueError('rollback')
    assert {f['name'] for f in net._matched_using(proj,'')}=={'shared','fallback','orphan'}


def test_verification_symbol_transport_retains_capture_hash_and_invalidation(tmp_path,monkeypatch):
    net,_=setup_bundle(tmp_path)
    with net.store.tx() as db:
        db.executemany('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,?)',
                       [('名字\0λ',2**60+1,4,'data'),('alpha',4,0,'data')])
    agent,_=net.local_agent('capture')
    fn=net._function('f','game');net._record(fn,agent,0,fn['matched_source'],1,matched=True)
    original=net.store.all
    def read(sql,*args):
        assert 'FROM symbols' not in sql,'symbols must use batched metadata'
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    assert match_bundle.status(net,net._function('f','game'))['changed_inputs']==[]
    with pytest.raises(ValueError):
        with net.store.tx() as db:
            db.execute("UPDATE symbols SET address=99 WHERE name='alpha'")
            assert 'symbol table changed' in match_bundle.status(net,net._function('f','game'))['changed_inputs']
            raise ValueError('rollback')
    assert match_bundle.status(net,net._function('f','game'))['changed_inputs']==[]


def test_source_scope_preserves_legacy_key_without_native_symbol_rows(tmp_path,monkeypatch):
    from decompnet.app.permuter_inputs import build_state
    from decompnet.project import compiler_for
    net,_=_game(tmp_path,[('f',4,'open')]);proj=net.project_row('game');fn=net._function('f','game')
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'名字',?,4,'data')",(2**60+1,))
    cfg=json.loads(proj['config']);ctx=net._context(proj['id'],fn['unit']) or {'header':''}
    symbols=[tuple(r) for r in net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])]
    expected=source_lead_analysis.hashlib.sha256(repr((compiler_for(cfg,fn['vram'],fn) if cfg.get('compiler') else {},cfg.get('gp'),proj['arch'],fn['target'],fn['relocs'],fn['size'],fn['vram'],ctx['header'],symbols,build_state(proj))).encode()).hexdigest()
    original=net.store.all
    def read(sql,*args):
        assert 'FROM symbols' not in sql
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    assert source_lead_analysis.destination(net,proj,fn)==expected
    with net.store.tx() as db:db.execute('UPDATE symbols SET address=42')
    assert source_lead_analysis.destination(net,proj,fn)!=expected


def test_brief_reads_only_first_forty_referenced_previews(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',4,'matched')]);proj=net.project_row('game')
    with net.store.tx() as db:
        db.executemany('INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(?,?,?,?,?,?)',
                       [(pid,f'data{i}',1000+i,4,'data',f'preview{i}') for i in range(200)])
        db.execute('INSERT INTO projects(id,slug,name,arch,root,config) VALUES(2,\'other\',\'Other\',\'mips\',\'.\',\'{}\')')
        db.execute("INSERT INTO symbols(project_id,name,address,preview) VALUES(2,'data0',1000,'other project')")
    monkeypatch.setattr(net,'is_rom',lambda p:True);monkeypatch.setattr(net,'warm_briefs',lambda p:None)
    monkeypatch.setattr(net,'symbols',lambda p:SimpleNamespace(name_at=lambda a:(f'data{a-1000}',0),label=lambda a:f'data{a-1000}'))
    monkeypatch.setattr(net,'xrefs',lambda p:{'calls':{},'called_by':{},'uses':{4096:list(range(1000,1050))}})
    for name in ('_debug_brief','_class_brief','_types_brief','_compiler_brief','_struct_brief'):monkeypatch.setattr(net,name,lambda *a:[])
    monkeypatch.setattr(net,'_twin_lines',lambda *a:[]);monkeypatch.setattr(net,'similar',lambda *a,**k:[])
    original=net.store.all;fetched=[]
    def read(sql,*args):
        rows=original(sql,*args)
        if 'preview FROM symbols' in sql:fetched.extend(rows)
        return rows
    monkeypatch.setattr(net.store,'all',read)
    fn=dict(net._function('f','game'))|{'vram':4096,'rom_offset':0,'unit':'file.c'}
    text='\n'.join(BriefMixin._extra_brief(net,fn,proj))
    assert len(fetched)==40 and 'preview0' in text and 'preview39' in text
    assert 'preview40' not in text and 'other project' not in text

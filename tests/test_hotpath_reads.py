"""Bounded reads and revision invalidation for the measured indexing/UI hot paths."""
import json

import pytest

from decompnet.app import code_audit_reviews, collab, named_donors
from decompnet.app.engine import RomNetwork


def network(tmp_path):
    net=RomNetwork(tmp_path/'db',tmp_path)
    with net.store.tx() as db:
        for pid in (1,2):
            db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(?,?,?,'mips',?,?)",
                       (pid,f'game{pid}',f'Game {pid}',str(tmp_path),json.dumps({'kind':'rom','rom':{}})))
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'global','')",(pid,))
        db.execute("INSERT INTO functions(id,project_id,name,status,vram,size,rom_offset,best_source,best_score) "
                   "VALUES(1,1,'work','open',4096,4,0,'draft',10)")
        db.execute("INSERT INTO functions(id,project_id,name,status) VALUES(2,2,'other','open')")
    return net


def test_latest_header_cache_fallback_invalidation_and_rollback(tmp_path,monkeypatch):
    net=network(tmp_path);calls=[];one=net.store.one
    def read(sql,*args):
        if 'FROM context_versions' in sql:calls.append(sql)
        return one(sql,*args)
    monkeypatch.setattr(net.store,'one',read)
    assert net._context(1,'unit')['header']=='global'
    assert net._context(1,'unit')['header']=='global'
    assert len(calls)==2  # one exact-unit miss and one indexed global fallback
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,2,'private','unit')")
    assert net._context(1,'unit')['header']=='private'
    with pytest.raises(RuntimeError):
        with net.store.tx() as db:
            db.execute("UPDATE context_versions SET header='rollback' WHERE project_id=1 AND version=2")
            assert net._context(1,'unit')['header']=='rollback'
            raise RuntimeError()
    assert net._context(1,'unit')['header']=='private'
    with net.store.tx() as db:
        db.execute("DELETE FROM context_versions WHERE project_id=1 AND unit='unit'")
    assert net._context(1,'unit')['header']=='global'
    plan=net.store.all("EXPLAIN QUERY PLAN SELECT * FROM context_versions WHERE project_id=1 AND unit='unit' ORDER BY version DESC LIMIT 1")
    assert not any('TEMP B-TREE' in r['detail'] for r in plan)


def test_donor_full_rows_are_bounded_after_filter_and_cursor_rotation(tmp_path,monkeypatch):
    net=network(tmp_path)
    with net.store.tx() as db:
        for fid in range(3,103):
            db.execute("INSERT INTO functions(id,project_id,name,status,target) VALUES(?,1,?,'open',?)",
                       (fid,f'work{fid}',bytes(8192)))
        db.execute("INSERT INTO settings VALUES('named-donor-cursor:1','10')")
    monkeypatch.setattr(named_donors,'MAX_FUNCTIONS',4)
    all_rows=net.store.all;loaded=[]
    def read(sql,*args):
        rows=all_rows(sql,*args)
        if sql.startswith('SELECT * FROM functions'):loaded.append(len(rows))
        return rows
    monkeypatch.setattr(net.store,'all',read)
    rows=named_donors.targets(net,net.project_row('game1'),{4,8,12,15,16,20})
    assert [r['id'] for r in rows]==[12,15,16,20]
    assert loaded==[4]


def test_audit_layout_cache_ignores_status_but_expires_on_layout_or_inputs(tmp_path,monkeypatch):
    net=network(tmp_path);all_rows=net.store.all;reads=[]
    def read(sql,*args):
        if sql.startswith('SELECT name,vram,rom_offset,size'):reads.append(sql)
        return all_rows(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    original=code_audit_reviews.revision(net,1)
    assert code_audit_reviews.revision(net,1)==original
    with net.store.tx() as db:db.execute("UPDATE functions SET status='matched' WHERE id=1")
    assert code_audit_reviews.revision(net,1)==original
    assert len(reads)==1
    with net.store.tx() as db:db.execute('UPDATE functions SET size=8 WHERE id=1')
    changed=code_audit_reviews.revision(net,1)
    assert changed!=original and len(reads)==2
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=1',(json.dumps({'rom':{'sha1':'new'}}),))
    assert code_audit_reviews.revision(net,1)!=changed
    assert len(reads)==2


def test_room_attempt_delta_captures_cursor_before_concurrent_insert(tmp_path,monkeypatch):
    net=network(tmp_path);room=collab.Room(net,'game1')
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,score) VALUES(1,10)')
        db.execute('INSERT INTO attempts(function_id,score) VALUES(2,20)')
    first=room.state(1,0,1,None)
    assert first['a']==2 and [r['name'] for r in first['best']]==['work']
    one=net.store.one;inserted=[]
    def read(sql,*args):
        row=one(sql,*args)
        if sql=='SELECT MAX(id) AS m FROM attempts' and not inserted:
            with net.store.tx() as db:
                inserted.append(db.execute('INSERT INTO attempts(function_id,score) VALUES(1,5)').lastrowid)
                db.execute("UPDATE functions SET best_source='better',best_score=5 WHERE id=1")
        return row
    monkeypatch.setattr(net.store,'one',read)
    current=room.state(1,first['a'],1,None)
    assert current['a']==2 and not current['best']
    following=room.state(1,current['a'],1,None)
    assert following['a']==inserted[0]
    assert following['best'][0]['source']=='better'


def test_room_match_updates_fetch_only_changed_function(tmp_path,monkeypatch):
    net=network(tmp_path);room=collab.Room(net,'game1')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='verified' WHERE id=1")
        db.execute("INSERT INTO functions(project_id,name,status,matched_source) VALUES(1,'unrelated','matched','other')")
        net._event(db,1,None,'match','work')
    # Event cursor 1 is before this new match but avoids requesting an initial snapshot.
    event=net.store.one("SELECT MAX(id) AS id FROM events")['id']
    with net.store.tx() as db:net._event(db,1,None,'match','work')
    all_rows=net.store.all;loaded=[]
    def read(sql,*args):
        rows=all_rows(sql,*args)
        if 'f.matched_source, a.name AS agent' in sql:loaded.append(len(rows))
        return rows
    monkeypatch.setattr(net.store,'all',read)
    state=room.state(event,0,1,None)
    assert [r['name'] for r in state['matched']]==['work']
    assert loaded==[1]

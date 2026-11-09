"""Listing room evidence must not scan unrelated settings or load private candidate bodies."""
import json

from decompnet.app import room_matches
from decompnet.app.engine import RomNetwork


def test_pending_list_uses_project_key_index_and_projects_only_metadata(tmp_path, monkeypatch):
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                       ('room','Room',str(tmp_path),'{}')).lastrowid
        prefix=f'room-match:{pid}:'
        items=[('accepted',{'status':'accepted','created_at':0,'source':'private '*20000}),
               ('later',{'status':'pending','created_at':2,'function':'later','agent':'client','direction':'client','reason':'waiting','source':'private source','evidence':{'header':'private header'}}),
               ('first',{'status':'pending','created_at':1,'function':'first','agent':'host','direction':'host','reason':'retry','updated_at':3,'source':'first body'})]
        for key,item in items:db.execute('INSERT INTO settings VALUES(?,?)',(prefix+key,json.dumps(item)))
        # Both adjacent project prefixes and unrelated settings must be excluded before JSON decoding.
        for key in ('unrelated',f'room-match:{pid}0:other',f'room-match:{pid+1}:other'):
            db.execute('INSERT INTO settings VALUES(?,?)',(key,'not JSON'))
    original=net.store.all
    queries=[]
    def read(sql,*args):
        rows=original(sql,*args)
        if 'FROM settings WHERE key >=' in sql:
            queries.append((sql,args))
            if 'SELECT key,value' not in sql:
                assert all(set(row.keys())=={'key','function','agent','direction','reason','updated_at'} for row in rows)
        return rows
    monkeypatch.setattr(net.store,'all',read)
    with net.progress.view():
        public=room_matches.public(net,'room')
        assert [r['function'] for r in public]==['first','later']
        assert public[1]['updated_at'] is None
        assert [r['function'] for r in room_matches.pending(net,'room')]==['first','later']
        assert [r['source'] for r in room_matches.pending(net,'room','client')]==['private source']
    assert len(queries)==3
    for sql,args in queries:
        plan=original('EXPLAIN QUERY PLAN '+sql,*args)
        assert any('SEARCH settings USING INDEX' in row['detail'] for row in plan)
        assert not any('SCAN settings' in row['detail'] for row in plan)

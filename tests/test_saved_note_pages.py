"""Saved note paging preserves the original single-snapshot export values."""
import math

import pytest

from decompnet.app.saves import _note_rows
from decompnet.store import Store

OLD = ('SELECT n.text,n.created_at,n.score_before,n.score_after,n.agent_id,f.name AS fn '
       'FROM notes n LEFT JOIN functions f ON f.id=n.function_id WHERE n.project_id=? ORDER BY n.id')


def test_note_pages_keep_order_names_unicode_nulls_and_full_timestamp_precision(tmp_path,monkeypatch):
    store=Store(tmp_path/'notes.sqlite3')
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'first','','{}'),(2,'second','','{}')")
        db.execute("INSERT INTO functions(id,project_id,name) VALUES(1,1,'named_函数')")
        for i in reversed(range(300)):
            db.execute('INSERT INTO notes(id,project_id,function_id,agent_id,text,created_at,score_before,score_after) '
                       'VALUES(?,1,?,?,?,?,?,?)',(2**53+i,1 if i%2 else None,7 if i%3 else None,
                       ('note \\ "\n\0 λ '+str(i))*80 if i%5 else None,
                       math.nextafter(1794177170.1234567,float('inf'))+i/1000 if i%7 else None,
                       2**60+i if i%3 else None,0 if i%2 else None))
        db.execute("INSERT INTO notes(project_id,text,created_at) VALUES(2,'foreign note',1)")
        db.execute("INSERT INTO notes(project_id,text,created_at) VALUES(1,'legacy time','unknown')")
    expected=[dict(row) for row in store.all(OLD,1)]
    fetched=[];read=store.all
    def counted(sql,*args):
        rows=read(sql,*args)
        if 'json_group_array' in sql:fetched.append(len(rows))
        return rows
    monkeypatch.setattr(store,'all',counted)
    try:
        actual=_note_rows(store,1)
        assert actual==expected
        assert fetched==[3]  # 301 notes, transported in <=128-row pages.
        assert 'foreign note' not in str(actual)
        assert _note_rows(store,999)==[]
    finally:store.conn.close()


def test_note_pages_see_transaction_changes_without_leaking_rollback(tmp_path):
    store=Store(tmp_path/'notes.sqlite3')
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'first','','{}')")
        db.execute("INSERT INTO notes(project_id,text,created_at) VALUES(1,'before',1.125)")
    try:
        before=_note_rows(store,1)
        with pytest.raises(ValueError,match='rollback'):
            with store.tx() as db:
                db.execute("UPDATE notes SET text='uncommitted' WHERE project_id=1")
                assert _note_rows(store,1)[0]['text']=='uncommitted'
                raise ValueError('rollback')
        assert _note_rows(store,1)==before
    finally:store.conn.close()

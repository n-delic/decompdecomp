"""Metadata batching preserves SQL semantics without per-row Python fetching."""
import pytest

from decompnet.app.metadata_rows import rows
from decompnet.store import Store


def test_batch_preserves_order_limits_nulls_unicode_and_parameters(tmp_path):
    store=Store(tmp_path/'db')
    with store.tx() as db:
        db.execute('CREATE TABLE metadata_test(id INTEGER,value TEXT,n REAL)')
        db.executemany('INSERT INTO metadata_test VALUES(?,?,?)',[(1,None,None),(2,'雪 "é"',1.25),(3,'last',0)])
    query='SELECT id,value,n FROM metadata_test WHERE id>=? ORDER BY id DESC LIMIT 2'
    expected=[dict(r) for r in store.all(query,1)]
    assert rows(store,('id','value','n'),query,1)==expected
    assert rows(store,('id','value','n'),query,4)==[]
    assert rows(store,('id','value','n'),'SELECT id,value,n FROM metadata_test WHERE id=1')==[{'id':1,'value':None,'n':None}]


@pytest.mark.parametrize('columns',[(),('not a column',),('id);DROP TABLE functions;',)])
def test_only_simple_column_identifiers_are_accepted(tmp_path,columns):
    with pytest.raises(ValueError):rows(Store(tmp_path/'db'),columns,'SELECT 1')

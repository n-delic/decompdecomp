"""Saved symbol export batches metadata without changing names, units or store ownership."""
import pytest

from decompnet.rom.symbols import AUTO_NAME
from tests.test_project_storage import scoped  # noqa: F401


def legacy(net, pid):
    store=net.project_store(pid)
    twins={r['name']:r['unit'] for r in store.all(
        'SELECT name,unit FROM functions WHERE project_id=? AND vram IN '
        '(SELECT vram FROM functions WHERE project_id=? GROUP BY vram HAVING COUNT(*)>1)',pid,pid)}
    return [dict(r)|({'unit':twins[r['name']]} if r['name'] in twins else {}) for r in store.all(
        'SELECT name,address,size,kind FROM symbols WHERE project_id=?',pid) if not AUTO_NAME.match(r['name'])]


def test_batches_symbols_and_reads_only_exported_overlay_names(scoped, monkeypatch):  # noqa: F811
    net,stores=scoped;store=stores[1]
    with store.tx() as db:
        db.execute("UPDATE functions SET unit='first-unit',vram=4096 WHERE id=10")
        db.execute("INSERT INTO functions(id,project_id,name,unit,vram) VALUES(11,1,'named_copy','second-unit',4096)")
        for i in range(300):
            name=f'func_{i:08X}_ovl_01'
            db.execute("INSERT INTO functions(project_id,name,unit,vram) VALUES(1,?,'unused',4096)",(name,))
            db.execute("INSERT INTO symbols(project_id,name,address) VALUES(1,?,4096)",(name,))
        db.executemany('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,?)',
                       [('same_name',4096,4,'func'),('named_copy',4096,4,'func'),
                        ('名字\0λ',2**60+1,2**60+3,'data'),('func_ABCD_tail',19,0,'user'),
                        ('func_ABC_ovl_1',20,0,'user'),('D_1',21,4,'generated')])
    expected=legacy(net,1)
    reads=[];all_rows=store.all
    def counted(sql,*args):
        rows=all_rows(sql,*args);reads.append((sql,len(rows)));return rows
    monkeypatch.setattr(store,'all',counted)
    monkeypatch.setattr(net.store,'all',lambda *a:pytest.fail('export read shared catalog'))
    assert net.layout_symbols(1)==expected
    assert sum(n for _,n in reads)==2  # Only named overlay functions cross as native rows.
    assert all('json_each' in sql for sql,_ in reads)
    assert len(expected)==5
    assert net.layout_symbols(2)==[]  # Other project has no symbols, despite its own same_name function.


def test_generated_only_symbols_skip_overlay_scan(scoped, monkeypatch):  # noqa: F811
    net,stores=scoped
    with stores[1].tx() as db:
        db.executemany('INSERT INTO symbols(project_id,name,address) VALUES(1,?,1)',
                       [('func_1',),('D_ABC',),('byte_12_ovl_001',),('STR_FF_overlay_012',)])
    monkeypatch.setattr(stores[1],'all',lambda *a:pytest.fail('generated-only export scanned function overlays'))
    assert net.layout_symbols(1)==[]


def test_layout_sees_transaction_edits_and_rollback_without_stale_cache(scoped):  # noqa: F811
    net,stores=scoped;store=stores[1]
    with store.tx() as db:db.execute("INSERT INTO symbols(project_id,name,address) VALUES(1,'named',3)")
    before=net.layout_symbols(1)
    with pytest.raises(ValueError,match='rollback'):
        with store.tx() as db:
            db.execute("UPDATE symbols SET address=99 WHERE project_id=1")
            assert net.layout_symbols(1)[0]['address']==99
            raise ValueError('rollback')
    assert net.layout_symbols(1)==before


def test_candidate_index_keeps_exact_symbol_tuples_and_transaction_invalidation(scoped,monkeypatch):  # noqa: F811
    from decompnet import candidate_scope
    net,stores=scoped;store=stores[1]
    with store.tx() as db:
        db.execute("UPDATE functions SET best_source='named_data' WHERE id=10")
        db.executemany('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,?)',
                       [('same_name',2**60+1,4,'func'),('named_data',2**60+3,2**60+5,'данные'),('unrelated',32,0,'data')])
    proj=store.one('SELECT * FROM projects WHERE id=1')
    fn=store.one('SELECT * FROM functions WHERE id=10')
    expected=[tuple(row) for row in store.all('SELECT name,address,size,kind FROM symbols WHERE name!=\'unrelated\' ORDER BY name')]
    all_rows=store.all
    def scoped_rows(sql,*args):
        assert not sql.startswith('SELECT name,address,size,kind FROM symbols'), 'symbol index crossed one native row per symbol'
        return all_rows(sql,*args)
    monkeypatch.setattr(store,'all',scoped_rows)
    before=candidate_scope.state(net,proj,fn)[5]
    assert before==expected
    with pytest.raises(ValueError,match='rollback'):
        with store.tx() as db:
            db.execute("UPDATE symbols SET address=99 WHERE name='named_data'")
            assert candidate_scope.state(net,proj,fn)[5] != before
            raise ValueError('rollback')
    assert candidate_scope.state(net,proj,fn)[5] == before

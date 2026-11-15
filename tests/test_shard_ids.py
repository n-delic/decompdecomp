"""Independent project writers keep globally unique, exact row IDs without catalog locks."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet import shard_ids
from decompnet.repair_archive import Archive
from decompnet.store import Store


def project(path, namespace, archive):
    store = Store(path,project_namespace=namespace,repair_archive=archive)
    with store.tx() as db:db.execute("INSERT OR IGNORE INTO projects(id,slug,root,config) VALUES(?,?,'.','{}')",(namespace,str(namespace)))
    return store


def add(store, pid, name):
    with store.tx() as db:
        return db.execute('INSERT INTO functions(project_id,name) VALUES(?,?)',(pid,name)).lastrowid


def test_project_ranges_preserve_legacy_ids_and_sqlite_lastrowid(tmp_path):
    archive = Archive(tmp_path/'repair-evidence.sqlite3')
    first,second = [project(tmp_path/f'{pid}.db',pid,archive) for pid in (1,2)]
    for pid,store in ((1,first),(2,second)):
        with store.tx() as db:db.execute('INSERT INTO functions(id,project_id,name) VALUES(?,?,?)',(pid,pid,'legacy'))
    a,b = add(first,1,'new'),add(second,2,'new')
    assert a == shard_ids.BLOCK+1 and b == 2*shard_ids.BLOCK+1
    assert first.one('SELECT name FROM functions WHERE id=?',a)['name'] == 'new'
    assert second.one('SELECT name FROM functions WHERE id=?',b)['name'] == 'new'
    assert first.one('SELECT id FROM functions WHERE name=?','legacy')['id'] == 1
    assert json.loads(json.dumps([a,b])) == [a,b]
    assert b < 2**53


def test_dynamic_schema_and_scripts_allocate_before_inserts_and_preserve_literals(tmp_path):
    store = project(tmp_path/'project.db',3,Archive(':memory:'))
    with store.tx() as db:
        db.execute('''-- a late extension
            CREATE TABLE extension("id" INTEGER /* keep comment */ PRIMARY KEY,
                                   text TEXT DEFAULT 'id INTEGER PRIMARY KEY')''')
        identity = db.execute('INSERT INTO extension DEFAULT VALUES').lastrowid
    assert identity == 3*shard_ids.BLOCK+1
    assert store.one('SELECT text FROM extension')['text'] == 'id INTEGER PRIMARY KEY'
    with store.writer_gate.hold(),store.lock:
        store.conn.executescript('''BEGIN;
            CREATE TABLE scripted(id INTEGER PRIMARY KEY, value TEXT);
            INSERT INTO scripted(value) VALUES('text; not a statement');
            CREATE TRIGGER scripted_text AFTER INSERT ON scripted BEGIN
              UPDATE scripted SET value=value || ';done' WHERE id=NEW.id;
            END;
            INSERT INTO scripted(value) VALUES('second'); COMMIT;''')
    rows = store.all('SELECT * FROM scripted ORDER BY id')
    assert [r['id'] for r in rows] == [3*shard_ids.BLOCK+1,3*shard_ids.BLOCK+2]
    assert rows[1]['value'] == 'second;done'


def test_deleted_ids_are_not_reused_after_restart_and_wrong_namespace_is_rejected(tmp_path):
    archive = Archive(':memory:')
    path = tmp_path/'project.db'
    store = project(path,4,archive)
    old = add(store,4,'deleted')
    with store.tx() as db:db.execute('DELETE FROM functions')
    store.conn.close()
    restored = Store(path,project_namespace=4,repair_archive=archive)
    assert add(restored,4,'restored') == old+1
    for namespace in (None,5):
        with pytest.raises(ValueError,match='original catalog identifier'):
            Store(path,project_namespace=namespace,repair_archive=archive)


def test_project_writers_never_wait_for_catalog_writer_and_concurrent_ids_are_unique(tmp_path):
    catalog = Store(tmp_path/'catalog.db')
    archive = catalog.conn.repair_archive
    path = tmp_path/'project.db'
    first = project(path,5,archive)
    second = Store(path,project_namespace=5,repair_archive=archive)
    with ThreadPoolExecutor(max_workers=4) as pool:
        with catalog.tx():
            futures = [pool.submit(add,first if n%2 else second,5,str(n)) for n in range(40)]
            ids = [f.result(timeout=5) for f in futures]
    assert len(set(ids)) == 40 and min(ids) == 5*shard_ids.BLOCK+1
    assert max(ids) == min(ids)+39
    assert catalog.one('SELECT COUNT(*) n FROM functions')['n'] == 0


def test_rollback_and_range_exhaustion_are_atomic(tmp_path):
    store = project(tmp_path/'project.db',6,Archive(':memory:'))
    with pytest.raises(RuntimeError):
        with store.tx() as db:
            db.execute("INSERT INTO functions(project_id,name) VALUES(6,'rollback')")
            raise RuntimeError()
    assert not store.one('SELECT 1 FROM functions')
    maximum = shard_ids.bounds(6)[1]
    with store.tx() as db:db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='functions'",(maximum,))
    with pytest.raises(sqlite3.IntegrityError,match='range exhausted'):add(store,6,'overflow')
    assert not store.one('SELECT 1 FROM functions')
    with pytest.raises(sqlite3.IntegrityError,match='incorrect project'):
        with store.tx() as db:db.execute('INSERT INTO functions(id,project_id,name) VALUES(?,?,?)',(2*shard_ids.BLOCK+1,6,'wrong owner'))


def test_legacy_files_require_real_migration_and_namespaces_require_shared_archive(tmp_path):
    legacy = Store(tmp_path/'legacy.db')
    with pytest.raises(ValueError,match='must be migrated'):
        Store(legacy.path,project_namespace=1,repair_archive=Archive(':memory:'))
    with pytest.raises(ValueError,match='shared repair archive'):Store(tmp_path/'new.db',project_namespace=1)
    for invalid in (0,-1,True,2**21,'1'):
        with pytest.raises(ValueError,match='namespace'):shard_ids.bounds(invalid)


def test_rolled_back_schema_creation_does_not_skip_next_table_seed(tmp_path):
    store = project(tmp_path/'project.db',7,Archive(':memory:'))
    with pytest.raises(RuntimeError):
        with store.tx() as db:
            db.execute('CREATE TABLE abandoned(id INTEGER PRIMARY KEY)')
            raise RuntimeError()
    with store.tx() as db:
        db.execute('CREATE TABLE replacement(id INTEGER PRIMARY KEY)')
        identity = db.execute('INSERT INTO replacement DEFAULT VALUES').lastrowid
    assert identity == 7*shard_ids.BLOCK+1
    assert not store.one("SELECT 1 FROM sqlite_master WHERE name='abandoned'")


def test_catalog_stable_schema_flag_survives_restart_without_project_namespace(tmp_path):
    path = tmp_path/'catalog.db'
    store = Store(path,stable_ids=True)
    with store.tx() as db:
        old = db.execute("INSERT INTO agents(name,token_hash) VALUES('old','old')").lastrowid
        db.execute('DELETE FROM agents WHERE id=?',(old,))
    store.conn.close()
    restored = Store(path)
    with restored.tx() as db:
        identity = db.execute("INSERT INTO agents(name,token_hash) VALUES('new','new')").lastrowid
        db.execute('CREATE TABLE extension(id INTEGER PRIMARY KEY)')
    assert identity > old
    assert 'AUTOINCREMENT' in restored.one("SELECT sql FROM sqlite_master WHERE name='extension'")['sql']

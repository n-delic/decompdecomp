"""A pass-local donor cache keeps the old SQL shortlist semantics without repeated source reads."""
import pytest

from decompnet.app.reuse_donors import Donors
from tests.test_similar_matches import pair as pair_fixture

pair = pytest.fixture(pair_fixture.__wrapped__)


def test_shortlist_keeps_order_limits_and_reloads_on_a_new_pass(pair, monkeypatch):
    proj = pair._project('destination')
    pair.twins.index_project(pair._project('source'))
    donors = Donors(pair, proj)
    original = pair.store.one
    reads = []
    def one(sql, *args):
        if sql.startswith('SELECT * FROM functions WHERE id='):
            reads.append(args)
        return original(sql, *args)
    monkeypatch.setattr(pair.store, 'one', one)
    first = donors.candidates(32, 0, 3200, '', '', 400)
    assert first and first[0]['id'] == 1
    assert donors.candidates(32, 0, 3200, '', '', 400) == first
    assert len(reads) == 1
    assert not donors.candidates(32, 0, 3200, first[0]['fingerprint'], first[0]['compiler'], 400)
    assert not donors.candidates(32, 0, 3200, '', '', 0)
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=1")
    assert not Donors(pair, proj).candidates(32, 0, 3200, '', '', 400)


def test_donor_metadata_uses_one_native_row_and_defers_source_reads(pair, monkeypatch):
    from decompnet import payload_storage
    pair.twins.index_project(pair._project('source'))
    store=pair.project_store('source')
    with store.tx() as db:
        for index in range(257):
            fid=db.execute("INSERT INTO functions(project_id,name,size,vram,status,matched_source) VALUES(1,?,32,?,'matched',?)",
                           (f'donor_{index}',0x80002000+index*4,'int unused(void){return 1;}\n'*200)).lastrowid
            db.execute('INSERT INTO cross_game_index(function_id,project_id,size,compiler,fingerprint,matched_at) VALUES(?,1,32,?,?,0)',
                       (fid, 'compiler-α', f'fingerprint-{index}'))
    expected=[dict(row) for row in store.all('SELECT i.function_id,i.project_id,i.size,i.compiler,i.fingerprint,f.vram '
        "FROM cross_game_index i JOIN functions f ON f.id=i.function_id AND f.status='matched' WHERE i.project_id=1")]
    expected.sort(key=lambda row:(row['size'],row['function_id']))
    native=[];original=store.one
    def one(sql,*args):
        if 'AS metadata' in sql and 'cross_game_index' in sql:native.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(store,'one',one)
    monkeypatch.setattr(payload_storage,'unpack',lambda *a:pytest.fail('metadata must not decode donor bodies'))
    donors=Donors(pair,pair._project('destination'))
    assert donors.rows==expected
    assert len(native)==1
    assert donors.bodies=={}


def test_reindex_metadata_never_inflates_matched_bodies_but_body_use_still_validates(pair,monkeypatch):
    from types import SimpleNamespace

    from decompnet import payload_storage
    store=pair.project_store('source')
    source='int retained(void){return 1;}\n'*500
    with store.tx() as db:db.execute('UPDATE functions SET matched_source=? WHERE id=1',(source,))
    source_project=pair._project('source')
    destination_project=pair._project('destination')
    monkeypatch.setattr(pair.twins,'prints',lambda *a:{1:SimpleNamespace(key='fingerprint',size=32,asm=False)})
    original=payload_storage.unpack
    decoded=[]
    def read_payload(*args):
        decoded.append(args[0])
        return original(*args)
    # Existing reader connections hold their registered callback, so replacing
    # the module attribute alone would not observe SQLite's detail-view reads.
    reader=store._reader() or store.conn
    reader.create_function('payload_read',2,read_payload,deterministic=True)
    assert pair.twins.index_project(source_project)==1
    assert store.one('SELECT function_id FROM cross_game_index')['function_id']==1
    assert pair.twins.index_project(source_project)==1
    assert not decoded
    reader.create_function('payload_read',2,original,deterministic=True)
    # A metadata index is never a source-validation shortcut. A missing body must
    # still fail the selected donor read before any destination judgment.
    with store.tx() as db:db.execute('DELETE FROM text_payloads WHERE hash=?',(payload_storage.digest(source),))
    with pytest.raises(Exception,match='user-defined function'):
        Donors(pair,destination_project).candidates(32,0,3200,'','',1)

import json
from types import SimpleNamespace

import pytest

from decompnet.app import wrapper_families as wrappers


def fn(address,name='f',id=1,big=True):
    words=[0x27bdffe0,0x00a02021,0x3c060000|((address+0x8000)>>16),0x24c60000|(address&65535),
           0xafbf001c,0x0c002000,0x24050009,0x8fbf001c,0x24020007,0x03e00008,0x27bd0020]
    return {'id':id,'name':name,'target':b''.join(w.to_bytes(4,'big' if big else 'little') for w in words),
            'relocs':json.dumps([[8,5,address],[12,6,address],[20,4,0x80008000]]),'size':44}


@pytest.mark.parametrize('big',[False,True])
def test_grouping_changes_only_one_paired_address_with_identical_callee_and_instruction_shape(big):
    a,b=fn(0x123400,big=big),fn(0x125690,big=big)
    assert wrappers.shape(a,big)[0]==wrappers.shape(b,big)[0]
    changed=dict(b,target=b['target'][:-4]+(0).to_bytes(4,'big' if big else 'little'))
    assert wrappers.shape(a,big)[0]!=wrappers.shape(changed,big)[0]
    bad=dict(b,relocs='[[8,5,1],[12,6,2],[20,4,3]]')
    assert wrappers.shape(bad,big) is None


def test_verified_wrapper_c_is_instantiated_with_low_address_local_substitution_only():
    donor=fn(0x123400,'old');dest=fn(0x125690,'new',2)
    donor['matched_source']='extern int D_123400; void helper(int,int,int*); int old(int a,int b,int c){helper(c,b,&D_123400);return 7;}'
    symbols=SimpleNamespace(resolve=lambda name:0x123400 if name=='D_123400' else None,label=lambda address:'D_125690')
    net=SimpleNamespace(symbols=lambda pid:symbols)
    group={wrappers.shape(donor,True)[0]:[donor]}
    rows=wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)
    assert len(rows)==1 and rows[0][0]=='wrapper-family'
    assert 'extern int D_125690;' in rows[0][2] and 'int new(' in rows[0][2]
    assert 'helper(c,b,&D_125690)' in rows[0][2]
    donor['matched_source']=donor['matched_source'].replace('&D_123400','D_123400')
    assert not wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)


def test_index_reads_only_exact_donor_source_and_preserves_its_checksum(tmp_path,monkeypatch):
    import sqlite3

    from decompnet import payload_storage
    from decompnet.store import Store
    store=Store(tmp_path/'wrappers.db')
    donor=fn(0x123400)
    source='int f(void){return 7;} /*'+'matched '*100+'*/'
    unused='unused '*200
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
        for i in range(5):
            db.execute("INSERT INTO functions(id,project_id,name,status,size,target,relocs,asm,seed,best_source,matched_source) "
                       "VALUES(?,1,?,'matched',?,?,?,?,?,?,?)",
                       (i+1,f'f{i}',44,donor['target'],donor['relocs'],unused,unused,unused,source))
        db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=?",(payload_storage.digest(unused),))
    reads=[]
    unpack=payload_storage.unpack
    def decode(digest,blob):
        reads.append(digest)
        return unpack(digest,blob)
    monkeypatch.setattr(payload_storage,'unpack',decode)
    for conn in (store.conn,store._reader()):conn.create_function('payload_read',2,decode,deterministic=True)
    net=SimpleNamespace(project_store=lambda pid:store)
    try:
        groups=wrappers.index(net,{'id':1,'arch':'mips','big_endian':True})
        donors=next(iter(groups.values()))
        assert [row['id'] for row in donors]==[1,2,3]
        assert all(row['matched_source']==source for row in donors)
        assert reads==[payload_storage.digest(source)]
        with store.tx() as db:
            db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=?",(payload_storage.digest(source),))
        with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
            wrappers.index(net,{'id':1,'arch':'mips','big_endian':True})
    finally:store.conn.close()


def test_index_never_decodes_rejected_shapes_or_over_cap_donors(tmp_path):
    from decompnet import payload_storage
    from decompnet.store import Store
    store=Store(tmp_path/'shortlist.db');donor=fn(0x123400)
    bad='unused bad source '*100
    try:
        with store.tx() as db:
            db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}'),(2,'other','','{}')")
            for i in range(1,301):
                valid=257<=i<=260
                db.execute("INSERT INTO functions(id,project_id,name,status,size,target,relocs,matched_source) VALUES(?,1,?,'matched',44,?,?,?)",
                           (i,f'f{i}',donor['target'] if valid else b'bad',donor['relocs'],f'int f{i}(void){{return 7;}}' if 257<=i<=259 else bad))
            db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=?",(payload_storage.digest(bad),))
        groups=wrappers.index(SimpleNamespace(project_store=lambda _:store),{'id':1,'arch':'mips','big_endian':True})
        assert [r['id'] for r in next(iter(groups.values()))]==[257,258,259]
    finally:store.conn.close()


@pytest.mark.parametrize('change',["status='open'","project_id=2","target=X'00'","matched_source=NULL","size=999"])
def test_index_rechecks_current_donor_snapshot_after_shortlisting(tmp_path,monkeypatch,change):
    from decompnet import function_rows
    from decompnet.store import Store
    store=Store(tmp_path/'race.db');donor=fn(0x123400)
    try:
        with store.tx() as db:
            db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}'),(2,'other','','{}')")
            db.execute("INSERT INTO functions(id,project_id,name,status,size,target,relocs,matched_source) VALUES(1,1,'f','matched',44,?,?,'int f(void){return 7;}')",(donor['target'],donor['relocs']))
        selected=function_rows.selected
        def changed(*args,**kwargs):
            with store.tx() as db:db.execute('UPDATE functions SET '+change+' WHERE id=1')
            return selected(*args,**kwargs)
        monkeypatch.setattr(function_rows,'selected',changed)
        assert wrappers.index(SimpleNamespace(project_store=lambda _:store),{'id':1,'arch':'mips','big_endian':True})=={}
    finally:store.conn.close()

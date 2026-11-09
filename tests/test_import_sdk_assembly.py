"""Use a homemade body injected into the fingerprint pack; never commit SDK bytes."""
import hashlib
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_assembly import discover_known
from decompnet.app.sdk_names import name_functions
from decompnet.app.source_assembly import detail, verified
from decompnet.consoles.n64 import N64
from decompnet.consoles.n64 import sdk_assembly as pack
from decompnet.rom import Picked, split

BODY=bytes.fromhex('03e0000800000000')  # homemade return stub, not a real pack signature


@pytest.fixture
def toy_pack(monkeypatch):
    digest=hashlib.sha256(BODY).hexdigest()
    monkeypatch.setattr(pack,'KNOWN',{digest:('sdkToy',8,'test fixture')})
    monkeypatch.setattr(pack,'SIZES',(8,))
    monkeypatch.setattr(N64,'sdk_assembly_sizes',(8,))


def import_toy(tmp_path,monkeypatch,duplicate=False):
    net=RomNetwork(tmp_path/'db',tmp_path)
    def fake_split(workdir,*args,**kwargs):
        functions=[]
        for index in range(2 if duplicate else 1):
            addr=0x80001000+index*8
            lines=[split.AsmLine(index*8+i,addr+i,int.from_bytes(BODY[i:i+4],'big'),'nop','') for i in (0,4)]
            functions.append(split.RomFunction('func_'+f'{addr:08X}',addr,index*8,'code',lines,workdir/'toy.s'))
        return split.SplitResult(functions,[],[split.Segment('code',0,16,0x80001000)],{},0,'','','')
    monkeypatch.setattr(N64,'split_game',fake_split)
    monkeypatch.setattr(N64,'debug_info',lambda *a:None)
    picked=Picked('n64',BODY*2,'toy.z64',{'title':'Toy','size':16,'game_code':'TEST','region':'US','compiler_guess':'KMC','sha1':'toy'},True)
    slug=net.import_split(N64,picked,toolchain='gcc-2.7.2-kmc',flags=['-O2'],log=lambda s:None)
    return net,slug


def test_fresh_import_classifies_and_names_before_agents_without_sdk_files(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch)
    fn=net._function('sdkToy',slug);proj=net.project_row(slug)
    assert fn['status']=='asm' and fn['matched_source'] is None
    assert detail(proj,fn)['verified']
    assert net.symbols(proj['id']).resolve('sdkToy')==0x80001000
    assert net.function_detail(slug,'sdkToy',include_hints=False)['sdk']['names']==['sdkToy']
    assert net.claim_row(net.local_agent('worker')[0],slug) is None
    from decompnet.app.source_assembly import breakdown
    assert breakdown(net,proj)['verified_sdk_assembly']==1
    assert breakdown(net,proj)['exact_c']==0


def test_duplicate_entries_keep_unique_symbols_and_sdk_display_names(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch,True)
    rows=net.project_store(net.project_row(slug)['id']).all('SELECT name,status FROM functions')
    assert len({r['name'] for r in rows})==2
    assert all(r['status']=='asm' and r['name'].startswith('func_') for r in rows)
    assert all(net.function_detail(slug,r['name'],include_hints=False)['display_name']=='sdkToy' for r in rows)


def test_backfill_verifies_without_signatures_and_preserves_claims_and_operator_choice(tmp_path,monkeypatch,toy_pack):
    net,slug=import_toy(tmp_path,monkeypatch)
    proj=net.project_row(slug);store=net.project_store(proj['id'])
    with store.tx() as db:db.execute("UPDATE functions SET status='open',meta='{}'")
    fn=net._function('sdkToy',slug);agent,_=net.local_agent('other')
    assert net.permuter._hold(agent,fn,count_turn=False,seconds=60)
    assert discover_known(net,proj)==0
    net.permuter._let_go(agent,fn)
    with store.tx() as db:db.execute('UPDATE functions SET meta=?',(json.dumps({'reopened':True}),))
    assert discover_known(net,proj)==0
    with store.tx() as db:db.execute("UPDATE functions SET meta='{}'")
    name_functions(net,slug)
    assert net._function('sdkToy',slug)['status']=='asm'
    fn=net._function('sdkToy',slug)
    changed=dict(fn);changed['target']=BODY[:-1]+b'\1'
    assert not verified(proj,changed)
    with store.tx() as db:db.execute("UPDATE functions SET status='matched'")
    assert discover_known(net,proj)==0


def test_full_bytes_endianness_size_and_no_relocation_requirement(tmp_path,monkeypatch,toy_pack):
    assert pack.identify(BODY)['name']=='sdkToy'
    assert pack.identify(BODY,False) is None
    assert pack.identify(BODY+b'\0') is None
    assert pack.identify(BODY[:-1]+b'\1') is None
    from decompnet.app.sdk_assembly import known_proof
    proj={'config':json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc'}})}
    assert known_proof(N64,proj,{'target':BODY,'relocs':'[[0,4,123]]'}) is None



def test_builtin_pack_metadata_has_complete_body_sizes():
    assert N64.sdk_assembly_sizes==pack.SIZES
    assert {entry[1] for entry in pack.KNOWN.values()}==set(pack.SIZES)
    assert all(len(digest)==64 and int(digest,16)>=0 and row[1]%4==0 for digest,row in pack.KNOWN.items())

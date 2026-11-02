"""Other-project byte identity is independent of saved C or function status."""
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.project_functions import listing


@pytest.fixture
def net(tmp_path):
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        for pid in (1,2,3):
            db.execute('INSERT INTO projects(id,slug,name,arch,root,config) VALUES(?,?,?,?,?,?)',
                       (pid,str(pid),'Project '+str(pid),'mips',str(tmp_path),json.dumps({'platform':'n64'})))
    with net.store.tx() as db:
        db.execute("UPDATE projects SET big_endian=1")
    return net


def add(net, pid, name, code, status='open'):
    with net.store.tx() as db:
        return db.execute('INSERT INTO functions(project_id,name,target,size,n_insns,status) VALUES(?,?,?,?,?,?)',
                          (pid,name,code,len(code),len(code)//4,status)).lastrowid


@pytest.mark.parametrize('status', ['open','matched'])
def test_exact_even_tiny_and_unmatched(net,status):
    add(net,1,'local',b'\x03\xe0\x00\x08',status)
    add(net,1,'same_project',b'\x03\xe0\x00\x08')
    other = add(net,2,'namedFunction',b'\x03\xe0\x00\x08')
    add(net,3,'different',b'\x03\xe0\x00\x09')
    result=listing(net,'1','local')
    assert result['exact'] and [r['id'] for r in result['rows']] == [other]
    assert result['rows'][0]['status']=='open' and result['rows'][0]['score']==1
    with net.store.tx() as db:
        db.execute('UPDATE functions SET gone_mark=1 WHERE id=?',(other,))
    assert not listing(net,'1','local')['exact']


def test_similar_fallback_and_exact_precedence(net):
    code = bytes.fromhex('24020001240300022404000303e0000800000000')
    different = bytes.fromhex('24020009240300082404000703e0000800000000')
    add(net,1,'local',code)
    peer = add(net,2,'similarName',different)
    result=listing(net,'1','local')
    assert not result['exact'] and result['rows'][0]['id']==peer
    assert result['rows'][0]['score']==1 and not result['rows'][0]['identical']
    exact=add(net,3,'identicalName',code)
    assert [r['id'] for r in listing(net,'1','local')['rows']] == [exact]
    with net.store.tx() as db:
        db.execute("UPDATE projects SET arch='arm' WHERE id=3")
    assert not listing(net,'1','local')['exact']


def test_no_bytes_or_no_other_projects(net):
    add(net,1,'empty',b'')
    add(net,1,'only',b'\x00'*20)
    assert listing(net,'1','empty')['rows']==[]
    assert listing(net,'1','only')['rows']==[]


@pytest.mark.parametrize('arch', ['mips','arm','ppc','ppc64','x86','x86_64'])
def test_exact_identity_is_processor_independent(net,arch):
    with net.store.tx() as db:
        db.execute('UPDATE projects SET arch=?',(arch,))
    add(net,1,'local',b'bytes')
    other=add(net,2,'named',b'bytes')
    assert listing(net,'1','local')['rows'][0]['id']==other


@pytest.mark.parametrize('status', ['open','matched'])
def test_routes_validate_peer_and_allow_explicit_similar_name(net,tmp_path,monkeypatch,status):
    from starlette.testclient import TestClient

    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW

    code=bytes.fromhex('24020001240300022404000303e0000800000000')
    other=bytes.fromhex('24020009240300082404000703e0000800000000')
    add(net,1,'local',code,status)
    peer=add(net,2,'named',other)
    home=tmp_path/'.decomp';home.mkdir()
    mcp,_,security,_=build(net,home,PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    url='/api/cross-game/functions?slug=1&function=local'
    assert client.get(url,headers={'User-Agent':'outside'}).status_code==401
    assert client.get(url).json()['rows'][0]['id']==peer
    renamed=[]
    monkeypatch.setattr(net,'rename_symbol',lambda *args: renamed.append(args) or 'Renamed')
    body={'slug':'1','function':'local','name':'named','peer_id':peer}
    assert client.post('/api/cross-game/name',json=body).status_code==403
    assert client.post('/api/cross-game/name',json=body,headers={'X-Decomp':'1'}).status_code==200
    assert renamed[0][1:4]==('1','local','named')
    body['name']='staleName'
    assert client.post('/api/cross-game/name',json=body,headers={'X-Decomp':'1'}).status_code==400
    assert len(renamed)==1


def test_project_peer_table_gui():
    import shutil
    import subprocess

    if not (node := shutil.which('node')):
        pytest.skip('needs node')
    subprocess.run([node,'tests/gui_project_peers.js'],check=True,capture_output=True,text=True,timeout=20)


@pytest.mark.parametrize('arch,endian,one,two', [
    ('arm',0,'0100a0e3','0200a0e3'),
    ('ppc',1,'38600001','38600002'),
    ('ppc64',1,'38600001','38600002'),
    ('x86',0,'b801000000','b802000000'),
    ('x86_64',0,'b801000000','b802000000'),
    ('thumb',0,'0120','0220'),
])
def test_similar_readers_across_processors(net,arch,endian,one,two):
    with net.store.tx() as db:
        db.execute('UPDATE projects SET arch=?,big_endian=?',(arch,endian))
    add(net,1,'local',bytes.fromhex(one)*5)
    other=add(net,2,'named',bytes.fromhex(two)*5)
    result=listing(net,'1','local')
    assert not result['exact'] and result['rows'][0]['id']==other

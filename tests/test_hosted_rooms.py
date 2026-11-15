import json

from decompnet.app import collab, hosted_rooms
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_tunnel_room_restores_invite_and_member_tokens(tmp_path):
    net,_=_game(tmp_path,[('f',4,'open')])
    original=collab.Room(net,'game');code=original.open_invite(mode='tunnel')
    member,_=net.local_agent('guest')
    token=net.store.one('SELECT token_hash FROM agents WHERE id=?',member.id)[0]
    calls=[]
    def reopen(slug,mode,address,port,log,keep):
        room=collab.Room(net,slug)
        assert room.open_invite(keep=keep,mode=mode)==code
        assert room.invite_ok(code)
        calls.append((slug,mode,keep))
    assert hosted_rooms.restore(net,reopen,lambda _:None)=={}
    assert calls==[('game','tunnel',True)]
    assert net.store.one('SELECT token_hash FROM agents WHERE id=?',member.id)[0]==token


def test_address_room_keeps_endpoint_and_closed_rooms_stay_closed(tmp_path):
    net,_=_game(tmp_path,[('f',4,'open')]);room=collab.Room(net,'game')
    room.open_invite(mode='address',address='http://100.64.0.1:6481',port=6481)
    calls=[]
    hosted_rooms.restore(net,lambda *args,**kwargs:calls.append((args,kwargs)),lambda _:None)
    assert calls[0][0]==('game','address','http://100.64.0.1:6481',6481)
    assert calls[0][1]['keep']
    room.close();calls.clear()
    assert hosted_rooms.restore(net,lambda *args,**kwargs:calls.append(args),lambda _:None)=={}
    assert not calls


def test_restore_failure_is_visible_and_does_not_forget_room(tmp_path):
    net,_=_game(tmp_path,[('f',4,'open')]);room=collab.Room(net,'game')
    code=room.open_invite(mode='tunnel')
    def fail(*args,**kwargs):raise NetworkError('Internet unavailable')
    result=hosted_rooms.restore(net,fail,lambda _:None)
    assert result=={'game':'Internet unavailable'}
    cfg=json.loads(net.project_row('game')['config'])['collab']
    assert cfg['open'] and cfg['code']==code


def test_restore_reads_closed_projects_without_sync_but_validates_open_rooms(tmp_path, monkeypatch):
    from decompnet.app.engine import RomNetwork

    home = tmp_path / 'home'
    (home / '.decomp').mkdir(parents=True)
    net = RomNetwork(home / '.decomp' / 'decomp.db', home)
    with net.store.tx() as db:
        for slug, opened in [('a-closed', False), ('b-broken', True), ('c-good', True)]:
            (tmp_path / slug).mkdir()
            cfg = {'kind': 'rom', 'platform': 'n64', 'collab': {'open': opened, 'mode': 'tunnel'}}
            db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                       (slug, slug, 'mips', 1, str(tmp_path / slug), json.dumps(cfg)))
    checks, attempts = [], []

    def sync(slug, **kwargs):
        checks.append(slug)
        if slug != 'c-good':raise NetworkError('Synthetic incomplete save layout')
        return False

    monkeypatch.setattr(net.progress, 'sync', sync)

    def reopen(slug, *args, **kwargs):
        attempts.append(slug)
        # Actual room opening must retain ordinary project/input validation.
        net.project_row(slug)

    try:
        result = hosted_rooms.restore(net, reopen, lambda _: None)
        assert result == {'b-broken': 'Synthetic incomplete save layout'}
        assert attempts == ['b-broken', 'c-good']
        assert checks == ['b-broken', 'c-good']
        assert json.loads(net.store.one('SELECT config FROM projects WHERE slug=?', 'b-broken')['config'])['collab']['open']
    finally:
        net.store.close_readers()
        net.store.conn.close()


def test_one_project_lookup_failure_does_not_abort_other_room_restores(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 4, 'open')])
    room = collab.Room(net, 'game')
    code = room.open_invite(mode='tunnel')
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,root,config) VALUES(?,?,?)', ('a-broken', str(tmp_path), '{}'))
    original = net.project_row

    def lookup(slug):
        if slug == 'a-broken':raise NetworkError('Synthetic project lookup failure')
        return original(slug)

    monkeypatch.setattr(net, 'project_row', lookup)
    reopened, logs = [], []
    result = hosted_rooms.restore(net, lambda slug, *args, **kwargs: reopened.append(slug), logs.append)
    assert result == {'a-broken': 'Synthetic project lookup failure'}
    assert reopened == ['game']
    assert any('a-broken' in message and 'lookup failure' in message for message in logs)
    assert json.loads(original('game')['config'])['collab']['code'] == code

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

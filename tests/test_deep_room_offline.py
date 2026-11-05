"""Transport loss does not turn local ROM work into a broken-source review."""
import json
import time
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_room, room_matches
from decompnet.app.collab import Refused
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log, setup
from tests.test_permute import AGENT


def test_claim_transport_failure_and_lost_renewal_keep_local_lease(monkeypatch):
    net=setup(monkeypatch)
    def lost(*args):raise NetworkError("the room's host isn't reachable right now: can't reach the host (ReadTimeout)")
    link=SimpleNamespace(net=net,slug='game',error='',claim=lost)
    monkeypatch.setattr(net,'links',{'game':link})
    fn=net.function();search=deep_permuter.Search(net,1,10,Log(),fn)
    assert search._hold(AGENT,fn)
    assert deep_room.offline(link) and search.room_offline
    assert net.store.one('SELECT agent_id FROM claims')['agent_id']==AGENT.id
    search.room_holds[fn['id']]=(link,AGENT,fn['name'],time.monotonic()-31)
    search.check()
    assert not search.room_holds
    search._let_go(AGENT,fn)
    assert not net.store.one('SELECT 1 FROM claims')


def test_host_refusal_does_not_become_offline_work(monkeypatch):
    net=setup(monkeypatch)
    def refused(*args):raise Refused('access denied')
    link=SimpleNamespace(net=net,slug='game',error='',claim=refused)
    monkeypatch.setattr(net,'links',{'game':link})
    search=deep_permuter.Search(net,1,10,Log(),net.function())
    with pytest.raises(Refused):search._hold(AGENT,net.function())
    assert not net.store.one('SELECT 1 FROM claims')


def test_best_attempt_outbox_survives_failure_and_flushes_after_reconnect(monkeypatch):
    net=setup(monkeypatch);fn=net.function()
    monkeypatch.setattr(room_matches,'packet',lambda *args:{'compiler':{'toolchain':'tested','flags':['-O2']},'header':'private'})
    link=SimpleNamespace(net=net,slug='game',url='http://host',room='room',error="can't reach the host")
    search=deep_permuter.Search(net,1,10,Log(),fn)
    deep_room.best(search,link,AGENT,fn,'improved C',20)
    stored=json.loads(net.store.one("SELECT value FROM settings WHERE key LIKE 'deep-room-best:%'")['value'])
    assert stored['source']=='improved C' and stored['evidence']['compiler']['toolchain']=='tested'
    sent=[]
    link._req=lambda *args:sent.append(args) or {'ok':False}
    deep_room.pump(link)
    assert net.store.one("SELECT 1 FROM settings WHERE key LIKE 'deep-room-best:%'")
    link._req=lambda *args:sent.append(args) or {'ok':True}
    deep_room.pump(link)
    assert sent[-1][2]['source']=='improved C'
    assert not net.store.one("SELECT 1 FROM settings WHERE key LIKE 'deep-room-best:%'")

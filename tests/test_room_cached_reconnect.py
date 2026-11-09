"""A restart retains applied room work without pretending that reconnect has completed."""
import json

import httpx
import pytest

from decompnet.app import collab, room_matches
from decompnet.app.engine import RomNetwork


@pytest.mark.parametrize('cursor', [{}, {'e':12,'a':3,'v':1}])
def test_saved_snapshot_is_available_before_reconnect(tmp_path, monkeypatch, cursor):
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    info={'cursor':cursor}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   ('cached','Cached',str(tmp_path),json.dumps({'link':info})))
    client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={})))
    try:
        link=collab.Link(net,'cached','https://host.example','room','secret','guest',client)
        assert link.status()['cached']==bool(cursor)
        assert link.status()['state']=='syncing'
        assert not link.synced  # saved data does not grant current presence/claims
        monkeypatch.setattr(link,'apply',lambda state:None)
        monkeypatch.setattr(link,'_req',lambda *args:{'behind':5,'events':[]})
        monkeypatch.setattr(room_matches,'recover_client',lambda *a:None)
        monkeypatch.setattr(room_matches,'pump',lambda *a:None)
        link.sync()
        assert link.status()['state']=='syncing' and link.status()['cached']
        # Even an empty event stream needs a durable marker; zero cursors are legitimate.
        if not cursor:
            stored=json.loads(net.project_row('cached')['config'])['link']
            assert stored['cached'] and stored['cursor']=={'e':0,'a':0,'v':0}
        restarted=collab.Link(net,'cached','https://host.example','room','secret','guest',client)
        assert restarted.status()['cached'] and not restarted.synced
        restarted.refused=True;restarted.error=collab.WAITING
        assert restarted.status()['state']=='waiting'
        restarted.error='Membership revoked'
        assert restarted.status()['state']=='out'
    finally:client.close()


def test_failed_snapshot_does_not_mark_project_cached(tmp_path,monkeypatch):
    from decompnet.network import NetworkError
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                   ('first','First',str(tmp_path),'{"link":{}}'))
    with httpx.Client() as client:
        link=collab.Link(net,'first','https://host.example','room','secret','guest',client)
        monkeypatch.setattr(link,'_req',lambda *a:{})
        monkeypatch.setattr(room_matches,'recover_client',lambda *a:None)
        def fail(state):raise NetworkError('Interrupted apply')
        monkeypatch.setattr(link,'apply',fail)
        with pytest.raises(NetworkError):link.sync()
        assert not link.status()['cached']
        assert 'cached' not in json.loads(net.project_row('first')['config'])['link']

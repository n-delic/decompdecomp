"""Reconciliation inventories are hints; host byte verification is the authority."""
import json
import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app import collab, room_matches
from decompnet.app import room_reconcile as reconcile
from decompnet.app.engine import RomNetwork
from decompnet.match_bundle import digest
from decompnet.network import NetworkError
from tests.test_app_engine import DAMAGE
from tests.test_rom import built  # noqa: F401


@pytest.fixture
def pair(built,tmp_path):  # noqa: F811
    host=RomNetwork(tmp_path/'host.db',tmp_path/'host')
    slug=host.import_rom(built,confirmed=True,log=lambda _:None)
    guest=RomNetwork(tmp_path/'guest.db',tmp_path/'guest')
    gslug=guest.import_rom(built,confirmed=True,log=lambda _:None)
    room=collab.Room(host,slug)
    host.rooms[slug]=room
    room.open_invite()
    token,name=room.join('client',guest.cfg(guest.project_row(gslug))['rom']['sha1'])
    member=host.authenticate(token)
    client=TestClient(collab.host_app(host,host.rooms))
    link=collab.Link(guest,gslug,'http://testserver',slug,token,name,client)
    return host,slug,guest,gslug,room,member,link


def inventory(net,slug,name='func_80000808'):
    fn=net._function(name,slug)
    return {k:fn[k] for k in ('name','vram','rom_offset','size')} | {'target_sha256':digest(fn['target'])}


def begin(room,member):
    reconcile.command(room,member,True)
    return reconcile.start(room)['id']


def test_client_inventory_resends_exact_bundle_despite_host_claim(pair,monkeypatch):
    host,slug,guest,gslug,room,member,link=pair
    proj=guest.project_row(gslug)
    with guest.project_store(gslug).tx() as db:
        db.execute("UPDATE context_versions SET header=header || '\n#define CLIENT_TIMER 60\n' WHERE project_id=?",(proj['id'],))
    source=DAMAGE.replace('= 60;','= CLIENT_TIMER;')
    agent,_=guest.local_agent('author')
    assert guest.attempt(agent,'func_80000808',source,gslug)['matched']
    host._update_cfg(slug,lambda cfg:cfg['compiler'].update(flags=['-O0']))
    owner,_=host.local_agent('busy-agent')
    host.claim(owner,slug,'func_80000808')
    # The host must use the incoming private context, not its O0 default/header.
    compile_source=host._compile
    def concurrent_compile(*args,**kwargs):
        result=compile_source(*args,**kwargs)
        host._update_cfg(slug,lambda cfg:cfg.update(spend={'tokens':99},progress_revision='background'))
        return result
    monkeypatch.setattr(host,'_compile',concurrent_compile)
    run=begin(room,member)
    reconcile.client_run(link,run)
    state=reconcile.snapshot(room)
    assert state['members'][member.name]['status']=='done'
    rows=list(state['results'].values())
    assert len(rows)==1 and rows[0]['status']=='applied',rows
    fn=host._function('func_80000808',slug)
    assert fn['status']=='matched'
    assert 'CLIENT_TIMER' in host._context(fn['project_id'],fn['unit'])['header']
    assert not host.project_store(slug).one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])
    assert host.cfg(host.project_row(slug))['compiler']['flags']==['-O0']
    # A repeated run only inventories the already-shared match; it never recompiles it.
    reconcile.close(room)
    run=begin(room,member)
    reconcile.client_run(link,run)
    state=reconcile.snapshot(room)
    assert state['members'][member.name]['shared']==1 and not state['results']


def test_inventory_does_not_trust_names_or_cross_member_bundles(pair):
    host,slug,guest,gslug,room,member,link=pair
    run=begin(room,member)
    item=inventory(guest,gslug)
    item['name']='different_client_name'
    reply=reconcile.exchange(room,member,{'id':run,'action':'inventory','items':[item],'page':0,'total':1})
    assert reply['requested'][0]['function']=='func_80000808'
    # Retrying the inventory page must not double-count it.
    reconcile.exchange(room,member,{'id':run,'action':'inventory','items':[item],'page':0,'total':1})
    assert reconcile.snapshot(room)['members'][member.name]['checked']==1
    token,_=room.join('other',guest.cfg(guest.project_row(gslug))['rom']['sha1'])
    other=host.authenticate(token)
    with pytest.raises(NetworkError,match='ended'):
        reconcile.exchange(room,other,{'id':run,'action':'apply','key':reply['requested'][0]['key']})
    item['target_sha256']='0'*64
    reply=reconcile.exchange(room,member,{'id':run,'action':'inventory','items':[item],'page':1,'total':2})
    assert not reply['requested']
    assert any(r['status']=='not_applied' for r in reconcile.snapshot(room)['results'].values())
    assert host._function('func_80000808',slug)['status']=='open'


def test_failed_byte_verification_remains_visible_and_cannot_claim_success(pair):
    host,slug,guest,gslug,room,member,link=pair
    run=begin(room,member)
    reply=reconcile.exchange(room,member,{'id':run,'action':'inventory','items':[inventory(guest,gslug)],'page':0,'total':1})
    source=DAMAGE.replace('= 60;','= 59;')
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    result=reconcile.exchange(room,member,{'id':run,'action':'apply','key':reply['requested'][0]['key'],
                                         'source':source,'evidence':evidence,'matched':True})
    assert not result['matched']
    assert next(iter(reconcile.snapshot(room)['results'].values()))['status']=='not_applied'
    assert host._function('func_80000808',slug)['status']=='open'
    reconcile.close(room)
    with pytest.raises(NetworkError,match='ended'):
        reconcile.exchange(room,member,{'id':run,'action':'finish'})


def test_status_survives_room_recreation_and_legacy_clients_are_explicit(pair):
    host,slug,guest,gslug,room,member,link=pair
    state=reconcile.start(room)
    assert state['members'][member.name]['status']=='update_required'
    reopened=collab.Room(host,slug)
    assert reconcile.snapshot(reopened)==state
    reconcile.close(reopened)
    run=begin(reopened,member)
    assert reconcile.command(reopened,member)['id']==run
    reconcile.close(reopened)
    assert reconcile.command(reopened,member) is None


def test_host_clears_only_pending_entries_already_matched(pair):
    host,slug,guest,gslug,room,member,link=pair
    agent,_=host.local_agent('author')
    key,_=room_matches.save(host,slug,'host',agent,'func_80000808',DAMAGE,None)
    reconcile.settle_pending(room)
    assert room_matches.pending(host,slug)
    assert host.attempt(agent,'func_80000808',DAMAGE,slug)['matched']
    reconcile.settle_pending(room)
    assert not room_matches.pending(host,slug)
    item=json.loads(room.store.one('SELECT value FROM settings WHERE key=?',key)['value'])
    assert item['source']=='' and item['evidence'] is None


def test_heartbeat_dispatch_is_deduplicated_and_does_not_hold_sync_lock(monkeypatch):
    entered=threading.Event();release=threading.Event();calls=[]
    def client_run(link,run):
        calls.append(run);entered.set();release.wait(5)
    monkeypatch.setattr(reconcile,'client_run',client_run)
    link=SimpleNamespace(fork=False,slug='game')
    reconcile.launch(link,{'id':'first'})
    assert entered.wait(2)
    reconcile.launch(link,{'id':'first'})
    reconcile.launch(link,{'id':'second'})
    assert calls==['first']
    release.set();link._reconcile_worker.join(2)
    reconcile.launch(link,{'id':'first'})
    assert calls==['first']
    reconcile.launch(link,{'id':'second'});link._reconcile_worker.join(2)
    assert calls==['first','second']


def test_cancel_keeps_report_and_stops_future_client_submissions(pair):
    host,slug,guest,gslug,room,member,link=pair
    run=begin(room,member)
    class Log:
        cancel_event=SimpleNamespace(wait=lambda _:None)
        checks=0
        def check_cancel(self):
            self.checks+=1
            if self.checks>1:raise RuntimeError('Stopped')
        def progress(self,value):self.value=value
    log=Log()
    with pytest.raises(RuntimeError,match='Stopped'):
        reconcile.run(room,log)
    assert log.value['members'][member.name]['status']=='waiting'
    assert not reconcile.snapshot(room)['active']
    assert reconcile.snapshot(room)['members'][member.name]['status']=='interrupted'
    with pytest.raises(NetworkError,match='ended'):
        reconcile.exchange(room,member,{'id':run,'action':'finish'})


def test_inventory_endpoint_requires_membership_and_does_not_accept_unrequested_source(pair):
    host,slug,guest,gslug,room,member,link=pair
    run=begin(room,member)
    client=link.client
    assert client.post('/collab/reconcile?room='+slug,json={'id':run,'action':'finish'}).status_code==400
    result=link._req('GET','/collab/whoami',{'reconcile':1})
    assert result['reconcile']['id']==run
    with pytest.raises(NetworkError,match='not requested'):
        link._req('POST','/collab/reconcile',{'id':run,'action':'apply','key':'forged','matched':True})

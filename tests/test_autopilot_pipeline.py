"""Critical-path reductions retain durable completions and synchronization guards."""
import pytest

from decompnet.app import draft_reports, restore_effects, trial_bookkeeping
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


def test_receipts_batch_advisory_updates_but_completed_receipt_flushes(tmp_path):
    net=RomNetwork(tmp_path/'db',tmp_path);tiny_game(net)
    fn=net.store.one('SELECT id,project_id FROM functions LIMIT 1')
    with trial_bookkeeping.scope():
        draft_reports.save(net,fn['id'],{'failures':['diagnostic']},project_id=fn['project_id'])
        assert not net.store.one('SELECT 1 FROM settings WHERE key=?',draft_reports.PREFIX+str(fn['id']))
        receipt=draft_reports.read(net,fn['id'],project_id=fn['project_id'])
        assert receipt['failures']==['diagnostic']
        receipt['failures'].append('not saved')
        assert draft_reports.read(net,fn['id'],project_id=fn['project_id'])['failures']==['diagnostic']
        draft_reports.save(net,fn['id'],{'complete':True},project_id=fn['project_id'])
        assert net.store.one('SELECT 1 FROM settings WHERE key=?',draft_reports.PREFIX+str(fn['id']))
    assert draft_reports.read(net,fn['id'],project_id=fn['project_id'])['complete']


def test_streaming_followups_are_queued_and_retained_on_stop(monkeypatch):
    net=object();queued=[]
    monkeypatch.setattr(restore_effects,'_queue',lambda net,pid,pending:queued.append((pid,pending)))
    with pytest.raises(RuntimeError):
        with restore_effects.streaming(net):
            assert restore_effects.defer(net,{'id':1,'slug':'game'},2,matched=True)
            assert queued and queued[0][1]['incremental']
            assert restore_effects.defer(net,{'id':1,'slug':'game'},3,matched=False)
            raise RuntimeError('stopped')
    assert any(3 in pending['improved'] for _,pending in queued)


def test_scoped_autopilot_trial_keeps_revision_guard_without_file_sync(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path);tiny_game(net)
    proj=net.project_row('game');fn=net.store.one('SELECT id FROM functions LIMIT 1')
    agent,_=net.local_agent('autopilot')
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Trial must not synchronize the entire project'))
    from decompnet.app.engine.autopilot import AutopilotMixin
    def work(*args):
        net.progress.check_result()
        return 'skip'
    monkeypatch.setattr(AutopilotMixin,'_autopilot_one_work',work)
    assert net._autopilot_one(agent,proj,fn['id'])=='skip'

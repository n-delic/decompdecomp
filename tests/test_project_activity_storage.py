"""Notification delivery must not skip an event when project ID ranges differ."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import activity_index
from decompnet.app.engine.gui import GuiMixin
from decompnet.network import NetworkError
from tests import test_project_storage as base

scoped = base.scoped


def event(store, pid, eid, detail, kind='match', actor=1):
    with store.tx() as db:
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(eid,pid,actor,kind,detail,eid))


def bind(net):
    net.events_after = GuiMixin.events_after.__get__(net)
    net.recent_events = GuiMixin.recent_events.__get__(net)


def test_global_cursor_preserves_legacy_and_delivers_later_smaller_source_ids(scoped):
    net, stores = scoped
    bind(net)
    event(stores[1],1,10,'older')
    event(stores[2],2,100,'initial')
    activity_index.bootstrap(net)
    assert net.events_after(None) == {'last':100,'events':[]}
    event(stores[2],2,101,'second project')
    first = net.events_after(100)
    assert len(first['events']) == 1 and first['events'][0]['detail'] == 'second project'
    event(stores[1],1,11,'late first project')
    second = net.events_after(first['last'])
    assert [row['detail'] for row in second['events']] == ['late first project']
    assert second['last'] > first['last']
    assert net.events_after(second['last'])['events'] == []
    assert net.recent_events(2)[0]['detail'] == 'late first project'


def test_repeated_and_concurrent_delivery_is_idempotent_and_does_not_copy_code(scoped):
    net, stores = scoped
    bind(net)
    activity_index.bootstrap(net)
    for pid in (1,2):event(stores[pid],pid,pid*10,'project '+str(pid))
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:activity_index.sync(net),range(4)))
    rows = net.events_after(0)['events']
    assert {r['slug'] for r in rows} == {'first','second'} and len(rows) == 2
    assert net.store.one('SELECT COUNT(*) n FROM activity_events')['n'] == 2
    assert not net.store.one('SELECT 1 FROM functions')
    assert not net.store.one('SELECT 1 FROM attempts')
    with stores[1].tx(),pytest.raises(NetworkError,match='outside a project transaction'):
        activity_index.sync(net)


def test_initial_open_never_relabels_a_backlog_as_new_and_routine_ids_are_kept(scoped):
    net, stores = scoped
    bind(net)
    event(stores[1],1,900,'routine','compile')
    activity_index.bootstrap(net)
    assert net.events_after(None)['last'] == 900
    for i in range(activity_index.BATCH+2):event(stores[1],1,901+i,'old '+str(i))
    mark = net.events_after(None)['last']
    assert net.events_after(mark)['events'] == []
    event(stores[2],2,1,'new since open')
    assert net.events_after(mark)['events'][0]['detail'] == 'new since open'


def test_missing_index_is_reported_without_partial_activation(scoped):
    net, _ = scoped
    bind(net)
    with pytest.raises(NetworkError,match='has not been migrated'):net.events_after(None)


def test_bootstrap_rolls_back_duplicate_legacy_ids_and_does_not_publish_ready(scoped):
    import sqlite3
    net, stores = scoped
    event(stores[1],1,1,'first')
    event(stores[2],2,1,'inconsistent duplicate global ID')
    with pytest.raises(sqlite3.IntegrityError):activity_index.bootstrap(net)
    assert not net.store.one('SELECT 1 FROM settings WHERE key=?',activity_index.READY)
    assert not net.store.one("SELECT 1 FROM sqlite_master WHERE name='activity_events'")

"""A removed catalog snapshot must not stop other autosave workers or hide damaged owners."""
import json
import threading

import pytest

from decompnet.app.saves import Autosave
from decompnet.network import Network, NetworkError
from tests.test_project_database_migration import close, legacy


@pytest.fixture(params=[False,True],ids=['shared-file','project-files'])
def data(tmp_path,request):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=request.param)
    for slug in ('first','second'):
        with net.project_store(slug).tx() as db:
            db.execute('UPDATE projects SET config=? WHERE slug=?',(json.dumps({'kind':'rom','rom':{'sha1':'synthetic'}}),slug))
    yield net
    close(net)


def test_removed_after_discovery_cancels_old_worker_and_starts_remaining_project(data,monkeypatch):
    net=data;auto=Autosave(net);cancel=threading.Event()
    auto.workers['first']={'cancel':cancel}
    resolver=net.project_store;removed=[];started=[]
    def resolve(project):
        if project=='first' and not removed:
            removed.append(True)
            net.remove_project('first')
            # Legacy resolver intentionally returns the shared Store even for a
            # removed slug; the catalog snapshot must still be discarded.
            if net._project_files is None:raise NetworkError('Project removed')
        return resolver(project)
    monkeypatch.setattr(net,'project_store',resolve)
    monkeypatch.setattr(auto,'_project_loop',lambda slug,worker:started.append(slug))
    auto._reconcile()
    auto.workers['second']['thread'].join(timeout=2)
    assert cancel.is_set() and set(auto.workers)=={'second'} and started==['second']
    assert [r['slug'] for r in auto._projects()]==['second']


def test_existing_catalog_identity_does_not_hide_owner_failures(data,monkeypatch):
    net=data;auto=Autosave(net)
    def broken(_):raise NetworkError('Cannot read project owner')
    monkeypatch.setattr(net,'project_store',broken)
    with pytest.raises(NetworkError,match='Cannot read project owner'):auto._projects()

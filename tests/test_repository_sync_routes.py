"""The repository build runs as a guarded, cancellable background job."""
import threading

from decompnet.app import repository_sync

from .test_repair_feedback_routes import repair_app as _repair_app

repository_app = _repair_app


def test_repository_sync_route_guards_and_duplicate_jobs(repository_app,monkeypatch):
    client,net,_=repository_app
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config)VALUES('game','Game','/unused','{}')")
    monkeypatch.setattr(repository_sync,'status',lambda *args:{'folder':'/repo','supported':True})
    assert client.get('/api/repository-sync?slug=game',headers={'User-Agent':'outside'}).status_code==401
    assert client.get('/api/repository-sync?slug=game').json()['folder']=='/repo'
    data={'slug':'game','folder':'/repo'}
    assert client.post('/api/repository-sync',json=data).status_code==403
    assert client.post('/api/repository-sync',json={'slug':'game'},headers={'X-Decomp':'1'}).status_code==400
    started=threading.Event();release=threading.Event()
    def run(*args):started.set();release.wait(5);return {'accepted':[],'skipped':[]}
    monkeypatch.setattr(repository_sync,'run',run)
    try:
        first=client.post('/api/repository-sync',json=data,headers={'X-Decomp':'1'})
        assert first.status_code==200 and 'job' in first.json() and started.wait(1)
        assert client.post('/api/repository-sync',json=data,headers={'X-Decomp':'1'}).status_code==400
    finally:release.set()


def test_repository_commit_requires_boolean(repository_app):
    client, _, _ = repository_app
    response = client.post('/api/repository-sync', json={'slug':'game', 'folder':'/repo', 'commit_push':'yes'},
                           headers={'X-Decomp':'1'})
    assert response.status_code == 400

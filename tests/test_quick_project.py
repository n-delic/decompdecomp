"""Opening metadata must not wait for diagnostics, full shared headers or room refreshes."""
import json
import sqlite3
import threading
from types import SimpleNamespace

from decompnet.app.engine import RomNetwork
from tests.test_app_server import app  # noqa: F401
from tests.test_loading import tiny_game


def test_quick_overview_does_not_build_diagnostics_or_fetch_header(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'summary.db',tmp_path);tiny_game(net)
    monkeypatch.setattr(net.twins,'cross_game_results',lambda *_:(_ for _ in ()).throw(AssertionError('reuse diagnostics')))
    monkeypatch.setattr(net.readability,'counts',lambda *_:(_ for _ in ()).throw(AssertionError('readability diagnostics')))
    refreshed=[]
    net.rooms['game']=SimpleNamespace(members=lambda *,refresh:refreshed.append(refresh) or [])
    def narrow(action,table,column,*_):
        if action==sqlite3.SQLITE_READ and table=='context_versions' and column=='header':return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    net.store._reader().set_authorizer(narrow)
    try:
        with net.progress.view():out=net.overview('game',quick=True)
    finally:net.store._reader().set_authorizer(None)
    assert out['functions']==4 and out['overview_pending']
    assert out['context_version']>=0 and refreshed==[False]


def test_quick_project_route_responds_while_writer_lock_is_busy(app,monkeypatch):  # noqa: F811
    client,net,*_=app
    tiny_game(net)
    monkeypatch.setattr(net,'warm_briefs_soon',lambda *_:None)
    finished=threading.Event();result=[]
    def request():
        result.append(client.get('/api/project?slug=game&quick=1'))
        finished.set()
    with net.store.lock:
        worker=threading.Thread(target=request);worker.start()
        assert finished.wait(2),'Opening summary waited for the writer/diagnostics lock'
    worker.join(2)
    assert result[0].status_code==200,result[0].text
    assert result[0].json()['overview_pending']


def test_ready_imported_project_quick_summary_skips_build_and_file_diagnostics(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'fork-summary.db',tmp_path)
    cfg={'kind':'fork','platform':'n64','ready':True,'version':'pal'}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES('imported','Imported',?,?)",(str(tmp_path),json.dumps(cfg)))
    with net.progress.view():proj=net.project_row('imported')
    monkeypatch.setattr(net.forks,'get',lambda slug:(proj,object(),cfg))
    monkeypatch.setattr(net.forks,'_preflight_once',lambda *_:(_ for _ in ()).throw(AssertionError('build preflight')))
    with net.progress.view():out=net.overview('imported',quick=True)
    assert out['kind']=='fork' and out['ready'] and out['overview_pending']
    assert out['versions']==['pal']

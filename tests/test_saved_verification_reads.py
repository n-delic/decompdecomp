"""Skipped saved verdicts must not inflate every function payload."""
import pytest

from decompnet import payload_storage
from decompnet.app import saves
from decompnet.app.engine import RomNetwork

from .test_project_database_migration import close


@pytest.fixture(params=[False, True],ids=['shared','project-file'])
def saved(tmp_path,request):
    path=tmp_path/'app.db'
    net=RomNetwork(path,tmp_path)
    source='int unchanged(void){return 1;}\n'*1000
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('game',?,'{}')",(str(tmp_path),)).lastrowid
        for name,status in [('matched','matched'),('best','open')]:
            db.execute('INSERT INTO functions(project_id,name,vram,status,asm,seed,best_source,matched_source,best_score) '
                       'VALUES(?,?,1,?,?,?,?,?,0)',(pid,name,status,'assembly '*1000,'seed '*1000,source,source if status=='matched' else None))
    if request.param:
        close(net);net=RomNetwork(path,tmp_path,project_databases=True)
    store=net.project_store(pid)
    rows={r['name']:dict(r) for r in store.all('SELECT name,vram FROM functions')}
    saved={'matched':{'vram':1,'matched_by':'author'},'best':{'vram':1,'best_by':'author'}}
    files={'src/matched.c':source,'best/best.c':source}
    yield net,store,rows,saved,files,source
    close(net)


def test_already_matched_and_unchanged_draft_load_only_comparison_source(saved,monkeypatch,tmp_path):
    net,store,rows,entries,files,source=saved
    decoded=[]
    def unpack(digest,blob):
        text=payload_storage.unpack(digest,blob);decoded.append(text);return text
    store._reader().create_function('payload_read',2,unpack)
    monkeypatch.setattr(net,'_compile',lambda *_:pytest.fail('unchanged saved work compiled'))
    result=saves._judge_saved(net,'game',tmp_path,{},rows,entries,lambda _:None,files=files)
    assert result=={'matched':1,'best':1,'lost':[]}
    assert decoded==[source,source]  # Eligibility then fresh coordinator comparison; no assembly, seed or matched C.


@pytest.mark.parametrize('kind,change',[('src','match'),('best','match'),('best','best')])
def test_full_snapshot_rechecks_foreground_changes_after_eligibility(saved,monkeypatch,tmp_path,kind,change):
    net,store,rows,_,_,source=saved
    original=saves._saved_current
    def current(*args):
        row=original(*args)
        with store.tx() as db:
            if change=='match':
                db.execute("UPDATE functions SET status='matched',matched_source=? WHERE name='best'",(source,))
            else:db.execute("UPDATE functions SET best_source='new source' WHERE name='best'")
        return row
    monkeypatch.setattr(saves,'_saved_current',current)
    monkeypatch.setattr(net,'_compile_snapshot',lambda *_:pytest.fail('stale eligibility scheduled compilation'))
    entries={'best':{'vram':1,('matched_by' if kind=='src' else 'best_by'):'author'}}
    files={kind+'/best.c':'new source'}
    checks=saves._saved_checks(net,net._project('game'),tmp_path,files,rows,{},entries,True,lambda:None)
    try:assert next(checks)[-1] is None
    finally:checks.close()


def test_unused_broken_payload_is_not_read_but_selected_comparison_still_validates(saved,tmp_path):
    net,store,rows,entries,files,source=saved
    with store.tx() as db:
        db.execute('DELETE FROM text_payloads WHERE hash=?',(payload_storage.digest('assembly '*1000),))
    # Assembly is not used when both entries skip compilation.
    assert saves._judge_saved(net,'game',tmp_path,{},rows,entries,lambda _:None,files=files)['matched']==1
    with store.tx() as db:
        db.execute('DELETE FROM text_payloads WHERE hash=?',(payload_storage.digest(source),))
    with pytest.raises(Exception,match='user-defined function'):
        saves._judge_saved(net,'game',tmp_path,{},rows,entries,lambda _:None,files=files)

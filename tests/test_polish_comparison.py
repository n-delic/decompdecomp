"""Polished inspectors expose the saved original without changing the accepted source."""
from decompnet.app.polish_history import detail

from .test_claims import _game

BEFORE='int f(int x) { return x; }'
AFTER='/* Return the supplied value. */\nint f(int value) { return value; }'


def test_polish_preserves_first_original_and_preview_shows_badge(tmp_path):
    net,pid=_game(tmp_path,[('f',40,'open')]);agent,_=net.local_agent('polisher')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source=?,best_source=?,matched_at=10 WHERE id=1",(BEFORE,BEFORE))
    proj=net._project('game');fn=net._function('f','game')
    net.readability._readable(proj,fn,agent,AFTER,original=BEFORE)
    preview=net.function_preview('game','f')
    assert preview['polished'] and preview['unpolished_source']==BEFORE
    assert preview['source']==AFTER
    net.readability._readable(proj,net._function('f','game'),agent,AFTER+'\n/* More context. */',original=AFTER)
    assert net._function('f','game')['unpolished_source']==BEFORE
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open',readable=0 WHERE id=1")
    assert detail(net.store,net._function('f','game'))=={'polished':False,'unpolished_source':None}


def test_legacy_polish_recovers_only_pre_match_successful_history(tmp_path):
    net,_=_game(tmp_path,[('f',40,'open')])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',readable=1,matched_at=10,matched_source=? WHERE id=1",(AFTER,))
        for source,score,at in [(BEFORE,0,9),('failed',10,9.5),(AFTER,0,11)]:
            db.execute('INSERT INTO attempts(function_id,source,score,created_at) VALUES(1,?,?,?)',(source,score,at))
    fn=net._function('f','game')
    assert detail(net.store,fn)['unpolished_source']==BEFORE
    assert detail(net.store,fn,history=False)['unpolished_source'] is None
    with net.store.tx() as db:db.execute('DELETE FROM attempts')
    assert detail(net.store,net._function('f','game'))=={'polished':True,'unpolished_source':None}


def test_unpolished_snapshot_uses_shared_compressed_storage(tmp_path):
    net,_=_game(tmp_path,[('f',40,'open')]);text=BEFORE+'\n/*'+'context '*200+'*/'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET matched_source=?,unpolished_source=? WHERE id=1',(text,text))
    assert net.store.one('SELECT unpolished_source FROM functions WHERE id=1')['unpolished_source']==text
    physical=net.store.one('SELECT matched_source,unpolished_source FROM main.functions WHERE id=1')
    assert isinstance(physical['unpolished_source'],bytes)
    assert physical['matched_source']==physical['unpolished_source']


def test_polish_comparison_controls():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    node=shutil.which('node')
    if node is None:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_polish_comparison.js'))],check=True,capture_output=True,text=True,timeout=30)


def test_repolishing_legacy_source_does_not_invent_an_original(tmp_path):
    net,_=_game(tmp_path,[('f',40,'open')]);agent,_=net.local_agent('polisher')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',readable=1,matched_source=? WHERE id=1",(AFTER,))
    net.readability._readable(net._project('game'),net._function('f','game'),agent,AFTER+'\n/* Refined. */',original=AFTER)
    assert net.function_preview('game','f')['unpolished_source'] is None

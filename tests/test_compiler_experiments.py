import json
import re

import pytest

from decompnet import context_preflight
from decompnet.app import agent_tokens, compiler_experiments, tools
from decompnet.network import Network, NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode',['rom','fork'])
def test_revision_profile_pairs_are_diagnostic_cached_and_scoped(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');b,_=net.local_agent('b')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode}),))
        db.execute('UPDATE functions SET best_source=?',('int f(){return 1;}',))
    net.claim_row(a,'game','f');_,rev=agent_tokens.candidate(net,a,'game','f');calls=[]
    monkeypatch.setattr(net,'_toolchain_named',lambda cfg,c:c)
    def check(agent,slug,function,compiler,flags,source,keep_match=True):
        calls.append((compiler,flags,source,keep_match));return 'profile: score 20, not a match'
    monkeypatch.setattr(net,'try_compiler',check)
    trials=[{'compiler':'fake','flags':'-O2','old':'return 1','new':'return 2'}, {'compiler':'fake','flags':'-O1'}]
    result=compiler_experiments.run(net,a,'game','f',rev,trials)
    assert len(calls)==2 and all(c[3] is False for c in calls)
    assert calls[0][2]=='int f(){return 2;}' and calls[1][2]=='int f(){return 1;}'
    trial=re.search(r'trial_id=(\w+)',result)[1]
    saved=json.loads(compiler_experiments.retrieve(net,a,'game','f',trial))
    assert saved['flags']=='-O2' and saved['source']==calls[0][2]
    again=compiler_experiments.run(net,a,'game','f',rev,trials)
    assert ('cached diagnostic' in again) == (mode=='rom')
    assert len(calls)==(2 if mode=='rom' else 4)
    assert net._session_diagnostic_compiles[a.id]==len(calls)  # Cached results are not new compilation progress.
    shared=json.loads(compiler_experiments.retrieve(net,b,'game','f',trial))
    assert shared['source']==saved['source'] and 'Historical diagnostic' in shared['verification']
    assert agent_tokens.candidate(net,a,'game','f')[1]==rev
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']==0
    assert 'compiler_experiments' in tools.project_tools(net,'game')


def test_preflight_rejects_only_definite_unconditional_externs(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',10,'matched')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')",(pid,))
        db.execute("UPDATE functions SET matched_source='extern s32 *value;'")
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:pytest.fail('preflight must avoid compilation'))
    assert 'preflight' in Network.edit_context(net,a,'game','','extern u8 *value;','add')
    members=[{'name':'f','matched_source':'extern int *value;'}]
    assert not context_preflight.conflicts('extern s32 *value;',members)
    assert not context_preflight.conflicts('#if(UNKNOWN)\nextern u8 *value;\n#endif',members)
    assert not context_preflight.conflicts('extern Custom *value;',members)


def test_later_mismatch_focus_and_draft_aliases(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    lines=['nop']*80;lines[10]='first';lines[60]='second'
    monkeypatch.setattr(net,'_asm_text',lambda *args:'\n'.join(lines))
    net.keep_diff(a.id,fn['id'],[('i','first','other'),('i','second','other')])
    assert 'Focus at target line 60' in agent_tokens.assembly(net,'game','f',focus='mismatch:2',agent=a)
    assert 'Target assembly lines 12:52' in agent_tokens.assembly(net,'game','f',focus='body',agent=a)
    with pytest.raises(NetworkError):
        agent_tokens.assembly(net,'game','f',focus='mismatch:1:2',agent=a)
    with net.store.tx() as db:db.execute("UPDATE functions SET seed='int f(){return 0;}'")
    assert 'Stored machine draft' in agent_tokens.function_view(net,a,'game','f','draft')

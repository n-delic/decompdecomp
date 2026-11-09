"""Synthetic evidence navigation and rejection caching never bypass acceptance checks."""
import pytest

from decompnet.app import agent_targets, agent_tokens
from decompnet.network import Network, NetworkError
from tests.test_claims import _game


def test_rejected_context_cache_rechecks_changed_inputs(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',10,'matched')]);a,_=net.local_agent('a')
    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'int x;','')",(pid,))
        db.execute("UPDATE functions SET matched_source='int f(){return 1;}'")
    calls=[]
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs: calls.append(1) or [('f','no longer matches')])
    def edit(message='test'):
        return Network.edit_context(net,a,'game','','int y;',message)
    assert 'REJECTED' in edit()
    assert 'Cached rejected' in edit('another explanation') and len(calls)==1
    with net.store.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address) VALUES(?,'value',8)",(pid,))
    assert 'Cached' not in edit() and len(calls)==2
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f(){return 2;}'")
    edit();assert len(calls)==3
    net.bump_generation(pid);edit();assert len(calls)==4
    # Success is never read from a rejection cache after the judge inputs change.
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config='{}'")
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs: [])
    assert '✅' in edit() and net._context(pid)['version']==2


@pytest.mark.parametrize('fork',[False,True])
def test_focused_assembly_uses_target_and_shared_diff(tmp_path,monkeypatch,fork):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    lines=['nop']*120;lines[70]='jal callee';lines[80]='sw $a0, 0x10($sp)'
    monkeypatch.setattr(net,'fork_slug',lambda slug: 'fork' if fork else None)
    if fork:
        from types import SimpleNamespace
        unit=SimpleNamespace(scratch=None)
        fp=SimpleNamespace(unit=lambda u:unit,diff=lambda *args:SimpleNamespace(target_asm=lambda:'\n'.join(lines)))
        monkeypatch.setattr(net.forks,'get',lambda slug:(None,fp,{}))
    else:
        monkeypatch.setattr(net,'_asm_text',lambda *args:'\n'.join(lines))
    net.keep_diff(a.id,fn['id'],[(' ','nop','nop'),('i',lines[80],'different')])
    call=agent_tokens.assembly(net,'game','f',limit=200,focus='callee',agent=a)
    mismatch=agent_tokens.assembly(net,'game','f',focus='mismatch',agent=a)
    assert 'Focus at target line 70' in call and len(call.splitlines())<=42
    assert 'Focus at target line 80' in mismatch
    assert 'No target region' in agent_tokens.assembly(net,'game','f',focus='absent',agent=a)


def test_patch_recovery_hints_never_fuzzily_apply():
    source='int value;\nvalue=1;\nvalue=2;'
    with pytest.raises(NetworkError) as error:
        agent_targets.replace_anchor(source,'value','other')
    assert 'occurrence 1, line 1' in str(error.value)
    assert 'occurrence 3, line 3' in str(error.value)
    with pytest.raises(NetworkError) as error:
        agent_targets.replace_anchor(source,'value = 1;','value=9;')
    assert 'Possible current line (not applied)' in str(error.value)


def test_variant_preview_shows_changed_region_not_header_prefix(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    source='/*'+'unchanged '*100+'*/\nint f(){return 1;}'
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f')
    def compile(agent,function,text,slug):
        net._record(fn,agent,10,text,1);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    result=agent_tokens.variants(net,a,'game','f',rev,[{'old':'return 1','new':'return 2'}],'test family')
    assert '-int f(){return 1;}' in result and '+int f(){return 2;}' in result
    assert 'unchanged unchanged' not in result


def test_repeated_stale_revision_omits_duplicate_delta(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net._agent_snapshots={(a.id,fn['id']):{'old':'int f(){return 1;}'}}
    first=str(agent_tokens.stale(net,a,fn,'old','int f(){return 2;}','new'))
    second=str(agent_tokens.stale(net,a,fn,'old','int f(){return 2;}','new'))
    assert 'Current changes' in first and 'already rejected' in second and 'Current changes' not in second
    assert len(second)<len(first)

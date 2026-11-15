from types import SimpleNamespace

from decompnet.app.collab import _share_saved_progress


def test_saved_work_progress_updates_before_and_after_host_checks():
    snapshots, lines, forgotten = [], [], []
    def log(line):
        lines.append(line)
    log.progress = snapshots.append
    candidates = [dict(name=f'f{i}',vram=i*4,size=4,status='matched',matched_source='source') for i in range(5)]
    candidates[3].update(status='open',best_source='best',best_score=10)
    state = {'functions':[dict(name=f'f{i}',vram=i*4,size=4) for i in range(4)],
             'matched':[dict(name='f0',vram=0)],'best':[]}
    def attempt(agent, function, source):
        active = snapshots[-1]
        assert active['function'] == function and active['phase'] == 'Host checking saved work'
        assert active['finished'] == (0 if function == 'f1' else 1)
        return {'matched':function=='f1','score':0 if function=='f1' else 60}
    link = SimpleNamespace(cursor={},_req=lambda *args:state,attempt=attempt,call=lambda *args,**kw:None)
    net = SimpleNamespace(local_agent=lambda _:('agent','token'))
    _share_saved_progress(net,'game',link,candidates,fork=False,best_comparable=True,
                          log=log,sync_room=False,forget=forgotten.append)
    counted = [s for s in snapshots if s.get('total')]
    assert {s['total'] for s in counted} == {3}
    assert [s['finished'] for s in counted] == [0,1,1,2,2,3]
    assert counted[-1]['confirmed'] == 1 and counted[-1]['submitted'] == 1
    assert snapshots[-1] == {'phase':"Synchronizing the host's results"}
    assert set(forgotten) == {f'f{i}' for i in range(5)}
    assert any('Host checking saved function 2 of 3: f2' in line for line in lines)
    assert any("wasn't confirmed" in line for line in lines)


def test_unreachable_host_does_not_advance_count():
    import pytest

    from decompnet.network import NetworkError
    snapshots=[]
    def log(_):pass
    log.progress=snapshots.append
    def fail(*args):raise NetworkError('host unavailable')
    candidate=dict(name='f',vram=4,size=4,status='matched',matched_source='source')
    link=SimpleNamespace(cursor={},_req=lambda *args:{'functions':[dict(name='f',vram=4,size=4)]},attempt=fail)
    net=SimpleNamespace(local_agent=lambda _:('agent','token'))
    with pytest.raises(NetworkError,match='host unavailable'):
        _share_saved_progress(net,'game',link,[candidate],fork=False,log=log,sync_room=False)
    assert snapshots[-1]['finished']==0 and snapshots[-1]['function']=='f'

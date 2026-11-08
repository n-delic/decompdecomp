from tests.test_claims import _game


def test_wait_reason_separates_pauses_and_selection_filters(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('a',10,'open'),('b',10,'open'),('c',10,'open')])
    agent,_=net.local_agent('worker')
    monkeypatch.setattr(net,'selection_counts',lambda *a: {'selected':2,'eligible':0})
    why,todo=net.selection_wait_reason('game',agent)
    assert '2 unchanged candidates paused' in why
    assert '1 open functions excluded' in why
    assert 'No AI request is running' in todo
    assert 'does not reset' in todo and 'shared across agents' in todo


def test_wait_reason_does_not_invent_candidate_pauses(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('a',10,'open')])
    agent,_=net.local_agent('worker')
    monkeypatch.setattr(net,'selection_counts',lambda *a: {'selected':0,'eligible':0})
    why,_=net.selection_wait_reason('game',agent)
    assert 'unchanged candidates paused' not in why
    assert '1 open functions excluded' in why

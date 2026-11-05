"""A rejected independent batch identifies every bad variant without running trials."""
import pytest

from decompnet.app import agent_evidence, agent_tokens, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_invalid_batch_reports_all_variant_positions_and_preserves_candidate(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    source='int f(){int a=1; int b=1; return a+b;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(agent,'game','f')
    _,revision=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr(tools,'_compile',lambda *a,**kw:pytest.fail('No partial batch may compile'))
    monkeypatch.setattr(agent_evidence,'baseline',lambda *a,**kw:pytest.fail('Bad explicit anchors must not build a baseline'))
    patches=[{'old':'return a+b','new':'return b+a'},
             {'old':'=1','new':'=2'},
             {'old':'missing','new':'present'}]
    text=tools.call(net,agent,'game','compile_variants',dict(function='f',revision=revision,patches=patches))
    assert 'Variant 1:' not in text
    assert 'Variant 2:' in text and 'Found 2 occurrences' in text
    assert 'Variant 3:' in text and 'Found 0 occurrences' in text
    assert f'Current candidate revision {revision}' in text
    assert 'No patches applied' in text and 'independent, not cumulative' in text
    assert agent_tokens.candidate(net,agent,'game','f')==(source,revision)
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0
    assert net.store.one('SELECT COUNT(*) n FROM agent_variant_runs')['n']==0
    assert net.store.one('SELECT agent_id FROM claims')['agent_id']==agent.id


def test_malformed_variant_is_identified_alongside_invalid_occurrence(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='int f(){return 1;}'")
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr(tools,'_compile',lambda *a,**kw:pytest.fail('Malformed batch must not compile'))
    with pytest.raises(NetworkError) as caught:
        agent_tokens.variants(net,agent,'game','f',revision,[None,{'old':'return 1','new':'return 2','occurrence':-1}])
    assert 'Variant 1: Every patch' in str(caught.value)
    assert 'Variant 2: occurrence must be' in str(caught.value)
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0

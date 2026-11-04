from decompnet.app import search_evidence
from tests.test_permute import AGENT, FakeNet


def test_compact_search_notes_are_bounded_and_invalidated_by_build_inputs():
    net=FakeNet();fn=net.function();proj=net.project_row('game')
    net._last_diffs={(AGENT.id,fn['id']):([('r','move $v0, $a0','move $v0, $a1')]*100,'legend')}
    search_evidence.remember(net,AGENT,proj,fn,fn['best_source'],40)
    summary=search_evidence.brief(net,proj,fn)
    assert '100 displayed differences' in summary and len(summary)<1000
    assert summary.count('=>')==4
    assert 'Try later_local_reuse.' in summary
    net._context=lambda *_:{'header':'changed','version':2}
    assert search_evidence.brief(net,proj,fn)==''


def test_durable_negative_results_are_summarized_only_for_current_build_inputs():
    from decompnet.app import agent_tokens
    net=FakeNet();fn=net.function();proj=net.project_row('game')
    scope = agent_tokens.target_scope(net,fn,'')
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_family_targets VALUES(?,?,?,?)',(fn['id'],scope,'compiled-error','hash'))
    summary = search_evidence.brief(net,proj,fn)
    assert '1 compiled-error' in summary and 'not proof' in summary
    net._context=lambda *_:{'header':'changed','version':2}
    assert search_evidence.brief(net,proj,fn) == ''

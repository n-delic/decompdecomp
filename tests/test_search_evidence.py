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


def test_output_fingerprint_summary_does_not_confuse_equal_scores_with_equal_output():
    net = FakeNet(); fn = net.function(); proj = net.project_row('game')
    net._last_diffs = {(AGENT.id,fn['id']):([('r','move $v0, $a0','move $v0, $a1')],'legend')}
    search_evidence.remember(net, AGENT, proj, fn, 'first', 40, output='assembly-a')
    search_evidence.remember(net, AGENT, proj, fn, 'second', 40, output='assembly-b')
    assert 'Repeated compiler output' not in search_evidence.brief(net, proj, fn)
    search_evidence.remember(net, AGENT, proj, fn, 'third', 40, output='assembly-a')
    brief = search_evidence.brief(net, proj, fn)
    assert '2 source variants' in brief and 'same measured function fingerprint' in brief
    net._context = lambda *_:{'header':'changed','version':2}
    assert 'Repeated compiler output' not in search_evidence.brief(net, proj, fn)


def test_agent_attempt_output_is_added_to_the_same_compact_history():
    from decompnet.app.engine.judging import JudgeMixin
    net = FakeNet(); fn = net.function(); proj = net.project_row('game')
    net._function = lambda *args:net.function()
    net._last_diffs = {(AGENT.id,fn['id']):([('r','move $v0, $a0','move $v0, $a1')],'')}
    result = dict(score=40, compiled_output=dict(fingerprint='measured',granularity='function'))
    JudgeMixin._remember_search_output(net,AGENT,'f','source-a','game',result)
    JudgeMixin._remember_search_output(net,AGENT,'f','source-b','game',result)
    assert '2 source variants' in search_evidence.brief(net,proj,fn)
    # A containing-unit fingerprint cannot claim two functions have identical code.
    result['compiled_output']['granularity'] = 'section'
    JudgeMixin._remember_search_output(net,AGENT,'f','source-c','game',result)
    assert '3 source variants' not in search_evidence.brief(net,proj,fn)

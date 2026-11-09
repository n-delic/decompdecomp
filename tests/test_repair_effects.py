import json

from decompnet.app import repair_effects as effects


def node(family, parent='parent'):
    return dict(source=family, parent_source=parent, parent_output='same', path=[family])


def test_two_unchanged_results_defer_same_output_and_keep_exploration():
    a, b, c = node('a'), node('b'), node('a', 'different')
    state = dict(pending=[a,b,c])
    effects.observe(state,a,'same');effects.observe(state,a,'same');effects.observe(state,a,'same')
    state = json.loads(json.dumps(state))
    effects.rotate(state)
    assert state['pending'] == [b,a,c]
    state['counts'] = {'scored':effects.DEFER_INTERVAL-1}
    effects.rotate(state)
    assert state['pending'] == [a,b,c]
    effects.observe(state,a,'changed')
    assert state['family_no_effect'][effects.key(a)] == 0


def test_missing_output_is_not_evidence_and_no_family_is_removed():
    a=node('a');state=dict(pending=[a])
    effects.observe(state,a,None)
    assert 'family_no_effect' not in state
    effects.rotate(state)
    assert state['pending'] == [a]


def test_equivalent_parent_cannot_repeatedly_reset_third_sibling_allowance():
    a,b=node('inert','new spelling'),node('fresh')
    state=dict(pending=[a,b],family_no_effect={effects.key(a):2},output_no_effect={effects.output_scope(a):5})
    effects.rotate(state)
    assert state['pending']==[b,a]
    assert not effects.needs_page(state)
    assert effects.needs_page({**state,'pending':[a]})
    state['counts']={'scored':7};effects.rotate(state)
    assert state['pending'][0]==a

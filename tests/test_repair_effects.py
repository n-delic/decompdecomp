import json

from decompnet.app import repair_effects as effects


def node(family, parent='parent'):
    return dict(source=family, parent_source=parent, parent_output='same', path=[family])


def test_two_unchanged_results_defer_only_same_parent_and_keep_exploration():
    a, b, c = node('a'), node('b'), node('a', 'different')
    state = dict(pending=[a,b,c])
    effects.observe(state,a,'same');effects.observe(state,a,'same')
    state = json.loads(json.dumps(state))
    effects.rotate(state)
    assert state['pending'] == [b,c,a]
    state['counts'] = {'scored':2}
    effects.rotate(state)
    assert state['pending'] == [a,b,c]
    effects.observe(state,a,'changed')
    assert state['family_effects'][effects.key(a)] == 0


def test_missing_output_is_not_evidence_and_no_family_is_removed():
    a=node('a');state=dict(pending=[a])
    effects.observe(state,a,None)
    assert 'family_effects' not in state
    effects.rotate(state)
    assert state['pending'] == [a]

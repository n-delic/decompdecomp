"""Two repair families often write the same C: a chain filtered to either name still sees the child."""
from decompnet.app import draft_repairs, prove_chain


def test_a_source_keeps_every_family_that_proposed_it():
    labels = draft_repairs.Labels()
    labels.setdefault('child', 'first')
    labels.setdefault('child', 'second')  # the earlier label stays
    labels['child'] = 'third'  # a later lane relabels it
    labels.update({'child': 'fourth'})
    assert labels['child'] == 'fourth'
    assert labels.names('child') == ['fourth', 'first', 'second', 'third']
    assert labels.names('unknown') == ['unlabelled']


def test_a_chain_filtered_to_either_family_takes_the_shared_child():
    def generate(source):
        labels = draft_repairs.Labels()
        if source == 'start':
            labels['child'] = 'kept'
            labels.setdefault('child', 'dropped')
            return draft_repairs.pairs(['child'], labels)
        return []

    for families in ({'kept'}, {'dropped'}, None):
        final, best, taken, _ = prove_chain.chain('start', generate, lambda sources: [0 if s == 'child' else 9 for s in sources],
                                                  families)
        assert (final, best) == ('child', 0)
        assert taken[0][0] == ('dropped' if families == {'dropped'} else 'kept')
        assert taken[0][3] == 1  # judged once, however many families proposed it


def test_the_proposal_workers_other_names_reach_the_caller(monkeypatch):
    result = {'candidates': [('child', 'kept')], 'aliases': [('child', ['kept', 'dropped'])]}
    monkeypatch.setattr(draft_repairs, '_worker_batch', lambda request, check: result)
    labels = draft_repairs.Labels()
    assert draft_repairs._in_worker('s', 'f', 'mips', '', (), labels, {}) == ['child']
    assert labels.names('child') == ['kept', 'dropped']

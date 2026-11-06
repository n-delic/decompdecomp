from decompnet import repair_review as review


def example(before='int f(void){return 1;}', after='int f(void){return 2;}', provenance=None):
    return {'project_id': 1, 'evidence': {'target_sha256':'target', 'comparable_settings':True,
            'before':{'source':before, 'settings':{'compiler':'one'}},
            'after':{'source':after, 'source_sha256':'hash', 'context_version':1, 'settings':{'compiler':'one'}},
            'provenance':provenance}}


def test_classification_is_a_review_not_an_automatic_rule():
    result = review.classify(example())
    assert result['category'] == 'constants' and result['status'] == 'reviewed'
    assert 'hypothesis' not in result['category']
    assert review.classify(example(None))['status'] == 'reviewed'


def test_only_provenance_backed_automatic_paths_are_implemented():
    assert review.classify(example(provenance={'kind':'deep local repair','family':'small_copy'}))['status'] == 'implemented'
    assert review.classify(example(provenance={'kind':'agent','family':'small_copy'}))['status'] == 'reviewed'


def test_duplicate_identity_requires_same_target_source_context_and_settings():
    a, b = example(), example()
    assert review.identity(a) == review.identity(b)
    b['evidence']['after']['settings']['compiler'] = 'another'
    assert review.identity(a) != review.identity(b)
    b['evidence']['target_sha256'] = None
    assert review.identity(b) is None


def test_source_layout_and_multi_edit_evidence_are_not_claimed_as_proved_causes():
    result = review.classify(example('int f(void) { return 1; }', 'int f(void) {\n return 1;\n}'))
    assert result['category'] == 'layout' and 'not a proved mechanism' in result['note']

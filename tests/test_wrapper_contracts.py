from types import SimpleNamespace

import pytest

from decompnet.app import wrapper_contracts as wrappers
from decompnet.app.engine.briefs import BriefMixin

SOURCE = 'M2C_UNK downstream();\nvoid stub(void) { downstream(); }'


def test_unspecified_stub_is_advice_not_a_signature_verdict():
    assert wrappers.forwarding(SOURCE, 'stub') == 'downstream'
    note = wrappers.source_hint(SOURCE, 'stub')
    assert 'advisory' in note and 'incoming arguments' in note
    assert 'No signature was changed' in note
    assert wrappers.forwarding('/* comment */\n'+SOURCE, 'stub') == 'downstream'
    assert wrappers.forwarding(SOURCE.replace('(void)', '()'), 'stub') == 'downstream'


@pytest.mark.parametrize('source', [
    SOURCE.replace('downstream();\n', 'downstream(void);\n'),
    SOURCE.replace('downstream();\n', 'downstream(int x);\n'),
    SOURCE.replace('void stub(void)', 'void stub(int x)'),
    SOURCE.replace('downstream(); }', 'downstream(1); }'),
    SOURCE.replace('downstream(); }', 'downstream(); downstream(); }'),
    SOURCE.replace('downstream(); }', 'if (ready) downstream(); }'),
    SOURCE.replace('downstream(); }', 'return downstream(); }'),
    SOURCE.replace('void stub', 'int stub'),
    '#define downstream() other()\n'+SOURCE,
    '#define stub alias\n'+SOURCE,
    'void downstream(void) {}\n'+SOURCE,
    'M2C_UNK downstream();\n'+SOURCE,
    'void stub(void) { stub(); }',
    'void stub(void) {}',
    '/* '+SOURCE+' */',
])
def test_explicit_ambiguous_and_complex_stubs_are_not_flagged(source):
    assert wrappers.source_hint(source, 'stub') == ''


def test_callee_lookup_is_bounded_and_advice_does_not_copy_source():
    calls = []

    def all_rows(sql, project, names, limit):
        calls.append((sql, project, names, limit))
        return [{'name': 'stub', 'matched_source': SOURCE},
                {'name': 'known', 'matched_source': 'void known(void) {}'}]

    net = SimpleNamespace(store=SimpleNamespace(all=all_rows))
    note = wrappers.brief(net, {'id': 7}, {'name': 'caller'}, 'void caller(void) { stub(); }')
    assert len(calls) == 1 and calls[0][1] == 7
    assert calls[0][3] == 64000 and 'length(matched_source)<=?' in calls[0][0]
    assert "status='matched'" in calls[0][0]
    assert 'stub -> downstream' in note and 'known ->' not in note
    assert 'M2C_UNK' not in note and 'void stub' not in note
    assert wrappers.source_hint(SOURCE, 'unit/stub') == ''
    assert wrappers.source_hint(' '*64001+SOURCE, 'stub') == ''


def test_project_brief_receives_advice_before_mode_specific_rendering(monkeypatch):
    from decompnet.app import draft_reports, search_evidence

    monkeypatch.setattr(draft_reports, 'read', lambda *_: {})
    monkeypatch.setattr(draft_reports, 'summary', lambda *_: '')
    monkeypatch.setattr(search_evidence, 'brief', lambda *_: '')
    proj = {'id': 7}
    net = SimpleNamespace(store=SimpleNamespace(one=lambda *_: proj, all=lambda *_: []),
                          cfg=lambda _: {'kind': 'fork'}, forks=SimpleNamespace(brief=lambda *_: 'project brief'),
                          _lifetime_hints=lambda _: '')
    fn = dict(id=1, project_id=7, status='matched', name='stub', matched_source=SOURCE,
              best_source=None, seed=None)
    text = BriefMixin._brief(net, fn, None, 'FUNCTION')
    assert 'project brief' in text and 'stub -> downstream' in text


def test_claim_hint_does_not_query_callees(monkeypatch):
    from decompnet.app import agent_tokens, claim_state, draft_reports

    monkeypatch.setattr(agent_tokens, 'candidate', lambda *_: (SOURCE, 'revision'))
    monkeypatch.setattr(draft_reports, 'read', lambda *_: {})
    monkeypatch.setattr(draft_reports, 'summary', lambda *_: '')
    monkeypatch.setattr(claim_state, 'current', lambda *_: 'claim')
    net = SimpleNamespace(store=SimpleNamespace(one=lambda *_: {'slug': 'sample', 'name': 'Sample'}))
    fn = dict(id=1, project_id=7, status='open', name='stub', matched_source=None,
              best_source=SOURCE, seed=None, unit='', size=28)
    text = claim_state.brief(net, fn, SimpleNamespace(id=2), 'CLAIMED')
    assert 'Preparation: deferred' in text and 'stub -> downstream' in text

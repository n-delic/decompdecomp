"""Every child of an m2c draft's written-out spelling is offered, not only the last one."""
from decompnet.app import draft_repairs


def _run(monkeypatch, raw, plain, labels):
    def spelled(source, name, arch, asm, flags, labels, **kwargs):
        children = raw if source == 'draft' else plain
        for text in children:
            labels[text] = f'family_of_{text}'
        return list(children)

    monkeypatch.setattr(draft_repairs, '_spelled', spelled)
    monkeypatch.setattr(draft_repairs.m2c_spelling, 'plain', lambda source: 'written')
    return draft_repairs._variants('draft', 'f', 'mips', labels=labels, limit=None)


def test_every_plain_child_is_merged_and_labelled(monkeypatch):
    labels = draft_repairs.Labels()
    found = _run(monkeypatch, ['a'], ['a', 'b', 'draft', 'c'], labels)
    assert found == ['a', 'b', 'c']
    assert labels['b'] == 'family_of_b' and labels['c'] == 'family_of_c'


def test_a_plain_dict_of_labels_gets_them_too(monkeypatch):
    assert _run(monkeypatch, ['a'], ['b', 'c'], {}) == ['a', 'b', 'c']


def test_no_plain_child_is_not_an_error(monkeypatch):
    assert _run(monkeypatch, ['a'], [], draft_repairs.Labels()) == ['a']

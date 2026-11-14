"""Large saved manifests retain contributor order without repeated list searches."""
from copy import deepcopy

import pytest

from decompnet.app.progress import _merge_manifest


@pytest.mark.parametrize('key', ['layout', 'symbols', 'notes', 'knowledge', 'set_aside'])
def test_manifest_merge_preserves_order_edits_deletions_and_duplicate_current_identities(key):
    def entry(name, value):
        if key == 'knowledge':return name
        if key == 'notes':return {'function':name, 'text':name, 'by':'author', 'value':value}
        return {'name':name, 'vram':4, 'value':value}
    old = [entry('keep',0),entry('edit',0),entry('delete',0)]
    new = [entry('keep',0),entry('edit',1),entry('added',1),entry('added',2)]
    current = [None,entry('keep',9),entry('edit',0),entry('keep',9),entry('delete',0),entry('external',7)]
    before = deepcopy((old,new,current))
    result = _merge_manifest(old,new,current,key)
    # Unknown list items keep the pre-existing placement rule; named duplicates in
    # current remain duplicated, while newly introduced identities are appended once.
    expected = [None,entry('keep',9),entry('edit',1),entry('keep',9),entry('external',7),entry('added',2)]
    assert result == expected
    assert (old,new,current) == before


def test_manifest_merge_symbol_order_requires_linear_identity_comparisons():
    class Name(str):
        comparisons = 0
        __hash__ = str.__hash__

        def __eq__(self, other):
            type(self).comparisons += 1
            return super().__eq__(other)

    size = 1500
    old = [{'name':Name(f'symbol{i}'), 'value':0} for i in range(size)]
    new = [{'name':Name(f'symbol{i}'), 'value':int(i==0)} for i in range(size)]
    current = deepcopy(old)
    Name.comparisons = 0
    result = _merge_manifest(old,new,current,'symbols')
    comparisons = Name.comparisons
    assert [str(item['name']) for item in result] == [f'symbol{i}' for i in range(size)]
    assert result[0]['value'] == 1 and result[-1]['value'] == 0
    assert comparisons < size * 20  # A list membership scan takes over a million comparisons here.

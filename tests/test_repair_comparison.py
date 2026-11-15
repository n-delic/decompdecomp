"""Container provenance may differ; compiler, target-endian and header inputs may not."""
from copy import deepcopy

import pytest

from decompnet.repair_comparison import differences


def snapshot():
    return {'mode':'rom', 'settings_source':'compile invocation snapshot', 'big_endian':True,
            'language':'c', 'context_unit':'',
            'rom':{'sha1':'a'*40, 'byte_order':'v64', 'region':'sample'},
            'context_sha256':'b'*64, 'scoring_revision':'c'*64,
            'compiler':{'toolchain':'example', 'flags':['-O2']}, 'effective_command':['cc','-O2','{src}']}


def test_container_metadata_is_ignored_only_with_complete_same_image_evidence():
    before = snapshot(); after = deepcopy(before); after['rom']['byte_order'] = 'z64'
    frozen = deepcopy((before, after))
    assert differences(before, after) == set()
    assert (before, after) == frozen
    after['big_endian'] = False
    assert differences(before, after) == {'big_endian'}


@pytest.mark.parametrize('key,value', [('mode','project'), ('settings_source','project configuration'),
    ('context_sha256',None), ('scoring_revision','old'), ('big_endian',1),
    ('compiler',{}), ('effective_command',[]), ('language',None), ('context_unit',None)])
def test_incomplete_or_imported_evidence_does_not_relax_comparison(key, value):
    before = snapshot(); before[key] = value; after = deepcopy(before); after['rom']['byte_order'] = 'z64'
    assert differences(before, after) == {'rom'}


@pytest.mark.parametrize('key,value', [('compiler',{'toolchain':'other','flags':['-O2']}),
    ('context_sha256','d'*64), ('scoring_revision','e'*64), ('effective_command',['cc','-O1']),
    ('gp',123), ('context_unit','other'), ('language','c++')])
def test_real_input_changes_remain_visible_alongside_container_metadata(key, value):
    before = snapshot(); after = deepcopy(before); after['rom']['byte_order'] = 'z64'; after[key] = value
    assert differences(before, after) == {key}


@pytest.mark.parametrize('sha', ['different', 'd'*40, None])
def test_missing_changed_or_invalid_image_hash_is_not_equivalent(sha):
    before = snapshot(); after = deepcopy(before); after['rom'].update(sha1=sha, byte_order='z64')
    assert differences(before, after) == {'rom'}


def test_missing_and_null_fields_are_distinct():
    before = snapshot(); after = deepcopy(before); after['gp'] = None
    assert differences(before, after) == {'gp'}



def test_other_image_metadata_is_not_discarded():
    before = snapshot(); after = deepcopy(before)
    after['rom'].update(byte_order='z64', region='different')
    assert differences(before, after) == {'rom'}

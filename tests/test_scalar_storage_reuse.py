from decompnet.app import draft_repairs
from decomprepair import scalar_storage_reuse

SOURCE = 'int f(int mode, int x) { int resource; short next; if (mode) { resource = x; use(resource); } else { next = x + 8; use(next); } return 0; }'


def generated(source=SOURCE, arch='mips', header=''):
    return [s for _,s in scalar_storage_reuse.variants(source,'f',header,arch)]


def test_preserves_narrowing_at_assignment_and_unused_declaration():
    result=generated()
    assert len(result)==1
    assert 'short next;' in result[0]
    assert 'resource = (short)(x + 8); use(resource);' in result[0]
    assert 'int resource;' in result[0]


def test_unsigned_short_still_promotes_to_signed_int():
    result=generated(SOURCE.replace('short next','unsigned short next'))
    assert '(unsigned short)(x + 8)' in result[0]
    assert not generated(SOURCE.replace('int resource','unsigned int resource'))


def test_live_values_and_unknown_types_are_not_merged():
    assert not generated(SOURCE.replace('use(next);','use(next); use(resource);'))
    assert not generated(SOURCE.replace('short next','Unknown next'))
    assert not generated(arch='unknown')


def test_escapes_uninitialized_values_and_hidden_macro_writes_are_rejected():
    assert not generated(SOURCE.replace('use(next);','use(&next);'))
    assert not generated(SOURCE.replace('next = x + 8;','use(next); next = x + 8;'))
    assert not generated(SOURCE.replace('use(next);','WRITE(next);'),header='#define WRITE(v) ((v)++)')
    assert not generated(SOURCE.replace('next = x + 8;','next += x + 8;'))


def test_shared_agent_family_is_registered():
    assert scalar_storage_reuse.FAMILY in draft_repairs.AGENT_FAMILIES


def test_stale_register_hints_never_change_eligibility():
    stale={'source_sha256':'old','mapping':{'scalar_registers':{'1':'$s0'}}}
    assert scalar_storage_reuse.variants(SOURCE,'f','','mips',stale)==scalar_storage_reuse.variants(SOURCE,'f','','mips')

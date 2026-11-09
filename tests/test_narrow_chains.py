"""A bounded linked hypothesis can cross a score valley; only the normal judge keeps it."""
import pytest

from decompnet.app import draft_repairs, narrow_chains

SOURCE='void f(){s32 wide;s32 copy;u8 narrow;wide=read();copy=wide;copy=(u16)copy&0xFF;narrow=wide&0xFF;if(narrow)return;send(wide&0xFF);}'


def test_linked_width_cast_reuse_and_copy_cleanup_are_one_candidate():
    out=narrow_chains.variants(SOURCE,'f')
    assert len(out)==2
    assert 'u16 narrow;' in out[0]
    assert 'copy = wide & 0xFF;' in out[0]
    assert 'narrow = (u8)(s8)wide;' in out[0]
    assert 'send(narrow&0xFF)' in out[0]
    assert 'copy=wide;copy=(u16)copy&0xFF;' in out[1]
    assert out[0] in draft_repairs.variants(SOURCE,'f','mips')


@pytest.mark.parametrize('old,new',[
    ('u8 narrow;','volatile u8 narrow;'),
    ('s32 wide;','volatile s32 wide;'),
    ('send(wide&0xFF);','wide++;send(wide&0xFF);'),
    ('send(wide&0xFF);','++narrow;send(wide&0xFF);'),
    ('send(wide&0xFF);','narrow+=1;send(wide&0xFF);'),
    ('send(wide&0xFF);','save(&wide);send(wide&0xFF);'),
    ('send(wide&0xFF);','save(&narrow);send(wide&0xFF);'),
    ('narrow=wide&0xFF;','narrow=next()&0xFF;'),
    ('send(wide&0xFF);','send(wide&0xFF,wide&0xFF);'),
    ('send(wide&0xFF);','send(wide);')])
def test_skip_mutated_escaped_qualified_or_unsupported_chains(old,new):
    assert not narrow_chains.variants(SOURCE.replace(old,new),'f')


def test_definition_isolation_and_opaque_source():
    source=SOURCE.replace('void f()','void g()')+' '+SOURCE
    out=narrow_chains.variants(source,'f')
    assert out and all(s.startswith(source[:source.index(' void f()')]) for s in out)
    assert not narrow_chains.variants('void f(){say("u8 n;n=w&0xFF;send(w&0xFF);");}','f')


def test_shadowed_locals_do_not_share_a_repair_chain():
    source=SOURCE.replace('send(wide&0xFF);','{s32 wide;send(wide&0xFF);}')
    assert not narrow_chains.variants(source,'f')

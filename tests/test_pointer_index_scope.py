from decompnet.app.pointer_index_scope import FAMILY, variants

SOURCE='void f(S *s, u32 *q) { s32 i; i=0; { u32 **slot=s->tbl+i; *slot=(u32 *)q; } }'


def test_couples_typed_operands_with_one_shot_scope():
    [(family,candidate)]=variants(SOURCE,'f')
    assert family==FAMILY
    assert 'do { u32 **__repair_index_scope_base = s->tbl;' in candidate
    assert 's32 __repair_index_scope_offset = i;' in candidate
    assert '*(__repair_index_scope_base + __repair_index_scope_offset) = (u32 *)q;' in candidate
    assert '} while (0);' in candidate
    assert not variants(candidate,'f')


def test_rejects_side_effects_escapes_shadows_qualifiers_and_macros():
    for source in (SOURCE.replace('s->tbl+i','s->tbl+i++'),
                   SOURCE.replace('*slot=(u32 *)q;','escape(slot); *slot=(u32 *)q;'),
                   SOURCE.replace('i=0;','i=0; { short i; }'),
                   SOURCE.replace('s32 i;','volatile s32 i;'),
                   SOURCE.replace('s32 i;','float i;')):
        assert not variants(source,'f')
    assert not variants(SOURCE,'f','#define q next()')
    assert variants(SOURCE,'f','extern volatile int device;')  # unrelated declarations are not evaluated


def test_family_reaches_shared_search_and_agent_pools():
    from decompnet.app import draft_repairs, local_experiments
    assert FAMILY in draft_repairs.AGENT_FAMILIES
    assert FAMILY in local_experiments.FAMILIES
    labels={}
    candidates=draft_repairs.variants(SOURCE,'f','mips','',[],labels,limit=None)
    assert any(labels[s]==FAMILY for s in candidates)


def test_caps_candidates_and_preserves_self_referential_slots():
    block='{ u32 **slot=s->tbl+i; *slot=(u32 *)q; }'
    source=SOURCE.replace(block,block*4)
    assert len(variants(source,'f'))==2
    assert not variants(SOURCE.replace('(u32 *)q','(u32 *)slot'),'f')


def test_shared_candidate_is_available_for_every_supported_processor():
    from decompnet.app import draft_repairs
    for arch in ('mips','ppc','x86','arm','thumb'):
        labels={}
        candidates=draft_repairs.variants(SOURCE,'f',arch,'',[],labels,limit=None)
        assert any(labels[s]==FAMILY for s in candidates)

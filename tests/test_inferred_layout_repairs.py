from decomprepair import inferred_layout_repairs as repair


def test_consistent_repeated_accesses_make_a_candidate_local_record():
    source = 'void f(void *p) { *(int*)((char*)p+20) = 1; use(*((int *) (((char *) p) + 20))); *(short*)((char*)p+24) = 2; }'
    variants = repair.variants(source, 'f', 'mips')
    assert len(variants) == 1
    text = variants[0][1]
    assert 'unsigned char pad_0[20]; int field_14; short field_18;' in text
    assert text.count('((__repair_record_p*)p)->field_14') == 2
    assert 'void f(void *p)' in text
    assert 'typedef struct' in text


def test_conflicting_overlapping_misaligned_or_dynamic_layouts_are_rejected():
    source = 'void f(void *p) { use(*(int*)((char*)p+20)); use(*(int*)((char*)p+20)); use(*(short*)((char*)p+24)); }'
    for invalid in (source.replace('short', 'char').replace('p+24','p+21'),
                    source.replace('p+24','p+25'), source.replace('p+24','p+offset'),
                    source.replace('*(short*)','*(double*)'), source.replace('void *p','volatile void *p')):
        assert not repair.variants(invalid, 'f', 'mips')
    assert not repair.variants(source, 'f', 'mips64')
    assert not repair.variants(source.replace('p+20', 'p+24'), 'f', 'mips')


def test_comments_strings_and_other_functions_are_not_inferred():
    source = 'void g(void *p) { use(*(int*)((char*)p+20)); use(*(int*)((char*)p+20)); use(*(int*)((char*)p+24)); }\nvoid f(void) { puts("*(int*)((char*)p+24)"); }'
    assert not repair.variants(source, 'f', 'mips')


def test_initialized_local_byte_pointer_offsets_use_record_views():
    source = 'void f(void) { u8 *p = global; use(*((u16 *)(p + 30))); use(*((u16 *)(p + 14))); use(*((u16 *)(p + 14))); }'
    variants = repair.variants(source, 'f', 'mips')
    assert len(variants) == 1
    text = variants[0][1]
    assert 'u8 *p = global;' in text
    assert 'unsigned char pad_0[14]; unsigned short field_E;' in text
    assert text.count('((__repair_record_p*)p)->field_E') == 2
    assert 'unsigned short field_1E;' in text


def test_implicit_offsets_require_an_unescaped_unique_byte_pointer():
    source = 'void f(void) { u8 *p = global; use(*((u16 *)(p + 30))); use(*((u16 *)(p + 14))); use(*((u16 *)(p + 14))); }'
    for extra in ('p = other;', 'p++;', 'escape(p);', 'escape(&p);', 'u8 *alias = p;',
                  '{u8 *p = other;}', 'use(p[0]);', 'use(*((u32 *)(p + 14)));'):
        assert not repair.variants(source.replace(' }', extra + ' }'), 'f', 'mips')
    for before in ('#define global other\n', '#if ENABLED\n'):
        assert not repair.variants(before + source, 'f', 'mips')
    for typ in ('u16', 'void', 'volatile u8', 'const u8'):
        assert not repair.variants(source.replace('u8 *p', typ + ' *p'), 'f', 'mips')


def test_implicit_byte_forms_and_supported_architectures():
    source = 'void f(void) { u8 *p = global; use(*((u16 *)(p + 30))); use(*((u16 *)(p + 14))); use(*((u16 *)(p + 14))); }'
    for typ in ('char', 's8', 'unsigned char', 'signed char'):
        for arch in ('mips', 'ppc', 'x86', 'arm', 'thumb'):
            assert repair.variants(source.replace('u8 *p', typ + ' *p'), 'f', arch)
    for invalid in (source.replace('void f(void) { u8 *p = global;', 'void f(u8 *p) {'),
                    source.replace('u8 *p = global;', 'u8 *p = global, *other;'),
                    source.replace('p + 30', 'p + offset')):
        assert not repair.variants(invalid, 'f', 'mips')

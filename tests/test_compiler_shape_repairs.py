"""Source-shape hypotheses preserve surrounding code and leave acceptance to the judge."""
from decompnet.app import compiler_shape_repairs as repair
from decompnet.app import draft_repairs


def shapes(source, header=''):
    return repair.variants(source, 'f', 'mips', ['-g3'], header)


def test_short_copy_requires_bounded_distinct_arrays_and_preserves_alignment():
    header = 'extern const char text[4] __attribute__((aligned(2)));'
    source = 'void f(void) { char buf[16]; *(s16*)buf = *(s16*)text; buf[2] = text[2]; use(buf); }'
    candidates = repair.small_copies(source, 'f', header)
    assert len(candidates) == 1
    assert '__builtin_memcpy(buf, text, 3); use(buf);' in candidates[0]
    for invalid in (source.replace('buf[16]', 'buf[2]'), source.replace('char buf[16]', 'char *buf'),
                    source.replace('text[2]', 'text[3]'), source.replace('text', 'buf')):
        assert not repair.small_copies(invalid, 'f', header)
    assert not repair.small_copies(source, 'f', 'extern s16 text;')
    assert not repair.small_copies(source, 'f', header.replace('const', 'volatile'))


def test_copy_reaches_normal_draft_pipeline_with_family_label():
    source = 'extern char b[4]; void f(void) { char a[4]; *(short*)a = *(short*)b; a[2] = b[2]; }'
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert any(labels[p] == 'small_copy' and '__builtin_memcpy' in p for p in proposals)


def test_typed_record_uses_existing_field_and_keeps_shared_header_unchanged():
    header = 'typedef struct { char pad[20]; void * cursor; } Packet;'
    source = 'void f(void *p) { *(void **)((char*)p + 20) = next; }'
    proposals = shapes(source, header)
    assert ('typed_record_address', 'void f(void *p) { ((Packet*)p)->cursor = next; }') in proposals
    assert not shapes(source, header.replace('20', '24'))
    assert not shapes(source, header.replace('char pad[20]', 'Unknown pad'))
    assert not shapes(source, '#pragma pack(1)\n' + header)


def test_one_shot_loop_removal_keeps_scope_and_rejects_loop_control():
    source = 'void f(void) { do { use(); } while (0); after(); }'
    assert ('one_shot_scope', 'void f(void) { { use(); } after(); }') in shapes(source)
    for token in ('break;', 'continue;', 'goto done;', 'done: use();'):
        assert not shapes(source.replace('use();', token, 1))
    assert not shapes(source.replace('while (0)', 'while (ready)'))


def test_pointer_scope_is_local_and_does_not_move_other_statements():
    source = 'void f(void) { void *p; before(); p = packet->cursor; packet->cursor = p+8; write(p); after(); }'
    candidates = [s for f, s in shapes(source) if f == 'pointer_block_scope']
    assert candidates == ['void f(void) {  before(); { void *p; p = packet->cursor; packet->cursor = p+8; write(p); } after(); }']
    assert not shapes(source.replace('write(p);', 'if (ready) write(p);'))
    assert not shapes(source.replace('write(p);', 'write(&p);'))


def test_matrix_base_can_share_positive_field_address_with_negative_offset():
    source = 'void f(void) { s32 *p; s32 *base; p = &matrix[13]; base = &matrix[0]; use(base); }'
    assert ('array_address_share', source.replace('base = &matrix[0];', 'base = p - 13;')) in shapes(source, 'extern s32 matrix[16];')
    assert not shapes(source, 'extern s32 *matrix;')


def test_only_requested_definition_is_changed_and_comments_are_opaque():
    source = 'void other(void) { do { use(); } while(0); }\nvoid f(void) { /* do { x(); } while(0); */ use(); }'
    assert not shapes(source)
    assert not repair.variants('void f(void) { do { use(); } while(0); }', 'f', 'unknown')


def test_volatile_typedefs_in_common_header_do_not_disable_unrelated_copies():
    source = 'extern char b[4]; void f(void) { char a[4]; *(short*)a = *(short*)b; a[2] = b[2]; }'
    assert repair.small_copies(source, 'f', 'typedef volatile unsigned char vu8;')


def test_multiple_copy_sites_have_a_combined_candidate():
    source = 'extern char b[4]; void f(void) { char a[4]; *(short*)a = *(short*)b; a[2] = b[2]; use(a); *(short*)a = *(short*)b; a[2] = b[2]; }'
    candidates = repair.small_copies(source, 'f')
    assert any(text.count('__builtin_memcpy') == 2 for text in candidates)


def test_two_independent_lifetime_merges_can_be_tried_together():
    source = 'void f(void) { int a; int b; int c; int d; a=one(); use(a); b=two(); use(b); c=three(); use(c); d=four(); use(d); }'
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    combined = [s for s in candidates if labels[s] == 'combined_integer_lifetimes']
    assert combined and all(s.count('int ') == 2 for s in combined)


def test_machine_draft_copies_are_lazy_and_keep_the_original_first():
    source = 'extern char b[4]; void f(void) { char a[4]; *(short*)a = *(short*)b; a[2] = b[2]; }'
    visited = []
    def drafts():
        yield 'raw_m2c', source
        visited.append('local fallback')
        yield 'local_context_m2c', source
    stream = repair.machine_draft_copies(drafts(), 'f')
    assert next(stream) == ('raw_m2c', source)
    assert not visited
    family, text = next(stream)
    assert family == 'raw_m2c_small_copy' and '__builtin_memcpy' in text
    assert not visited


def test_c_octal_bounds_and_offsets_do_not_crash_or_use_decimal_layout():
    source = 'extern char b[04]; void f(void) { char a[010]; *(short*)a = *(short*)b; a[2] = b[2]; }'
    assert repair.small_copies(source, 'f')
    header = 'typedef struct { char pad[024]; int field; } Record;'
    assert ('Record', 'int', 'field', 20) in repair.fields(header)

from decompnet.app import generation_calls


def test_narrow_store_scope_runs_through_generation_worker():
    source = """void f(void *record, int value) {
        if (value) {
            *((s16 *) (((char *) record) + 0x38)) = value;
            *((s16 *) (((char *) record) + 0x42)) = value + 1;
            *((u8 *) (((char *) record) + 0x3F)) = value;
        }
    }"""
    proposals, = generation_calls.library([
        ('decomprepair.scheduling_scope.variants', (source, 'f', 'mips')),
    ])
    (family, text), = proposals
    assert family == 'scheduling_scope'
    assert 'do { *((s16 *) (((char *) record) + 0x42)) = value + 1; } while (0);' in text

from decompnet.app import loop_storage_shapes

SOURCE='void f(void) { unsigned int i; i=0; do { use(i); i++; use(i); } while(i<4); }'


def test_optional_backedge_keeps_body_scope_and_original_condition():
    family,source=loop_storage_shapes.variants(SOURCE,'f',arch='mips')[0]
    assert family=='explicit_loop_backedge'
    assert '__repair_rows: { use(i); i++; use(i); }' in source
    assert 'if (i<4) goto __repair_rows;' in source
    assert SOURCE.endswith('while(i<4); }')


def test_counter_staging_follows_backedge_and_commits_before_latch():
    backedge=loop_storage_shapes.variants(SOURCE,'f',arch='mips')[0][1]
    results=loop_storage_shapes.variants(backedge,'f',arch='mips')
    assert results
    family,source=results[0]
    assert family=='row_number_staging'
    assert '__repair_row_number = i + 1; use(__repair_row_number);' in source
    assert source.index('i = __repair_row_number;')<source.index('if (i<4)')


def test_loop_directed_transfers_and_macros_are_not_reinterpreted():
    for statement in ('break;', 'continue;', 'goto out;', 'if (i) break;'):
        assert not loop_storage_shapes.variants(SOURCE.replace('use(i); i++;',statement+' i++;'),'f',arch='mips')
    assert not loop_storage_shapes.variants(SOURCE.replace('use(i);','BUMP(i);'), 'f','#define BUMP(x) (++x)','mips')


def test_counter_staging_rejects_entry_bypassing_new_definition():
    source='void f(int x) { unsigned int i; i=0; if(x) goto middle; rows: { i++; middle: use(i); } if(i<4) goto rows; }'
    assert not loop_storage_shapes.variants(source,'f',arch='mips')

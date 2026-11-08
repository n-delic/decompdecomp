from decompnet.app import loop_storage_shapes, repair_localisation

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


def test_fresh_lines_rank_relevant_counter_before_unrelated_loops():
    source='void f(int x) {\n unsigned int i;\n'+('do { use(x); } while(x);\n'*5)+'\n\n i=0;\n rows: {\n i++;\n use(i);\n }\n if(i<4) goto rows;\n}'
    broad=loop_storage_shapes.variants(source,'f',arch='mips')
    line=source[:source.index('use(i)')].count('\n')+1
    evidence=repair_localisation.bind(source,{'available':True,'lines':[{'line':line}]})
    ranked=loop_storage_shapes.variants(source,'f',arch='mips',locations=evidence)
    assert broad[0][0]=='explicit_loop_backedge'
    assert any(family=='row_number_staging' for family,_ in broad)
    assert ranked[0][0]=='row_number_staging'
    assert any(family=='explicit_loop_backedge' for family,_ in ranked)
    assert loop_storage_shapes.variants(source,'f',arch='mips',locations=evidence,mapped_only=True)==ranked[:1]
    evidence['source_sha256']='stale'
    assert loop_storage_shapes.variants(source,'f',arch='mips',locations=evidence)==broad
    assert not loop_storage_shapes.variants(source,'f',arch='mips',locations=evidence,mapped_only=True)

from decompnet.app import agent_tokens, efficiency_feedback
from decompnet.app.source_type_context import referenced
from tests.test_claims import _game


def test_late_release_saves_findings_without_touching_other_claim(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');b,_=net.local_agent('b')
    net.claim_row(a,'game','f')
    with net.store.tx() as db:
        db.execute('DELETE FROM claims WHERE agent_id=?',(a.id,))
    net.claim_row(b,'game','f')
    result=net.release_function(a,'f','The loop bound needs a signed temporary.','game',move_on=True)
    assert 'already released' in result and 'Findings saved' in result
    assert net.store.one('SELECT agent_id FROM claims')['agent_id']==b.id
    assert 'signed temporary' in net.store.one('SELECT text FROM notes')['text']
    state=efficiency_feedback.continuation(net,a,'game','f',applied=[],base='old')
    assert 'Do not patch' in state and 'add_note' in state and 'Patch this revision only' not in state


def test_reference_directives_do_not_overlap_or_leak_into_typedefs():
    source='#ifndef M2C_DEFINES\n#define M2C_DEFINES\ntypedef int M2C_UNK;\n#endif\n#define VALUE(x) \\\n ((x)+1)\nint unrelated;\n'
    got=referenced(source,'M2C_UNK f(){return VALUE(1);}','')
    assert '#ifndef' not in got and '#endif' not in got and '#define M2C_DEFINES' not in got
    assert got.count('typedef int M2C_UNK;')==1 and got.count('#define VALUE')==1
    assert ';#' not in got and 'int unrelated' not in got


def test_unchanged_feedback_omits_mapping_but_preserves_score(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    rows=[('r','move $a0, $s0','move $a0, $s1')]
    net.keep_diff(a.id,net._function('f','game')['id'],rows)
    full='score 10\nmarkers: r=register\n### Compiled C lines associated with differences\n| 12 | `return x;` | Register | expected | current |\n'
    out=agent_tokens.feedback(net,a,'game','f',full,rows)
    assert 'score 10' in out and 'repeated C-line table omitted' in out and '| 12 |' not in out


def test_rendering_difference_is_not_first_mismatch_advice(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    rows=[('i','lw $v0, 0x634($v0)','lw $v0, %lo(D_800D0634)($v0)'),('r','move $a0, $s0','move $a0, $s1')]
    net.keep_diff(a.id,net._function('f','game')['id'],rows)
    full='score 10\nThe first 0 of 2 target instructions match; the first difference (operand): old\nmarkers: i=operand'
    out=agent_tokens.feedback(net,a,'game','f',full,None)
    assert '1 relocation-rendering differences omitted' in out and 'first differing row 1' in out
    assert 'first difference (operand)' not in out and '%lo' not in out


def test_explicit_assembly_page_has_exact_next_cursor(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    monkeypatch.setattr(net,'_asm_text',lambda *args:'\n'.join('nop '+str(i) for i in range(80)))
    out=agent_tokens.assembly(net,'game','f',start=0,limit=20,focus='page',agent=a)
    assert 'lines 0:20 of 80' in out and 'start=20, limit=20, focus="page"' in out
    out=agent_tokens.assembly(net,'game','f',start=60,limit=20,focus='page',agent=a)
    assert 'End of target assembly.' in out


def test_mapping_labels_proven_rendering_pairs():
    from decompnet.source_lines import feedback_note
    row=('i','lw $v0, 0x634($v0)','lw $v0, %lo(D_800D0634)($v0)')
    note='### Compiled C lines associated with differences\n| 3 | `return x;` | Operand | `'+row[1]+'` | `'+row[2]+'` |'
    assert '| Rendering only |' in feedback_note(note,[row])
    assert '| Operand |' in feedback_note(note)

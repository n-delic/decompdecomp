"""The near-match guide's compiler rules and method, and the brief pointing at the section a near-match's last diff
calls for (helpers read the guide top to bottom and missed the rule that fit their rows; hand sessions with the
match-function skill closed the same rows with these rules)."""
from decompnet import network
from decompnet.app import tools


def test_the_guide_carries_the_rules_with_their_evidence_and_the_method():
    text = tools.guide_text('near-match', 'n64')
    for rule in ('birthing_insn_p', 'true_dependence', 'qty_compare', 'find_reg', 'move_movables', 'combine_movables',
                 'REG_EQUIV', 'dead pseudo', 'do { } while (0)` adds loop-depth weight'):
        assert rule in text, rule
    for section in ('Register-only rows', 'Order rows', 'Frame-size rows', 'Remove inherited workarounds',
                    '-fno-strength-reduce', 'find_similar', 'Faithful programs only', 'Expect a combination'):
        assert section in text, section
    for tool in ('search_learnings', 'prove_chain', 'compiler_source', 'find_similar', 'score_permutations'):
        assert tool in text and tool in tools.HANDLERS, tool
    assert '*verified' in text and '*observation' in text and '*controlled' in text
    # GCC 2.x pass rules are MIPS-only: a PC helper's guide leaves them out
    assert 'birthing_insn_p' not in tools.guide_text('near-match', 'x86')
    kmc = tools.guide_text('gcc-kmc', 'n64')
    assert 'Last rows: which pass, by symptom' in kmc and '-fno-strength-reduce' in kmc


def test_the_brief_points_a_near_match_at_the_section_its_rows_need():
    fn = {'n_insns': 40, 'best_score': 20}
    regs = [('r', 'move $s0,$a0', 'move $s1,$a0'), (' ', 'jr $ra', 'jr $ra')]
    order = [('o', 'sw $v0,0($a0)', 'lw $v1,4($a0)'), ('r', 'move $s0,$a0', 'move $s1,$a0')]
    frame = [('|', 'addiu $sp,$sp,-40', 'addiu $sp,$sp,-48'), ('r', 'move $s0,$a0', 'move $s1,$a0')]
    assert '"Register-only rows"' in network.move_on_brief(fn, rows=regs)
    assert '"Order rows"' in network.move_on_brief(fn, rows=order)
    assert '"Frame-size rows"' in network.move_on_brief(fn, rows=frame)
    assert 'rows"' not in network.move_on_brief(fn)
    # not a near-match: no section pointer, but the twin hint stays
    far = network.move_on_brief({'n_insns': 40, 'best_score': 900}, rows=regs)
    assert 'rows"' not in far and 'find_similar(function)' in far

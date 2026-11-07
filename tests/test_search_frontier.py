from decompnet.app import permuter_focus, repair_ranking, search_frontier


def test_numeric_and_small_position_residual_survive_crowded_frontier():
    frontier=search_frontier.Frontier()
    target=['move $s0, $a0']*20
    def rows(count):
        return [('r' if i<count else ' ',op,'move $s1, $a0' if i<count else op) for i,op in enumerate(target)]
    baseline=rows(20)
    frontier.offer('numeric',315,rows(18),arch='mips',baseline=baseline,baseline_score=565)
    frontier.offer('few-positions',330,rows(6),arch='mips',baseline=baseline,baseline_score=565)
    for i in range(12):
        frontier.offer('sibling'+str(i),340+i,rows(8+i),arch='mips',baseline=baseline,baseline_score=565)
    assert {'numeric','few-positions'} <= {n['source'] for n in frontier.nodes}
    assert len(frontier.nodes)<=search_frontier.WIDTH


def test_known_relocation_rendering_does_not_hide_register_misses():
    rows = [('i','lw $a0, -0x7ce0($a0)','lw $a0, %lo(D_800C8320)($a0)'),
            ('r','andi $s0, $s0, 0xffff','andi $v1, $s0, 0xffff')]
    assert permuter_focus.classify(rows) == 'register'
    assert rows[0][0] == 'i'  # the real diff and numeric score remain untouched
    assert repair_ranking.order(['constant_local','masked_value_reuse'],rows)[0] == 'masked_value_reuse'
    assert search_frontier.structure(rows) == 1


def test_unknown_symbols_wrong_bits_registers_addends_and_hi_values_remain_mismatches():
    for target, candidate in [
        ('lw $a0, -0x7ce4($a0)','lw $a0, %lo(D_800C8320)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a1, %lo(D_800C8320)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a0, %lo(renamed)($a0)'),
        ('lw $a0, -0x7ce0($a0)','lw $a0, %lo(D_800C8320+4)($a0)'),
        ('lui $a0, 0x800c','lui $a0, %hi(D_800C8320)'),
    ]:
        assert permuter_focus.normalized([('i',target,candidate)])[0][0] == 'i'
    assert permuter_focus.normalized([('i','lui $a0, 0x800d','lui $a0, %hi(D_800C8320)')])[0][0] == ' '
    renamed=[('i','lw $a0, -0x7ce0($a0)','lw $a0, %lo(renamed)($a0)')]
    assert permuter_focus.normalized(renamed,lambda name:0x800c8320)[0][0] == ' '


def test_frontier_keeps_numeric_best_and_distinct_structural_alternatives_with_bounded_memory():
    frontier = search_frontier.Frontier()
    frontier.offer('numeric', 10, [('r','r1','r2')]*7)
    frontier.offer('moved', 15, [('o','lw','')])
    frontier.offer('register', 12, [('r','s0','v1')]*2)
    for i in range(30):
        frontier.offer('bad'+str(i), 200+i, [('|','target','candidate'+str(i))]*30)
    assert len(frontier.nodes) <= search_frontier.WIDTH
    assert {'numeric','moved','register'} <= {n['source'] for n in frontier.nodes}
    assert frontier.choose(1,'fallback') != 'fallback'


def test_frame_and_code_growth_prune_only_unhelpful_expansion():
    baseline = [(' ','addiu $sp, $sp, -32','addiu $sp, $sp, -32'),
                ('r','move $s0, $a0','move $s1, $a0')]
    larger = [('s','addiu $sp, $sp, -32','addiu $sp, $sp, -40'),
              ('r','move $s0, $a0','move $s1, $a0'), ('>','','nop')]
    assert search_frontier.unhelpful_detour('mips',baseline,larger)
    assert not search_frontier.unhelpful_detour('mips',[],larger)
    assert not search_frontier.unhelpful_detour('ppc',baseline,larger)
    assert not search_frontier.unhelpful_detour('mips',baseline,larger[:-1])
    frontier = search_frontier.Frontier()
    frontier.offer('helper',12,larger,arch='mips',baseline=baseline)
    assert not frontier.nodes
    # Removing a register miss can justify exploring a new frame/code shape.
    mostly_registers = baseline+[('r','move $s2, $a1','move $s3, $a1')]*8
    assert not search_frontier.unhelpful_detour('mips',mostly_registers,larger)


def test_numeric_improvements_still_enter_the_frontier():
    baseline = [(' ','addiu $sp, $sp, -32','addiu $sp, $sp, -32'),
                ('r','move $s0, $a0','move $s1, $a0')]
    larger = [('s','addiu $sp, $sp, -32','addiu $sp, $sp, -40'),
              ('r','move $s0, $a0','move $s1, $a0'), ('>','','nop')]
    frontier = search_frontier.Frontier()
    frontier.offer('improved',9,larger,arch='mips',baseline=baseline,baseline_score=10)
    assert frontier.nodes[0]['source'] == 'improved'


def test_register_alternative_beyond_numeric_cutoff_remains_explorable():
    frontier=search_frontier.Frontier()
    rows=[('r','move $s1, $a0','move $s2, $a0')]
    frontier.offer('saved',275,rows)
    alternate=[('r','move $s1, $a0','move $s3, $a0')]
    frontier.offer('lifetime-step',390,alternate,baseline=rows,baseline_score=275)
    assert {'saved','lifetime-step'} <= {n['source'] for n in frontier.nodes}
    assert any(frontier.choose(i,'saved')=='lifetime-step' for i in range(search_frontier.WIDTH))

from types import SimpleNamespace

from decompnet.app import expression_locals, mmio_repairs, repair_ranking


def test_mmio_volatile_lvalue_only_and_side_effects_preserved():
    source = 'extern vu32 D_A4800000; int f(){D_A4800000=physical(p); return 0;}'
    out = mmio_repairs.variants(source,'f','mips')
    assert len(out)==1 and '*(volatile unsigned int *)0xA4800000=physical(p)' in out[0]
    assert out[0].count('physical(p)')==1
    assert not mmio_repairs.variants(source,'f','x86')
    assert not mmio_repairs.variants(source.replace('vu32','u32'),'f','mips')
    assert not mmio_repairs.variants(source.replace('A4800000','80000000'),'f','mips')
    assert not mmio_repairs.variants(source.replace('D_A4800000=physical(p)','x=D_A4800000'),'f','mips')


def test_ranking_is_stable_and_never_drops_families():
    families=['signedness','constant_local','integer_address_order','comparison_reuse']
    assert repair_ranking.order(families,[]  )==families
    ranked=repair_ranking.order(families,[('r','a','b')])
    assert ranked[:2]==['comparison_reuse','integer_address_order']
    assert set(ranked)==set(families)
    net=SimpleNamespace(_last_diffs={(7,2):([('r','a','b')],'')})
    assert repair_ranking.evidence(net,2,9)==[('r','a','b')]
    assert repair_ranking.evidence(net,3,9)==[]


def test_c89_initializer_does_not_get_an_assignment_before_its_declaration():
    source='void f(){s32 value=*(s32 *)(base+4); send(value);}'
    out=expression_locals.variants(source,'f')
    assert not any('address_temporary'==f for f,_ in out)
    assert expression_locals.declaration_prefix(' const s32 *value = ')
    assert expression_locals.declaration_prefix(' s32*value = ')
    assert not expression_locals.declaration_prefix(' value = ')
    macro='void f(){s32 value=M2C_FIELD(base+4,s32 *,0);send(value);}'
    out=expression_locals.variants(macro,'f')
    assert out and all('s32 value=M2C_FIELD((__repair_addr =' in text for family,text in out if family=='address_temporary')


def test_current_compiler_uses_function_range_flags(tmp_path,monkeypatch):
    import json

    from decompnet.app import agent_tokens, compiler_experiments
    from tests.test_claims import _game
    net,pid=_game(tmp_path,[('f',60,'open')]);agent,_=net.local_agent('a')
    source='int f(){return 1;}'
    cfg={'compiler':{'toolchain':'base','flags':['-O1']},'compiler_ranges':[{'start':0,'end':100,'toolchain':'range','flags':['-O2','-g3']}]}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')",(pid,))
        db.execute('UPDATE functions SET vram=4,best_source=?,best_score=5,asm=?',(source,'\n'.join('line'+str(i) for i in range(80))))
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f');seen=[]
    monkeypatch.setattr(net,'_toolchain_named',lambda cfg,c:c)
    monkeypatch.setattr(net,'try_compiler',lambda a,s,f,c,flags,source,keep_match:seen.append((c,flags,keep_match)) or 'score 1')
    compiler_experiments.run(net,agent,'game','f',revision,[{'compiler':'current'}])
    assert seen==[('range','-O2 -g3',False)]
    fn=net._function('f','game')
    monkeypatch.setattr(net,'_extra_brief',lambda *a:['EXTRA DETAILS'])
    brief=net._brief(fn,agent,'CLAIMED')
    assert source in brief and 'line79' not in brief and 'EXTRA DETAILS' not in brief
    assert 'compiler ID: range' in brief
    full=net._brief(fn,agent,'FUNCTION')
    assert 'line79' in full and 'EXTRA DETAILS' in full

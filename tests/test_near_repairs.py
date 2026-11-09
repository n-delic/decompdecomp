from decompnet.app.near_repairs import addresses, variants


def asm(*words):
    return '\n'.join(f'/* {i*4:X} {0x80001000+i*4:08X} {word:08X} */ misleading %hi(D_80020000)' for i,word in enumerate(words))


def test_raw_address_signed_low_and_clobbers():
    assert addresses(asm(0x3c028001, 0x8c438010)) == {0x80008010}
    assert not addresses(asm(0x3c028001, 0x00401021, 0x8c438010))
    assert not addresses(asm(0x3c028001, 0x0c001000, 0x8c438010))


def test_raw_literal_and_alias_preserve_declarations_and_opaque_text():
    raw = asm(0x3c028001, 0x8c430010)
    src = 'extern char D_80010011[];\nvoid f(void) { use(D_80010011); /* 0x80020010 */ use("D_80010011"); }'
    out = variants(src, 'f', 'mips', raw)
    assert any('use((D_80010011-1))' in v for v in out)
    assert all(v.startswith('extern char D_80010011[];') and '/* 0x80020010 */' in v and '"D_80010011"' in v for v in out)
    assert variants('void f(void) { use(*(int *)0x80020010); }', 'f', 'mips', raw) == ['void f(void) { use(*(int *)0x80010010); }']
    assert not variants(src, 'f', 'ppc', raw)


def test_scalar_and_pointer_globals_stay_loads():
    raw = asm(0x3c028001, 0x8c430010)
    for typ in ('s32', 'u8 *'):
        out = variants(f'extern {typ} D_80020010;\nvoid f(void) {{ use(D_80020010); }}', 'f', 'mips', raw)
        assert out and '(*('+typ+' *)0x80010010)' in out[0]


def test_only_unused_nonvolatile_local_padding():
    src = 'void f(void) {\n u8 pad[8];\n work(); }'
    assert any('pad[8]' not in v for v in variants(src, 'f', 'ppc'))
    for declaration, use in [('volatile u8 pad[8];', ''), ('u8 pad[8];', 'use(pad);')]:
        assert not variants('void f(void) {\n '+declaration+'\n'+use+'}', 'f', 'mips')


def test_independent_local_stores_and_dependencies():
    src = 'void f(void) {\n Loc x;\n x.b[0] = -x.input[0];\n x.a[0] = -x.input[1];\n}'
    out = variants(src, 'f', 'arm')
    assert out and out[0].index('x.a[0] =') < out[0].index('x.b[0] =')
    assert not variants(src.replace('-x.input[1]', 'x.b[0]'), 'f', 'arm')
    assert not variants(src.replace('-x.input[1]', 'call()'), 'f', 'arm')


def test_layout_pair_and_bias_can_combine_with_address():
    src = 'typedef struct { u8 padA[0x20]; s16 value; u8 padB[4]; } Item;\nextern u8 *D_80020010;\nvoid f(void) { base=D_80020010; p=(Item *)((u8 *)base+0x40); }'
    out = variants(src, 'f', 'mips', asm(0x3c028001, 0x8c430010))
    assert any('padA[24]' in v and 'padB[12]' in v and '+0x40' not in v and '0x80010010' in v for v in out)
    assert not variants('void f(void) {\n#define X 1\nreturn; }', 'f', 'mips')


def test_debug_probe_cache_and_exact_only_persistence():
    import json
    import time

    from decompnet.app.permute import State
    from tests.test_permute import AGENT, FakeNet, ScriptedPermuter

    net = FakeNet()
    cfg = {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
        db.execute('UPDATE functions SET best_score=5, size=40')
    probes, changes = [], []
    net.debug_flag_variants = lambda *a: [['-O2'], ['-O2', '-g'], ['-O2', '-g3']]
    net._context = lambda *a: {'header': '', 'version': 1}
    net._judge_with = lambda *a: (probes.append(a[3]) or False, None, [])
    net.set_compiler_ranges = lambda *a: changes.append(a)
    perm = ScriptedPermuter(net, {})
    proj = net.project_row('game')
    fn = net.store.one('SELECT * FROM functions WHERE id=1')
    state = State()
    assert perm._repair_flags(AGENT, proj, fn, state, time.monotonic()+10) == 2
    assert probes == [['-O2', '-g'], ['-O2', '-g3']] and not changes
    assert perm._repair_flags(AGENT, proj, fn, state, time.monotonic()+10) == 0
    with net.store.tx() as db:
        db.execute('DELETE FROM reuse_trials')
    net._judge_with = lambda *a: (True, None, [])
    perm._score = lambda *a: 0
    assert perm._repair_flags(AGENT, proj, fn, state, time.monotonic()+10) == 2
    assert changes[0][1] == [[fn['vram'], fn['vram']+40]]
    assert state.matched == 1


def test_ambiguous_raw_addresses_do_not_guess():
    raw = asm(0x3c028001, 0x8c430010, 0x8c440012)
    assert not variants('void f(void) { use(*(int *)0x80010011); }', 'f', 'mips', raw)


def test_other_function_and_packed_layout_are_left_alone():
    src = 'void other(void) { use(*(int *)0x80020010); }\nvoid f(void) { return; }'
    assert not variants(src, 'f', 'mips', asm(0x3c028001, 0x8c430010))
    src = 'typedef struct __attribute__((packed)) { u8 padA[32]; s16 value; u8 padB[4]; } Item;\nvoid f(void) { return; }'
    assert not variants(src, 'f', 'mips')


def test_near_flag_search_rotates_cached_debug_probes_to_optimization_flags():
    import time

    from decompnet.app.permute import Permuter, State

    from .test_permute import AGENT
    net = _flag_probe_net(); proj = net.project_row('game'); fn = net.function()
    net.debug_flag_variants = lambda tc, flags: [flags, ['-O2','-g'], ['-O2','-g3']]
    net.flag_variants = lambda tc, flags: [flags, ['-O1'], ['-O3']]
    tested=[]; changes=[]
    def judge(proj, fn, tc, flags, source):
        tested.append(flags)
        return flags == ['-O3'], None, ''
    net._judge_with=judge
    net.set_compiler_ranges=lambda *args:changes.append(args)
    perm=Permuter(net); perm._score=lambda *args:0
    state=State()
    assert perm._repair_flags(AGENT,proj,fn,state,time.monotonic()+10)==2
    assert not changes
    assert perm._repair_flags(AGENT,proj,fn,state,time.monotonic()+10)==3
    assert tested==[['-O2','-g'],['-O2','-g3'],['-O1'],['-O3']]
    assert changes[0][-1]==['-O3'] and state.matched==1


def test_near_flag_match_respects_manual_compiler_approval():
    import time

    from decompnet.app.permute import Permuter, State

    from .test_permute import AGENT
    net=_flag_probe_net(); proj=net.project_row('game'); fn=net.function()
    net.debug_flag_variants=lambda tc, flags:[flags,['-O2','-g']]
    net._judge_with=lambda *args:(True,None,'')
    net.auto_adjusts=lambda proj:False
    suggestions=[]
    net.suggest=lambda *args,**kwargs:suggestions.append(kwargs)
    net.set_compiler_ranges=lambda *args:(_ for _ in ()).throw(AssertionError('must ask the user'))
    state=State()
    assert Permuter(net)._repair_flags(AGENT,proj,fn,state,time.monotonic()+10)==1
    assert suggestions[0]['function']==fn['name'] and suggestions[0]['source']==fn['best_source']
    assert state.matched==0


def _flag_probe_net():
    import json

    from .test_permute import FakeNet
    net=FakeNet()
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}),))
        db.execute('UPDATE functions SET best_score=5,size=40')
    return net

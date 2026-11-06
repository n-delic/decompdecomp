from decompnet.app.near_match_repairs import variants


def test_sum_order_proposals_are_bounded_and_do_not_duplicate_evaluations():
    source = 'void f(float *p, float x) { p[0] = (x * 2.0f) + p[0]; }'
    result = variants(source, 'f')
    assert any(f == 'arithmetic_operand_order' and '(p[0]) + ((x * 2.0f))' in text for f,text in result)
    assert not variants('void f(float *p) { p[0] = get() + p[0]; }', 'f')
    assert not variants('void f(volatile float *p) { p[0] = p[1] + p[0]; }', 'f')


def test_forward_missing_call_arguments_requires_target_evidence_and_updates_prototype():
    source = 'void callee(int x, int y); void f(void *arg0, void *arg1) { callee(4, 20); }'
    assert not variants(source,'f')
    result = variants(source,'f','addiu $a2, $zero, 4\nlw $a3, 20($a0)\njal callee\nnop')
    assert any('callee(arg0, arg1, 4, 20)' in s and 'callee(void *arg0, void *arg1, int x, int y)' in s for _,s in result)
    assert len(result) <= 4


def test_operand_repair_recovers_two_independent_compiler_controls(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    tid = 'gcc-2.7.2-kmc'
    if not toolchains.installed(tid):
        pytest.skip('needs the local pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler':{'toolchain':tid,'flags':['-O2']}})
    for name, index, expression in [('f',0,'x * 2.0f'),('g',2,'p[1] * x')]:
        before = f'void {name}(float *p,float x) {{ p[{index}] = ({expression}) + p[{index}]; }}'
        after = f'void {name}(float *p,float x) {{ p[{index}] = p[{index}] + ({expression}); }}'
        control = compile_source(cfg,'',after)
        assert control.ok, control.log
        target = extract_function(control.obj,name).code
        recovered = False
        for family, candidate in variants(before,name):
            if family != 'arithmetic_operand_order': continue
            result = compile_source(cfg,'',candidate)
            if result.ok and extract_function(result.obj,name).code == target:
                recovered = True
        assert recovered  # Regression against a compiler-output control, never a game-match claim.


def test_forwarded_arguments_recover_two_independent_compiler_controls(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    tid = 'gcc-2.7.2-kmc'
    if not toolchains.installed(tid):
        pytest.skip('needs the local pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler':{'toolchain':tid,'flags':['-O2']}})
    for name, value in [('first',4),('second',7)]:
        before = f'void callee(int x,int y); void {name}(void *arg0,void *arg1){{callee({value},20);}}'
        after = f'void callee(void*,void*,int,int); void {name}(void *arg0,void *arg1){{callee(arg0,arg1,{value},20);}}'
        control = compile_source(cfg,'',after)
        assert control.ok, control.log
        target = extract_function(control.obj,name).code
        recovered = False
        assembly = 'addiu $a2, $zero, 4\naddiu $a3, $zero, 20\njal callee\nnop'
        for family, candidate in variants(before,name,assembly):
            if family != 'call_forwarded_arguments': continue
            result = compile_source(cfg,'',candidate)
            if result.ok and extract_function(result.obj,name).code == target:
                recovered = True
        assert recovered


def test_forwarding_zero_argument_calls_has_no_empty_argument_or_void_tail():
    for proto in ('void callee(void);', 'void callee();'):
        source = proto+' void f(int arg0){callee();}'
        proposals = [s for family,s in variants(source,'f','move $a0, $s0\njal callee\nnop')
                     if family == 'call_forwarded_arguments']
        assert proposals
        assert all('callee(arg0)' in s and 'callee(int arg0)' in s for s in proposals)
        assert not any(', )' in s or ', void' in s for s in proposals)

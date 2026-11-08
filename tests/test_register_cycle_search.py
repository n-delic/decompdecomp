import pytest

from decompnet.app import identity_lifetime_repairs

HEADER = 'typedef unsigned int u32; typedef int s32; typedef signed short s16;'


SOURCE = '''void f(s32 ctx,s32 x,s32 y){u32 z;put(ctx,x,y);
z=(u32)x^(u32)ctx;z^=(u32)ctx;z^=(u32)x;ctx=(u32)ctx+(u32)(s16)y*z;
put(ctx,x,y);put(ctx,x,y);}'''


ROWS = [(' ', 'addiu $sp, $sp, -0x40', 'addiu $sp, $sp, -0x40'),
        ('r','addiu $s4, $s7, -1','addiu $s5, $s7, -1'),
        ('r','sra $s5, $v1, 16','sra $s4, $v1, 16')]


def test_identity_relocations_keep_values_and_do_not_invent_reads():
    candidates = identity_lifetime_repairs.variants(SOURCE,'f',HEADER,'mips')
    assert candidates and len(candidates)<=identity_lifetime_repairs.LIMIT
    assert all(c.count('put(ctx,x,y);')==3 and c.count('ctx=(u32)ctx+')==1 for _,c in candidates)
    assert candidates[0][1].rfind('ctx=(u32)ctx+')>candidates[0][1].rfind('put(ctx,x,y);')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER,'ppc64')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER.replace('unsigned int u32','unsigned short u32'),'mips')
    assert not identity_lifetime_repairs.variants(SOURCE,'f',HEADER+'\n#define s16 volatile short','mips')
    for changed in [SOURCE.replace('ctx=(u32)ctx+', 'ctx=(u32)other+'),
                    SOURCE.replace('z^=(u32)x','z^=(u32)y'),
                    SOURCE.replace('u32 z;','volatile u32 z;'),
                    SOURCE.replace('put(ctx,x,y);}', 'escape(&ctx);put(ctx,x,y);}'),
                    SOURCE.replace('put(ctx,x,y);}', 'use(z);put(ctx,x,y);}'),
                    SOURCE.replace('u32 z;','u32 z; s32 x;')]:
        assert not identity_lifetime_repairs.variants(changed,'f',HEADER,'mips')


def test_relocation_requires_dominating_initialization_and_stays_in_branch():
    source = SOURCE.replace('s32 x,s32 y','s32 y').replace('u32 z;','u32 z; s32 x; if(y)x=7;')
    assert not identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    source = SOURCE.replace('s32 x,s32 y','s32 y').replace('u32 z;','u32 z; s32 x; x=7;')
    assert identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    conditional = SOURCE.replace('z=(u32)', 'if(y){z=(u32)').replace('put(ctx,x,y);}', 'put(ctx,x,y);}put(ctx,x,y);}')
    assert all(c.rfind('ctx=(u32)ctx+')<c.rfind('}put(ctx,x,y);')
               for _,c in identity_lifetime_repairs.variants(conditional,'f',HEADER,'mips'))


def test_relocation_on_host_preserves_function_results(tmp_path):
    import shutil
    import subprocess
    cc=shutil.which('cc')
    if not cc:pytest.skip('host C compiler unavailable')
    source=SOURCE.replace('void f(', 's32 f(').replace('put(ctx,x,y);}', 'put(ctx,x,y);return ctx;}')
    candidates=identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    assert candidates
    harness=HEADER+'\nvoid put(s32 a,s32 b,s32 c){(void)a;(void)b;(void)c;}\n'
    harness+='\nint main(void){int i;for(i=-32000;i<32000;i+=137)if(f(i,i/2,-i)!=i)return 1;return 0;}'
    for i,candidate in enumerate([source,candidates[0][1]]):
        path=tmp_path/f'c{i}.c';out=tmp_path/f'c{i}';path.write_text(harness.split('\nint main')[0]+candidate+'\nint main'+harness.split('\nint main')[1])
        subprocess.run([cc,'-O2','-Wall','-Werror',str(path),'-o',str(out)],check=True,capture_output=True)
        subprocess.run([str(out)],check=True)


def test_identity_placement_never_crosses_declaration_or_unbraced_control():
    source=SOURCE.replace('u32 z;put(ctx,x,y);','put(ctx,x,y);u32 z;')
    for _,candidate in identity_lifetime_repairs.variants(source,'f',HEADER,'mips'):
        assert candidate.index('u32 z;') < candidate.index('z=(u32)')
    source=SOURCE.replace('put(ctx,x,y);put(ctx,x,y);}', 'if(y)put(ctx,x,y);else put(ctx,x,y);}')
    candidates=identity_lifetime_repairs.variants(source,'f',HEADER,'mips')
    assert all('if(y)put(ctx,x,y);else put(ctx,x,y);' in c for _,c in candidates)

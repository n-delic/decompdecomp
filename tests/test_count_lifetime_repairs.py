import shutil
import subprocess

import pytest

from decompnet.app import count_lifetime_repairs as repair
from decompnet.app import draft_repairs, repair_ranking
from decompnet.diff_summary import summarize

HEADER = 'typedef unsigned short u16; typedef unsigned int u32; typedef int s32;'
SOURCE = '''
typedef struct Road {u16 count;} Road;
void probe(void);
int run(Road *p,int late) {
 s32 count; s32 index;
 count=777;
 if(p) {
 count=p->count;
 index=1;
 goto test;
 body: index++; count=p->count;
 test: count=(u32)count>>1;
 if(index<count){goto body;}
 }
 if(late){count=42;}
 return count;
}
'''


def choices(source=SOURCE):
    return repair.variants(source,'run','mips',HEADER)


def test_count_bundle_preserves_original_value_outside_the_phase_and_is_bounded():
    generated=choices()
    assert 1<=len(generated)<=8
    assert {'count_phase_lifetime','count_load_qualification','count_lifetime_bundle'} <= {x for x,_ in generated}
    assert all('count=__repair_phase_count;'in s for _,s in generated)
    bundle=[s for family,s in generated if family=='count_lifetime_bundle']
    assert any('short __repair_guard_y' in s and '__repair_narrow_contract'in s for s in bundle)
    labels={}
    assert any('count_lifetime_bundle'==labels[s] for s in draft_repairs.variants(SOURCE,'run','mips',header=HEADER,labels=labels,limit=None))


@pytest.mark.parametrize('source',[
    SOURCE.replace('u16 count','s32 count'),
    SOURCE.replace('s32 count;', 'u32 count;'),
    SOURCE.replace('body: index++;', 'body: note(sizeof(count));index++;'),
    SOURCE.replace('body: index++; count=p->count;','body: index++; count=p->count+1;'),
    SOURCE.replace('body: index++;','body: return count; index++;'),
    SOURCE.replace('goto body;','goto elsewhere;'),
    SOURCE.replace('count=777;','count=777; escape(&count);'),
    SOURCE.replace('count=777;','count=777;if(late)goto test;'),
    SOURCE.replace('count=p->count;','if(late){count=p->count;}',1),
])
def test_unknown_bounds_escapes_and_external_exits_have_no_phase_repair(source):
    assert not any(f.startswith('count_')for f,_ in choices(source))


def test_variable_shift_requires_one_unescaped_literal_assignment():
    source=SOURCE.replace('s32 count;','s32 shift; s32 count;').replace('count=777;','shift=1;count=777;').replace('>>1','>>shift')
    assert choices(source)
    assert not choices(source.replace('shift=1','shift=2'))
    assert not choices(source.replace('index=1;','index=1; shift++;'))


def test_existing_guard_width_is_ranked_first_only_for_stack_misses():
    source='''void probe(void); void f(void){unsigned short count;
 count=32767; count=(u32)count>>1;
 {u32 x=count;u32 y=x;y+=0x13579bdfu;y-=0x13579bdfu;if(y!=x){probe();}}}'''
    variants=repair.guards(source,'f','mips',HEADER)
    assert len(variants)==1 and 'short y=x' in variants[0][1]
    assert not repair.guards(source.replace('count=(u32)count>>1;','if(flag){count=(u32)count>>1;}'),'f','mips',HEADER)
    assert not repair.guards(source.replace('count=(u32)count>>1;','if(flag)count=(u32)count>>1;'),'f','mips',HEADER)
    assert not repair.guards(source,'f','ppc',HEADER)
    assert repair_ranking.order(['stack_padding','modular_guard_width'],[('s','frame32','frame40')])[0]=='modular_guard_width'


def test_generated_shapes_execute_the_same_reads_results_and_no_guard_calls(tmp_path):
    cc=shutil.which('cc')
    if not cc:pytest.skip('host C compiler unavailable')
    harness='''
static int calls; void probe(void){calls++;}
int main(void){Road r; unsigned int n; int late;
 if(run(0,0)!=777 || run(0,1)!=42)return 1;
 for(n=0;n<=65535;n+=257){r.count=n;for(late=0;late<2;late++){
 int expected=late?42:n/2;if(run(&r,late)!=expected||calls)return 2;}}
 return 0;}
'''
    for index,(_,source)in enumerate(choices()):
        path=tmp_path/f'case{index}.c';binary=tmp_path/f'case{index}'
        path.write_text(HEADER+source+harness)
        subprocess.run([cc,'-std=gnu89','-O2',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=20)
        subprocess.run([str(binary)],check=True,timeout=10)


def test_summary_distinguishes_opcode_branch_stack_and_shifted_positions():
    rows=[('|','nop','addiu $s3, $zero, 1'),('i','j f+0x20','j f+0x18'),('s','sw $ra, 32($sp)','sw $ra, 40($sp)')]
    report=summarize(rows)
    assert report['counts']['opcode']==1 and report['counts']['branch_target']==1 and report['counts']['stack']==1
    assert report['position_differences']==3 and not report['stack_only']
    assert summarize([('s','frame32','frame40')])['stack_only']
    assert summarize([('>','','nop')])['position_differences']is None
    assert summarize([None])['counts']['unknown']==1


def test_pinned_kmc_eliminates_guard_calls_and_emits_no_extra_data(tmp_path):
    import io

    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config

    if toolchains.installed('gcc-2.7.2-kmc')is None:
        pytest.skip('Pinned KMC compiler unavailable')
    config=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[
        '-G0','-mips3','-mgp32','-mfp32','-O2','-fno-strength-reduce','-msoft-float']}})
    bundles=[s for family,s in choices()if family=='count_lifetime_bundle']
    assert len(bundles)==2
    for source in bundles:
        compiled=compile_source(config,HEADER,source)
        assert compiled.ok,compiled.log
        elf=ELFFile(io.BytesIO(compiled.obj));symbols=elf.get_section_by_name('.symtab')
        assert not any(s.name=='probe'and s['st_shndx']=='SHN_UNDEF'for s in symbols.iter_symbols())
        assert not any(s['sh_size']for s in elf.iter_sections()if s.name in {'.data','.bss','.rodata'})
        assert [s.name for s in symbols.iter_symbols()if s['st_info']['type']=='STT_FUNC'and s['st_shndx']!='SHN_UNDEF']==['run']


def test_frontier_keeps_the_candidate_with_fewer_rendered_position_changes():
    from decompnet.app.search_frontier import Frontier

    frontier=Frontier()
    # An aligned movement looks cheap numerically but changes four actual positions.
    moved=[('o','a',''),(' ','b','b'),(' ','c','c'),(' ','d','d'),('o','','a')]
    close=[('|','a','x'),(' ','b','b'),(' ','c','c'),(' ','d','d')]
    frontier.offer('numeric',10,moved)
    frontier.offer('position',400,close)
    assert any(n['source']=='position'and n['summary']['position_differences']==1 for n in frontier.nodes)


def test_macro_captures_are_rejected_and_literals_are_not_renamed():
    source=SOURCE.replace('body: index++;','body: TOUCH(); index++;')
    assert not any(f.startswith('count_')for f,_ in repair.variants(source,'run','mips',HEADER+'\n#define TOUCH() count=100'))
    source=SOURCE.replace('body: index++;','body: note("count=p->count;"); index++;')
    assert all('"count=p->count;"'in s for _,s in choices(source))

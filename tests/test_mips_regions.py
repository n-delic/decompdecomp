from decompnet.app import region_repairs as regions
from decompnet.arch.mips_regions import read

TARGET=['lw $t0, 0($a0)','addu $v0, $t0, $a1','beqz $v0, 0x18','nop',
        'jr $ra','nop','jr $ra','nop']


def test_delay_slot_blocks_edges_and_explicit_register_dependencies():
    graph=read(TARGET)
    assert graph['block_for']==[0,0,0,0,4,4,6,6]
    assert graph['successors']['0']==[4,6]
    assert graph['successors']['4']==[]
    assert (0,1,'t0') in graph['dependencies'] and (1,2,'v0') in graph['dependencies']
    assert read(['j 0x4','nop','nop']) is None
    assert read(['jalx 0x100','nop']) is None


def test_cfg_regions_are_target_aligned_despite_inserted_candidate_rows():
    before=[('r' if i in (0,1) else ' ',s,'other' if i in (0,1) else s) for i,s in enumerate(TARGET)]
    after=[(' ',s,s) for s in TARGET];after.insert(5,('>','','nop'))
    a=regions.snapshot(before,'mips');b=regions.snapshot(after,'mips')
    assert a['scheme']=='control-flow' and a['target']==b['target']
    assert regions.gain(a,b)>0 and regions.damage(a,b)>0
    assert regions.snapshot([(' ','op','op')]*16)['scheme']=='fixed'

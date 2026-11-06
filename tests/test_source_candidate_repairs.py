"""Small controlled cases for declaration, relocation and source-control recovery."""
from types import SimpleNamespace

import pytest

from decompnet import elfobj
from decompnet.app import source_branch_review as branches
from decompnet.app import source_preparation as prep
from decompnet.app import source_symbol_recovery as symbols
from decompnet.app import source_units
from decompnet.arch import mips_source_symbols as mips


def test_explicit_header_recovers_enum_objects_and_their_bound_dependencies(tmp_path):
    (tmp_path/'types.h').write_text('typedef unsigned char Byte;\n')
    original = ('#define COUNT 4\nenum Order { FIRST=0, SECOND };\n'
                'static const Byte label[COUNT] = {1,2,3,4};\n'
                'static int untouched = 123;\n'
                'int original(void)\n{\n return SECOND + label[0];\n}\n')
    (tmp_path/'game.c').write_text(original)
    cfg={'root':str(tmp_path),'header':str(tmp_path/'types.h')}
    body,header=prep.prepare(prep.Reader(cfg),cfg,('entry','original','game.c'),{'original':'entry'},'')
    assert 'enum Order { FIRST=0, SECOND };' in header
    assert 'extern const Byte label[COUNT];' in header and '#define COUNT 4' in header
    assert 'untouched' not in header and '{1,2,3,4}' not in header
    assert 'int entry(void)\n{\n return SECOND + label[0];\n}' in body


def test_mapped_include_enum_is_recovered_once_without_emitting_data(tmp_path):
    (tmp_path/'types.h').write_text('')
    (tmp_path/'extra.h').write_text('enum Flags { READY=1, BUSY=2 };\nextern int table[READY];\n')
    (tmp_path/'game.c').write_text('#include "extra.h"\nint original(void){return BUSY + table[0];}')
    cfg={'root':str(tmp_path),'header':str(tmp_path/'types.h')}
    _,header=prep.prepare(prep.Reader(cfg),cfg,('entry','original','game.c'),{},'')
    assert header.count('enum Flags') == 1 and header.count('extern int table') == 1


def test_declarations_never_invent_types_import_locals_or_duplicate_existing_objects():
    from decompnet.app.source_declarations import referenced
    reference='static int table[2]={3,7};\nvoid f(void){int local; table[0]=local;}\nint a,b;'
    assert referenced(reference,'use(table,local,a);','') == 'extern int table[2];'
    assert not referenced(reference,'use(table);','extern int table[2];')
    assert not referenced(reference,'use(table);','#define table other')


class Relocations:
    def __init__(self,entries):self.entries=entries
    def __getitem__(self,key):return {'sh_info':1,'sh_link':2}[key]
    def is_RELA(self):return False
    def iter_relocations(self):return iter(self.entries)


def object_fixture(monkeypatch, raw, entries, names=None, little=False):
    names=names or {1:('missing','SHN_UNDEF')}
    table=Relocations([{'r_offset':at,'r_info_type':kind,'r_info_sym':symbol} for at,kind,symbol in entries])
    class Symbol(dict):
        def __init__(self,name,index):super().__init__(st_shndx=index);self.name=name
    class Elf:
        elfclass=32;little_endian=little
        def __getitem__(self,key):return 'EM_MIPS'
        def get_section(self,index):
            return SimpleNamespace(data=lambda:raw) if index==1 else SimpleNamespace(get_symbol=lambda n:Symbol(*names[n]))
        def iter_sections(self):return iter([table])
    monkeypatch.setattr(mips,'ELFFile',lambda stream:Elf())
    monkeypatch.setattr(mips,'RelocationSection',Relocations)
    monkeypatch.setattr(elfobj,'_function_symbols',lambda e:{'entry':(1,0,len(raw))})
    return table


def words(*values,little=False):return b''.join(v.to_bytes(4,'little' if little else 'big') for v in values)


@pytest.mark.parametrize('little',[False,True])
def test_aligned_address_uses_signed_addend_and_relocation_table_order(monkeypatch,little):
    # Scheduled physical order differs from the relocation table's two address pairs.
    raw=words(0x3c010000,0x3c010000,0x24220008,0x24230000,little=little)
    table=object_fixture(monkeypatch,raw,[(4,5,1),(12,6,1),(0,5,1),(8,6,1)],little=little)
    target=words(0x3c018001,0x3c018001,0x24228008,0x24238000,little=little)
    got=mips.bindings(b'obj','entry',target,0x80001000,lambda name:None)
    assert got[0]['address']==0x80008000
    table.entries[0]['r_offset']=0  # duplicate relocation offsets are ambiguous
    assert not mips.bindings(b'obj','entry',target,0x80001000,lambda name:None)


def test_symbol_conflicts_opcode_changes_and_known_or_defined_symbols_are_not_guessed(monkeypatch):
    raw=words(0x0c000000,0,0x0c000000,0)
    object_fixture(monkeypatch,raw,[(0,4,1),(8,4,1)])
    assert not mips.bindings(b'obj','entry',words(0x0c000800,0,0x0c000900,0),0x80001000,lambda n:None)
    target=words(0x0c000800,0,0x0c000800,0)
    assert mips.bindings(b'obj','entry',target,0x80001000,lambda n:None)[0]['address']==0x80002000
    assert not mips.bindings(b'obj','entry',target,0x80001000,lambda n:0x80002000)
    assert not mips.bindings(b'obj','entry',target[:-4],0x80001000,lambda n:None)
    assert not mips.bindings(b'obj','entry',words(0x0c000800,1,0x0c000800,0),0x80001000,lambda n:None)
    object_fixture(monkeypatch,raw,[(0,4,1),(8,4,1)],names={1:('.rodata',1)})
    assert not mips.bindings(b'obj','entry',target,0x80001000,lambda n:None)


def test_unpaired_or_unsupported_relocations_are_not_guessed(monkeypatch):
    raw=words(0x3c010000,0x24220000)
    object_fixture(monkeypatch,raw,[(0,5,1)])
    assert not mips.bindings(b'obj','entry',words(0x3c018001,0x24220000),0x80001000,lambda n:None)
    object_fixture(monkeypatch,raw,[(0,5,1),(4,2,1)])
    assert not mips.bindings(b'obj','entry',raw,0x80001000,lambda n:None)


def test_private_alias_pair_preserves_literals_and_comments():
    source='int entry(void){ return missing(); } // missing\nchar *s="missing";'
    header='int missing(void);'
    pairs=symbols.variants(source,header,{'symbol_bindings':[{'name':'missing','address':0x80002000}]})
    assert pairs==[('cpu-aligned-symbols',source.replace('return missing()', 'return D_80002000()'),'int D_80002000(void);')]
    assert not symbols.variants(source,header,{})


def test_small_unlocated_real_helper_is_a_trial_only_and_keeps_actual_body(tmp_path):
    text='short level(int x){return x/100;}\nint original(int x){return level(x);}'
    (tmp_path/'game.c').write_text(text)
    cfg={'root':str(tmp_path),'_compiler_units':True,'_helper_units':True,'_known_functions':['entry']}
    reader=prep.Reader(cfg)
    out=source_units.variants(reader,cfg,('entry','original','game.c'),{'original':'entry'},'int entry(int x){return level(x);}','short level(int x);')
    assert any(family=='cpu-helper-source-unit' and 'short level(int x){return x/100;}' in source for family,source,header in out)
    cfg['_helper_units']=False
    assert not source_units.variants(reader,cfg,('entry','original','game.c'),{'original':'entry'},'int entry(int x){return level(x);}','')


def test_control_flow_review_locates_extra_call_condition_without_changing_source():
    source='void f(App *p) {\n if (cinema(p) || !p->health) {\n  hide(p);\n }\n}'
    before=source
    rows=[('>','','jal ?cinema'),(' ','lw $t0, 4($s0)','lw $t0, 4($s0)'),('|','beqz $t0, 0x30','bnez $v0, 0x30')]
    result=branches.review(source,rows,'mips')
    assert result[0]['source_line']==2 and 'if (cinema(p) || !p->health)' in result[0]['condition']
    assert 'No behavioral edit' in result[0]['message'] and result[0]['instructions'][1]['row']==1
    assert source==before
    assert not branches.review(source,[('i','beqz $t0, 0x30','beqz $t0, 0x40')],'mips')


def test_missing_guard_reports_adjacent_source_statement_from_debug_locations():
    source='void f(void) {\n vertices = buffer;\n draw(vertices);\n}'
    rows=[('<','bgez $t0, 0x50',''),(' ','move $a0, $s0','move $a0, $s0')]
    locations={'lines':[{'line':2,'instructions':[{'row':0}]}]}
    result=branches.review(source,rows,'mips',locations)
    assert result[0]['source_line']==2 and result[0]['condition']=='vertices = buffer;'
    assert 'retail/source revision' in result[0]['message']


def test_helper_discovery_is_independently_linked_and_unit_rechecked_with_real_ido(tmp_path):
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.app.compiler_context import acceptable
    from decompnet.compiler import compile_source
    from decompnet.rom.match import RomTarget, link
    from decompnet.rom.symbols import Symbol, SymbolTable
    if toolchains.installed('ido-5.3') is None:pytest.skip('Needs the already installed pinned IDO compiler')
    header='typedef struct {short agitation; int health;} Unit;'
    source=('short known(Unit *p) {return (short)(p->agitation/100);}\n'
            'int entry(Unit *p) {if(known(p)>3)return p->health;return known(p)+p->health;}')
    flags=['-O3','-mips2','-G','0','-non_shared','-Xcpluscomm','-Wab,-r4300_mul']
    from decompnet.project import compiler_config
    config=compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':flags}})
    target=compile_source(config,header,source);assert target.ok,target.log
    elf=ELFFile(BytesIO(target.obj));bodies=elfobj._function_symbols(elf)
    rows={}
    for name,(section,start,size) in bodies.items():
        code=elf.get_section(section).data()[start:start+size]
        rows[name]=dict(name=name,size=size,target=code,status='matched',vram=0x80001000 if name=='known' else 0x80002000)
    table=SymbolTable([Symbol(name,row['vram'],row['size'],'func') for name,row in rows.items()])
    class Store:
        def one(self,sql,pid,name):return rows.get(name)
        def all(self,sql,pid,size,code):return [r for r in rows.values() if r['size']==size and r['target']==code]
    net=SimpleNamespace(is_rom=lambda p:True,fork_slug=lambda s:None,store=Store(),symbols=lambda pid:table,
                        reader=lambda p,r:lambda a,n:None,
                        _rom_target=lambda r:RomTarget(r['name'],r['vram'],r['target'],[]))
    proj={'id':1,'slug':'sample','arch':'mips'}
    original=source.replace('known','original_helper')
    baseline=compile_source(compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':['-O2']+flags[1:]}}),header,original)
    assert baseline.ok,baseline.log
    aliases=symbols.helpers(net,proj,rows['entry'],baseline.obj)
    assert aliases=={'original_helper':'known'}
    pair=symbols.variants(original,header,{'helper_aliases':aliases})[0]
    improved=compile_source(config,pair[2],pair[1]);assert improved.ok,improved.log
    assert acceptable(net,proj,rows['entry'],improved.obj)
    assert link(improved.obj,'entry',net._rom_target(rows['entry']),table,net.reader(proj,rows['entry'])).exact
    # A wrong caller cannot borrow the independently matched helper to pass the unit guard.
    wrong=compile_source(config,header,pair[1].replace('>3','>4'));assert wrong.ok
    assert not acceptable(net,proj,rows['entry'],wrong.obj)


def test_profile_helper_retry_survives_budget_boundary_and_keeps_settings_private(monkeypatch):
    import json
    import time
    from contextlib import contextmanager

    from decompnet import toolchains
    from decompnet.app import compiler_context, prepared_evidence, profile_hypotheses, source_lead_analysis
    cached={};calls=[]
    fn={'id':1,'name':'entry','vram':0x80002000,'target':b'target','status':'open','best_score':10}
    class Store:
        def one(self,sql,*args):
            if sql.startswith('SELECT * FROM functions'):return fn
            return {'present':1} if len(args)==2 and args[1] in cached else None
        def all(self,*args):return [{'feedback':value} for value in cached.values()]
        @contextmanager
        def tx(self):yield self
        def execute(self,sql,args):cached[args[1]]=args[-1]
    proj={'id':1,'slug':'sample','arch':'mips','config':json.dumps({'compiler':{'toolchain':'ido-5.3','flags':['-O2']}})}
    class Net:
        store=Store()
        def is_rom(self,p):return True
        def project_row(self,s):return proj
        def flag_variants(self,*a):return [['-O3']]
        def symbols(self,pid):return SimpleNamespace(resolve=lambda n:None)
        def _compile(self,p,h,s,f):
            calls.append(s)
            assert json.loads(p['config'])['compiler']['flags']==['-O3']
            assert json.loads(proj['config'])['compiler']['flags']==['-O2']
            return SimpleNamespace(ok=True,obj=b'unknown' if 'original_helper' in s else b'verified',log='')
        def _evaluate(self,*a):return object(),True,[]
        def _target_obj(self,*a):return object()
    monkeypatch.setattr(toolchains,'installed',lambda *a:True)
    monkeypatch.setattr(prep,'inputs',lambda *a:('unchanged',))
    monkeypatch.setattr(prep,'settings',lambda *a:{'auto_exact':False})
    monkeypatch.setattr(source_lead_analysis,'destination',lambda *a:'scope')
    monkeypatch.setattr(source_lead_analysis,'record',lambda *a,**k:None)
    monkeypatch.setattr(profile_hypotheses,'prioritized',lambda *a:[['-O3']])
    monkeypatch.setattr(compiler_context,'acceptable',lambda n,p,f,o:o==b'verified')
    monkeypatch.setattr(symbols,'helpers',lambda n,p,f,o:{'original_helper':'known'} if o==b'unknown' else {})
    monkeypatch.setattr(prepared_evidence,'digest',lambda *a:'scope')
    monkeypatch.setattr(prep.scoring,'diff',lambda *a:SimpleNamespace(score=0,bytes_match=True,rows=[]))
    st=SimpleNamespace(stop=SimpleNamespace(is_set=lambda:False),add=lambda **k:None)
    report={'tested':0}
    prep.private_profiles(Net(),proj,fn,'entry calls original_helper','',st,report,lambda s:None,1,time.monotonic()+10)
    assert report['tested']==1 and calls==['entry calls original_helper']
    assert any(json.loads(value).get('cpu_retry') for value in cached.values())
    report={'tested':0}
    prep.private_profiles(Net(),proj,fn,'entry calls original_helper','',st,report,lambda s:None,1,time.monotonic()+10)
    assert report['tested']==1 and calls[-1]=='entry calls known'
    assert any(json.loads(value).get('exact') for value in cached.values())


def test_measured_symbol_trial_runs_next_even_when_many_initial_contexts_exist(monkeypatch):
    from decompnet.app import cpu_source_recovery as recovery
    baseline=('source','header');variants=[baseline]+[(f'other{i}','header') for i in range(8)]
    origins={pair:{'family':'cpu-private-source-unit'} for pair in variants}
    monkeypatch.setattr(recovery,'proposals',lambda *a:[('cpu-aligned-symbols','bound source','bound header')])
    recovery.extend(None,None,None,variants,origins,*baseline,{},origins[baseline])
    assert variants[1]==('bound source','bound header')
    assert origins[variants[1]]['cpu_depth']==1
    # The separate follow-up cap remains bounded, independent of initial contexts.
    origins.update({(f'trial{i}','h'):{'cpu_depth':1} for i in range(6)})
    before=variants[:]
    recovery.extend(None,None,None,variants,origins,*baseline,{},origins[baseline])
    assert variants==before


@pytest.mark.parametrize('arch,target,candidate',[('ppc','beq 0x20','bne 0x20'),('arm','beq 0x20','bne 0x20'),('x86','je 0x20','jne 0x20')])
def test_control_review_uses_shared_disassembled_rows_on_other_processors(arch,target,candidate):
    assert branches.review('void f(void){}',[('|',target,candidate)],arch)


def test_unsigned_low_instruction_cannot_supply_a_signed_address_guess(monkeypatch):
    raw=words(0x3c010000,0x34220000)
    object_fixture(monkeypatch,raw,[(0,5,1),(4,6,1)])
    assert not mips.bindings(b'obj','entry',words(0x3c018001,0x34228000),0x80001000,lambda n:None)


def test_unresolved_call_relocation_is_symbol_work_not_a_behavioral_diagnosis():
    assert not branches.review('void f(void){other();}', [('i','jal func_80001000','jal ?other')],'mips')

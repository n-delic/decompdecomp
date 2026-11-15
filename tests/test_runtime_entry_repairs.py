"""Synthetic instruction evidence guides hypotheses; the normal judge alone accepts them."""
import json
from types import SimpleNamespace

from decompnet.app import draft_repairs, entry_point_repairs, matching_preflight, profile_hypotheses, source_diagnostics
from decompnet.arch import mips_runtime
from tests.test_matching_preflight import setup

MOD='''sw $a0, 8($sp)
sw $a1, 12($sp)
sw $a2, 16($sp)
sw $a3, 20($sp)
ld $t0, 8($sp)
sd $t0, 0($sp)
ddiv $zero, $t0, $t1
mfhi $t2
bgez $t2, elsewhere
bgtz $t1, other
blez $t2, done
daddu $t0, $t2, $t1
jr $ra
nop'''
CONVERT='''cvt.l.d $f4, $f12
ctc1 $v0, $31
cfc1 $t0, $31
dmfc1 $v0, $f4
dsll32 $v1, $v0, 0
dsra32 $v1, $v1, 0
jr $ra
dsra32 $v0, $v0, 0'''
ENTRY='''lui $t4, %hi(func_80001000+0x80)
lw $t0, 0($sp)
addiu $t4, $t4, %lo(func_80001000+0x80)'''


def test_runtime_shapes_require_instruction_and_o32_evidence_not_names():
    assert mips_runtime.helper(MOD)=='signed_mod64'
    assert mips_runtime.helper(CONVERT)=='double_to_unsigned64'
    assert mips_runtime.helper(MOD.replace('sw $a3','sw $t3')) is None
    assert mips_runtime.helper(CONVERT.replace('dsra32 $v1','dsra32 $t1')) is None
    assert mips_runtime.helper(MOD+'\njal somewhere') is None
    assert mips_runtime.helper('glabel __ll_mod\njr $ra\nnop') is None
    assert 'long long a' in mips_runtime.candidate(MOD,'f')
    assert 'unsigned long long' in mips_runtime.candidate(CONVERT,'f')
    assert mips_runtime.candidate(MOD,'f;bad') is None


def test_profile_hypotheses_retain_other_flags_and_remove_conflicting_abi():
    own={'toolchain':'ido-5.3','flags':['-O2','-mips2','-n32','-G','0','-non_shared','-Wab,-r4300_mul']}
    p=profile_hypotheses.prioritized({'arch':'mips'},{'asm':MOD},own)
    assert p==[['-O1','-mips3','-32','-G','0','-non_shared']]
    assert not profile_hypotheses.prioritized({'arch':'ppc'},{'asm':MOD},own)
    assert not profile_hypotheses.prioritized({'arch':'mips'},{'name':'__ll_mod'},own)
    own['toolchain']='gcc-example'
    assert not profile_hypotheses.prioritized({'arch':'mips'},{'asm':MOD},own)


def test_sdk_priority_requires_signature_provenance():
    own={'toolchain':'ido-5.3','flags':['-O2','-mips2']}
    fn={'name':'osSendMesg','meta':json.dumps({'naming':{'method':'rename'}})}
    assert not profile_hypotheses.prioritized({'arch':'mips'},fn,own)
    fn['meta']=json.dumps({'naming':{'method':'sdk_signature','fingerprint':'known'}})
    assert profile_hypotheses.prioritized({'arch':'mips'},fn,own)==[['-O1','-mips2']]


def test_runtime_source_and_profile_probes_share_budget_and_suggest_without_bypassing_gate(monkeypatch):
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda _: {'pinned':True})
    net,proj,fn,state=setup();proj['arch']='mips';fn['asm']=MOD
    net.cfg=lambda _: {'compiler':{'toolchain':'ido-5.3','flags':['-O2','-mips2']}}
    def judge(p,f,t,flags,source):
        state['calls'].append((flags,source))
        exact='long long a' in source and '-mips3' in flags
        return exact,SimpleNamespace(score=0 if exact else 50),''
    net._judge_with=judge;state['auto']=True
    r=matching_preflight.run(net,proj,fn,max_profiles=2)
    assert r['tested']==2 and r['best']['exact']
    assert not state['applied'] and len(state['suggested'])==1
    assert 'long long a' in state['suggested'][0]['source']
    pending = matching_preflight.run(net,proj,fn,max_profiles=2)
    while pending.get('pending'):
        pending = matching_preflight.run(net,proj,fn,max_profiles=2)
    assert matching_preflight.run(net,proj,fn,max_profiles=2)['cached']
    assert len(state['calls'])==2


def test_entry_repair_requires_agreeing_hi_lo_and_byte_stride():
    source='void base(void);\nvoid f(void){sink((u8*)func_80001000+0x80);}\n'
    got=entry_point_repairs.variants(source,'f',ENTRY)
    assert len(got)==1 and '(u8*)func_80001080' in got[0]
    assert 'extern void func_80001080(void);' in got[0]
    assert not entry_point_repairs.variants(source,'f',ENTRY.replace('%lo(func_80001000+0x80)','%lo(func_80001000+0x84)'))
    assert not entry_point_repairs.variants(source.replace('u8*','u32*'),'f',ENTRY)
    assert not entry_point_repairs.variants(source,'f',ENTRY.replace('lw $t0','lw $t4'))
    assert not entry_point_repairs.variants(source,'f',ENTRY.splitlines()[0])
    labels={};assert got[0] in draft_repairs.variants(source,'f','mips',ENTRY,labels=labels,limit=None)
    assert labels[got[0]]=='interior_entry_symbol'


def test_entry_repair_keeps_other_bodies_comments_and_strings_opaque():
    source='void other(void){sink((u8*)func_80001000+0x80);}\nvoid f(void){sink("(u8*)func_80001000+0x80");}\n'
    assert not entry_point_repairs.variants(source,'f',ENTRY)
    macro='#define CLEANUP ((u8*)func_80001000+0x80)\nvoid f(void){sink(CLEANUP);}\n'
    assert entry_point_repairs.variants(macro,'f',ENTRY)
    assert not entry_point_repairs.variants(macro.replace('sink(CLEANUP)','sink(0)'),'f',ENTRY)


def test_specialized_call_hint_is_advisory_and_checks_clobbers_and_delay_slots():
    asm='move $t2, $a0\njal helper\nnop\nsw $v0, 0($t2)\njr $ra\nnop'
    assert mips_runtime.specialized_calls(asm)
    assert not mips_runtime.specialized_calls(asm.replace('nop\nsw','move $t2, $a0\nsw'))
    assert not mips_runtime.specialized_calls(asm.replace('sw $v0','lw $t2, 0($sp)\nsw $v0'))
    hints=source_diagnostics.hints('int f(void){return 0;}','f',asm,'mips')
    assert any('specialized calling convention' in h for h in hints)
    assert not source_diagnostics.hints('int f(void){return 0;}','f',asm,'x86')


def test_correct_runtime_profile_still_checks_a_wrong_width_draft(monkeypatch):
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda _: {'pinned':True})
    net,proj,fn,state=setup();proj['arch']='mips';fn['asm']=CONVERT
    flags=['-O1','-mips3','-32']
    net.cfg=lambda _: {'compiler':{'toolchain':'ido-5.3','flags':flags}}
    seen=[]
    def judge(p,f,t,flags,source):
        seen.append(source);exact='unsigned long long' in source
        return exact,SimpleNamespace(score=0 if exact else 50),''
    net._judge_with=judge
    r=matching_preflight.run(net,proj,fn,max_profiles=2)
    assert r['tested']==2 and r['best']['exact'] and len(seen)==2
    assert state['suggested'][0]['flags']=='-O1 -mips3 -32'


def test_changed_runtime_target_and_source_require_fresh_judgments(monkeypatch):
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda _: {'pinned':True})
    net,proj,fn,state=setup();proj['arch']='mips';fn['asm']=MOD
    net.cfg=lambda _: {'compiler':{'toolchain':'ido-5.3','flags':['-O2']}}
    net._judge_with=lambda *a:(False,SimpleNamespace(score=50),'')
    assert matching_preflight.run(net,proj,fn,max_profiles=2)['tested']==2
    pending = matching_preflight.run(net,proj,fn,max_profiles=2)
    while pending.get('pending'):
        pending = matching_preflight.run(net,proj,fn,max_profiles=2)
    assert matching_preflight.run(net,proj,fn,max_profiles=2)['cached']
    fn['asm']=CONVERT
    assert matching_preflight.run(net,proj,fn,max_profiles=2)['tested']==2
    fn['best_source']+='\n'
    assert matching_preflight.run(net,proj,fn,max_profiles=2)['tested']==2


def test_preflight_sdk_profile_is_second_and_current_profile_stays_first(monkeypatch):
    monkeypatch.setattr(matching_preflight.toolchains,'installed',lambda _: {'pinned':True})
    net,proj,fn,state=setup();proj['arch']='mips'
    fn['meta']=json.dumps({'naming':{'method':'sdk_signature'}})
    net.cfg=lambda _: {'compiler':{'toolchain':'ido-5.3','flags':['-O2']}}
    matching_preflight.run(net,proj,fn,max_profiles=2)
    assert state['calls']==[['-O2'],['-O1']]


def test_raw_assembly_spacing_and_comment_columns_still_prove_entry_address():
    source='void f(void){sink((u8*)func_80001000+0x80);}'
    asm='/* 100 80000000 00000000 */  lui $t4, %hi(func_80001000 + 0x80)\n' \
        '/* 104 80000004 00000000 */  addiu $t4, $t4, %lo(func_80001000 + 0x80)'
    assert entry_point_repairs.variants(source,'f',asm)
    assert entry_point_repairs.variants(source.replace('0x80','0200'),'f',asm)

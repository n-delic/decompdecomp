"""Explicit local definitions and configured branches remain hypotheses for the ordinary judge."""
import json

import pytest

from decompnet.app import source_conditions as conditions
from decompnet.app import source_definitions as definitions
from decompnet.app import source_diagnostics
from decompnet.app import source_preparation as prep
from decompnet.app.permute import Permuter, State
from decompnet.network import Agent, NetworkError
from tests.test_source_preparation import config, prepared_net


def test_conditional_braces_and_line_numbers_select_only_the_configured_branch(tmp_path):
    cfg=config(tmp_path);cfg['overrides']='FINAL=1'
    (tmp_path/'types.h').write_text('typedef int word;\n')
    original='''void original(void)
{
 if (ready) {
#if FINAL
  send();
 }
#else
  if(extra) {send();}
 }
#endif
 finish();
}
'''
    (tmp_path/'game.c').write_text(original)
    source,_=prep.prepare(prep.Reader(cfg),cfg,('f','original','game.c'),{'original':'f'},'')
    assert 'finish();\n}' in source and 'if(extra)' not in source
    assert source.count('\n')==original.count('\n')+1
    assert source.startswith('#line 1 "reference.c"\n')


def test_nested_elif_undef_and_numeric_conditions_preserve_lines():
    text='#define A 0xFFu\n#if A == 255 && !defined(B)\nyes\n#elif 1\nno\n#else\nalso_no\n#endif\n'
    selected,macros=conditions.select(text)
    assert 'yes\n' in selected and 'also_no' not in selected
    assert selected.count('\n')==text.count('\n') and macros['A']=='0xFFu'
    assert 'yes' not in conditions.select(text,overrides={'A':'0'})[0]
    assert 'yes' in conditions.select('#undef A\n#ifndef A\nyes\n#endif\n',{'A':'1'})[0]


def test_unsupported_or_unbalanced_conditions_do_not_guess_or_execute():
    for text in ('#if run()\nx\n#endif\n','#if 1 << 40\nx\n#endif\n','#else\nx','#if 1\nx'):
        with pytest.raises(NetworkError):conditions.select(text)
    with pytest.raises(NetworkError,match='compiler-specific'):
        conditions.select('#ifdef __GNUC__\nx\n#endif')
    assert 'x' not in conditions.select('#if 0\n#if unsupported()\nx\n#endif\n#endif')[0]


def test_only_explicit_referenced_objects_are_preserved_and_plain_variant_survives(tmp_path):
    cfg=config(tmp_path);cfg['definitions']='manager @ globals.c\nunused @ absent.c';cfg['aliases']='manager = D_80001000'
    (tmp_path/'game.c').write_text('int original(void){return manager.active;}\n')
    (tmp_path/'types.h').write_text('typedef struct {int active;} Manager;\nextern Manager manager;\n')
    (tmp_path/'globals.c').write_text('// static is only a comment\nstatic Manager manager = {0};\n')
    variants=prep.prepare_variants(prep.Reader(cfg),cfg,('f','original','game.c'),{'original':'f','manager':'D_80001000'},'')
    assert len(variants)==2
    assert 'Manager D_80001000 = {0};' in variants[0][0]
    assert 'static Manager' not in variants[0][0]
    assert '#line 1 "reference.c"' in variants[0][0]
    assert 'Manager D_80001000 = {0};' not in variants[1][0]
    assert variants[0][1]==variants[1][1]


def test_definition_parser_rejects_externs_duplicates_and_multiple_declarators():
    for source in ('extern int x;','int x;\nint x;','int y, x;','int x=1,y=2;'):
        with pytest.raises(NetworkError):definitions.extract(source,'x')
    assert 'int x={0};' in definitions.extract('typedef int word;\nint x={0};','x')
    with pytest.raises(NetworkError):definitions.mappings('x @ a.c\nx @ b.c')


def test_failed_definition_candidate_does_not_starve_plain_variant_or_repeat_compiles(tmp_path,monkeypatch):
    net,proj,fn=prepared_net();net.cfg=lambda p:json.loads(p['config']);net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug=lambda _:None;net.links={};net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    cfg=config(tmp_path);cfg['definitions']='x @ globals.c'
    (tmp_path/'game.c').write_text('int original(void){return x;}')
    (tmp_path/'types.h').write_text('extern int x;')
    (tmp_path/'globals.c').write_text('int x=0;')
    prep.configure(net,proj,cfg)
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    calls=[]
    def attempt(permuter,proj,fn,source,*args):
        calls.append(source)
        if 'int x=0;' in source:raise NetworkError('Prepared C did not compile')
        return 100
    monkeypatch.setattr(prep,'attempt',attempt)
    assert prep.run(Permuter(net),proj,State(),budget=2)['tested']==2
    assert len(calls)==2 and 'int x=0;' not in calls[1]
    assert prep.run(Permuter(net),proj,State(),budget=2)['tested']==0
    assert not net.store.one('SELECT 1 FROM claims')


def test_definition_variants_share_one_compile_budget(tmp_path,monkeypatch):
    net,proj,fn=prepared_net();net.cfg=lambda p:json.loads(p['config']);net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug=lambda _:None;net.links={};net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    cfg=config(tmp_path);cfg['definitions']='x @ globals.c'
    (tmp_path/'game.c').write_text('int original(void){return x;}');(tmp_path/'types.h').write_text('extern int x;');(tmp_path/'globals.c').write_text('int x=0;')
    prep.configure(net,proj,cfg)
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    calls=[];monkeypatch.setattr(prep,'attempt',lambda *a:calls.append(a) or 100)
    assert prep.run(Permuter(net),proj,State(),budget=1)['tested']==1 and len(calls)==1


def test_extra_address_load_diagnostic_uses_diff_evidence_not_source_name():
    rows=[['>','','lui $at, %hi(D_80001000)'],['>','','lui $at, %hi(D_80001004)']]
    hints=source_diagnostics.hints('int f(void){return 1;}','f',arch='mips',rows=rows)
    assert any('original object definitions' in h for h in hints)
    assert not source_diagnostics.hints('int f(void){return 1;}','f',arch='mips',rows=rows[:1])
    assert not source_diagnostics.hints('int f(void){return 1;}','f',arch='ppc',rows=rows)


def test_comment_directives_do_not_change_branch_state():
    text='/*\n#if unknown()\n*/\nint value;\n'
    assert conditions.select(text)[0]==text

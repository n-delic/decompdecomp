"""Explicit callback casts are not evidence of compatible callable types."""
from types import SimpleNamespace

import pytest

from decompnet.app import callback_contracts as contracts
from decompnet.app import wrapper_contracts

SOURCE = 'void install(int, void (*)(void));void callback(void);void caller(void){install(1,(void (*)(void)) callback);}'
SELECTED = {'callback':'void callback(void *node){use(node);}'}


def test_selected_callback_mismatch_survives_explicit_function_pointer_cast():
    items = contracts.leads(SOURCE,'caller',SELECTED)
    assert len(items)==1
    assert 'install argument 2: callback selected void(void*), declared callback void(void)' in items[0]
    message = contracts.message(items)
    assert 'cast does not make incompatible' in message
    assert 'all registered callbacks' in message
    assert 'No signature or source was changed' in message


def test_pointer_returning_constructor_and_named_callback_parameter():
    source = SOURCE.replace('void install(int, void (*)(void))','Widget *install(int, void (*hook)(void))')
    assert contracts.leads(source,'caller',SELECTED)


@pytest.mark.parametrize('argument', ['callback','&callback','(void (*)(void)) callback'])
def test_plain_and_address_callback_arguments(argument):
    assert contracts.leads(SOURCE.replace('(void (*)(void)) callback',argument),'caller',SELECTED)


@pytest.mark.parametrize('source,selected', [
    (SOURCE,{'callback':'void callback(void){}'}),
    (SOURCE,{'callback':'void callback(){}'}),
    (SOURCE,{'callback':'void callback(void *node){} void callback(int n){}'}),
    ('namespace N{'+SOURCE+'}',SELECTED),
    (SOURCE,{'callback':'void callback(Hidden value){}'}),
    (SOURCE.replace('void (*)(void)','Hook',1),SELECTED),
    (SOURCE.replace('(void (*)(void)) callback','choose()?callback:other'),SELECTED),
    (SOURCE.replace('void caller(void)','void caller(int callback)'),SELECTED),
    (SOURCE.replace('install(1,','Unknown callback;install(1,'),SELECTED),
    (SOURCE.replace('install(1,','void (*install)(int,Hook);install(1,'),SELECTED),
    ('#define callback other\n'+SOURCE,SELECTED),
    ('#if FLAG\n'+SOURCE+'\n#endif',SELECTED),
    ('#include "hidden.h"\n'+SOURCE,SELECTED),
    (SOURCE.replace('void install(int, void (*)(void));','void install(int, void (*)(void));void install(int,Hook);'),SELECTED),
])
def test_unknown_indirect_shadowed_macro_or_ambiguous_forms_are_quiet(source,selected):
    assert not contracts.leads(source,'caller',selected)


def test_repeated_sites_deduplicate_and_queries_are_reused():
    source = SOURCE.replace('install(1,(void (*)(void)) callback);','install(1,(void (*)(void)) callback);'*3)
    calls=[]
    def read(*args):
        calls.append(args)
        return [{'name':name,'matched_source':text}for name,text in SELECTED.items()]
    message = wrapper_contracts.brief(SimpleNamespace(store=SimpleNamespace(all=read)),{'id':1},{'name':'caller'},source)
    assert len(calls)==1
    assert message.count('install argument 2')==1
    assert 'Callback-contract review' in message


def test_cheap_claim_cue_defers_evidence_lookup():
    hint = wrapper_contracts.source_hint(SOURCE,'caller')
    assert 'get_function(section="brief")' in hint
    assert 'Callback casts present' in hint
    assert not contracts.source_hint(SOURCE.replace('(void (*)(void)) callback','callback'),'caller')
    assert not contracts.source_hint('/* '+SOURCE+' */','caller')

from decompnet.app.source_next_steps import prototype_hints, recommendations, untested_profiles


def test_helper_float_argument_and_unsigned_return_disagreements_are_review_leads():
    hints = prototype_hints('unsigned material(void *p); int sound(int id, float volume);',
                            'void f(void){material(p);sound(1,0);}',
                            'int material(void *p); void sound(int id, int volume);')
    assert len(hints) == 2 and 'float' in hints[1]
    assert all('declarations were not changed' in h for h in hints)
    assert not prototype_hints('int sound(int a);','void f(void){sound(1);}','extern int sound(int a);')


def test_revision_guidance_preserves_better_draft_and_never_patches_control_flow():
    rows = {'missing_calls':{'save':1}, 'extra_calls':{'write':1},
            'target':{'branches':['beq'],'exits':1},'candidate':{'branches':[],'exits':1}}
    hints = recommendations('void f(void){write();}', 'void f(void){save();}', rows)
    assert 'saved-only save' in hints[0] and 'preserve the better draft' in hints[0]
    assert 'No branches were patched' in hints[1]
    assert not recommendations('void f(void){}','void f(void){}',{})


def test_profile_budget_advances_past_cached_choices_and_deduplicates():
    choices = [['-O2'],['-g'],['-g'],['-O1'],['-O3'],['-O0']]
    assert untested_profiles(choices,['-O2'],tuple,lambda k:k==('-g',)) == [['-O1'],['-O3']]
    assert untested_profiles(choices,['-O2'],tuple,lambda k:k in {('-g',),('-O1',),('-O3',)}) == [['-O0']]


def test_register_only_advice_checks_prototypes_before_body_rewrites():
    assert 'float zero arguments' in recommendations('','',{'register_only':True})[0]


def test_parameter_names_do_not_create_false_signature_disagreements():
    assert not prototype_hints('unsigned sound(int id, float volume);',
                               'void f(void){sound(1,0);}',
                               'extern unsigned sound(int number, float level);')


def test_cached_source_miss_continues_profiles_but_rejected_lead_does_not(tmp_path, monkeypatch):
    import json

    from decompnet.app import source_lead_analysis as analysis
    from decompnet.app import source_preparation as prep
    from decompnet.app.permute import Permuter, State
    from decompnet.network import Agent
    from tests.test_source_preparation import config, prepared_net

    net, proj, fn = prepared_net()
    net.cfg = lambda p: json.loads(p['config'])
    net.project_row = lambda s: net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug = lambda s: None
    net.links = {}
    net.local_agent = lambda n: (Agent(1, 'autopilot', None), '')
    cfg = config(tmp_path)
    (tmp_path/'types.h').write_text('')
    (tmp_path/'game.c').write_text('int original(void){return 1;}')
    prep.configure(net, proj, cfg)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    calls = []
    monkeypatch.setattr(prep, 'attempt', lambda *args: calls.append('source') or 50)
    monkeypatch.setattr(prep, 'private_profiles', lambda *args: calls.append('profiles'))
    worker = Permuter(net)
    prep.run(worker, proj, State(), budget=3)
    prep.run(worker, proj, State(), budget=3)
    assert calls == ['source', 'profiles', 'profiles']
    monkeypatch.setattr(analysis, 'rejected', lambda *args: True)
    prep.run(worker, proj, State(), budget=3)
    assert calls == ['source', 'profiles', 'profiles']


def test_padded_reference_headers_keep_line_layout_without_repeated_whitespace_scans():
    padding = '\n' * 20000
    assert not prototype_hints(padding+'int sound(int id);', 'void f(void){sound(0);}', padding+'int sound(int number);')


def test_padded_original_definition_keeps_source_line_boundaries():
    from decompnet.forks.csource import find
    padding = '\n' * 20000
    source = padding + '#define LIMIT 1\nint original(void) { return LIMIT; }\n'
    span = find(source, 'original')
    assert span and 'int original(void)' in source[span[0]:span[1]]


def test_new_prepared_context_tests_profiles_even_when_it_improves_best(tmp_path, monkeypatch):
    import json

    from decompnet.app import source_preparation as prep
    from decompnet.app.permute import Permuter, State
    from decompnet.network import Agent
    from tests.test_source_preparation import config, prepared_net
    net,proj,fn=prepared_net()
    net.cfg=lambda p:json.loads(p['config'])
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug=lambda s:None
    net.links={}
    net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    cfg=config(tmp_path)
    (tmp_path/'types.h').write_text('')
    (tmp_path/'game.c').write_text('int original(void){return 1;}')
    prep.configure(net,proj,cfg)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('UPDATE functions SET best_score=100')
    calls=[]
    monkeypatch.setattr(prep,'attempt',lambda *a:calls.append('improved') or 20)
    monkeypatch.setattr(prep,'private_profiles',lambda *a:calls.append('profiles'))
    prep.run(Permuter(net),proj,State(),budget=3)
    assert calls==['improved','profiles']

import json

import pytest

from decompnet.app import named_array_context as arrays
from decompnet.app import prepared_evidence as evidence
from decompnet.app import source_preparation as prep
from decompnet.network import Agent, NetworkError
from tests.test_source_preparation import prepared_net as base_net


def prepared_net():
    net,proj,fn=base_net()
    with net.store.tx() as db:db.execute("UPDATE functions SET size=4")
    return net,proj,net.store.one("SELECT * FROM functions WHERE id=1")


def test_exact_bundle_publishes_profile_context_and_source_together():
    net,proj,fn=prepared_net()
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    expected=prep.inputs(net,proj,fn)
    net._record(fn,Agent(1,'autopilot',None),0,'new source',0,matched=True,
                effective_settings={'compiler':settings},prepared_header='new types',
                prepared_inputs=expected,prepared_compiler=settings)
    current=net.store.one('SELECT * FROM functions WHERE id=1')
    assert current['status']=='matched' and current['matched_source']=='new source'
    assert net._context(1,current['unit'])['header']=='new types'
    assert net.store.one('SELECT header FROM verified_matches')['header']=='new types'
    cfg=json.loads(net.store.one('SELECT config FROM projects')['config'])
    assert cfg['compiler']=={} and cfg['compiler_ranges'][0]['flags']==['-O3']


@pytest.mark.parametrize('change',['source','claim','overlap','miss'])
def test_bundle_publication_rolls_back_on_stale_claim_overlap_or_nonexact(change):
    net,proj,fn=prepared_net();expected=prep.inputs(net,proj,fn)
    with net.store.tx() as db:
        if change=='source':db.execute("UPDATE functions SET best_source='different'")
        if change=='claim':db.execute('DELETE FROM claims')
        if change=='overlap':db.execute("INSERT INTO functions(project_id,name,n_insns,vram,size) VALUES(1,'overlap',1,4096,4)")
    before=net.store.one('SELECT config FROM projects')['config']
    with pytest.raises(NetworkError):
        net._record(fn,Agent(1,'autopilot',None),1 if change=='miss' else 0,'new',0,matched=change!='miss',
                    prepared_header='new header',prepared_inputs=expected,prepared_compiler={'toolchain':'ido-5.3','flags':['-O3']})
    assert net.store.one('SELECT config FROM projects')['config']==before
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0


def test_review_rejects_stale_before_compiling():
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    with net.store.tx() as db:
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(1,?,?,?,?)',
                   ('local-source-profile-v1:key','0','test',json.dumps({'exact':True,'inputs':'stale'})))
    with pytest.raises(NetworkError,match='stale'):
        evidence.apply(net,proj,'local-source-profile-v1:key',print)
    assert evidence.listing(net,proj)[0]['function']=='f'


def test_overlay_profile_stops_before_other_entry_without_changing_its_compiler():
    from decompnet.project import compiler_for
    net,proj,fn=prepared_net()
    with net.store.tx() as db:
        db.execute('UPDATE functions SET size=16,rom_offset=100 WHERE id=1')
        db.execute("INSERT INTO functions(project_id,name,vram,size,rom_offset) VALUES(1,'other_overlay',4104,16,500)")
    fn=net.store.one('SELECT * FROM functions WHERE id=1')
    expected=prep.inputs(net,proj,fn)
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    with net.store.tx() as db:evidence.publish_profile(net,db,fn,expected,settings,Agent(1,'autopilot',None))
    cfg=json.loads(net.store.one('SELECT config FROM projects')['config'])
    assert cfg['function_compilers']['100:4096']['toolchain']=='ido-5.3'
    assert compiler_for(cfg,4096,fn)['toolchain']=='ido-5.3'
    assert compiler_for(cfg,4104)==cfg['compiler']


def test_same_address_overlays_have_independent_profiles():
    net,proj,fn=prepared_net();settings={'toolchain':'ido-5.3','flags':['-O3']}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':settings}),))
        db.execute('UPDATE functions SET rom_offset=100 WHERE id=1')
        db.execute("INSERT INTO functions(project_id,name,vram,size,rom_offset) VALUES(1,'other_overlay',4096,4,500)")
    proj=net.store.one('SELECT * FROM projects WHERE id=1');fn=net.store.one('SELECT * FROM functions WHERE id=1')
    expected=prep.inputs(net,proj,fn)
    with net.store.tx() as db:
        evidence.publish_profile(net,db,fn,expected,settings,Agent(1,'autopilot',None))
    assert net.store.one('SELECT config FROM projects')['config']==proj['config']
    with net.store.tx() as db:
        evidence.publish_profile(net,db,fn,expected,{'toolchain':'ido-5.3','flags':['-O2']},Agent(1,'autopilot',None))
    from decompnet.project import compiler_for
    cfg=json.loads(net.store.one('SELECT config FROM projects')['config'])
    other=net.store.one("SELECT * FROM functions WHERE name='other_overlay'")
    assert compiler_for(cfg,4096,fn)['flags']==['-O2']
    assert compiler_for(cfg,4096,other)['flags']==['-O3']


def test_named_array_requires_declared_extent_and_verified_rom_values():
    source='void f(void){ int choices[]={1,2}; use(choices); say("choices"); }'
    header='extern const int D_80004000[2];'
    read=lambda address,size:bytes.fromhex('0000000100000002') if address==0x80004000 and size==8 else None
    resolve=lambda name:0x80004000 if name=='D_80004000' else None
    pairs=arrays.variants(source,'f',header,read,resolve)
    assert len(pairs)==1 and 'choices.values' in pairs[0][0] and 'say("choices")' in pairs[0][0]
    assert not arrays.variants(source,'f',header,lambda *args:b'wrong',resolve)
    assert not arrays.variants(source,'f',header.replace('[2]','[3]'),read,resolve)
    assert not arrays.variants(source,'f',header,read,lambda name:None)


def test_data_diagnostics_do_not_override_verdict():
    from decompnet.app.candidate_diagnostics import text
    checks={'instructions':{'exact':True},'linked_bytes':{'exact':False},'data':['your data for table differs; differing range 0x80004004-0x80004008']}
    message=text(checks)
    assert 'Instructions match; constant data differs' in message and '0x80004004' in message
    assert 'Linked bytes verified identical' not in message


def test_review_recompiles_outside_transaction_and_records_exact_bundle(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app.permute import Permuter
    from decompnet.rom import export
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE id=1')
    net.local_agent=lambda name:(Agent(1,'autopilot',None),'')
    net.permuter=Permuter(net)
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    item={**settings,'source':'int f(void){return 1;}','header':'new types','exact':True,'inputs':evidence.digest(net,proj,fn)}
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(1,?,?,?,?)',
                   ('local-source-profile-v2:key','0','test',json.dumps(item)))
    def compile(p,h,s,f):
        assert not getattr(net.store._local,'in_tx',False)
        assert h=='new types' and json.loads(p['config'])['compiler_ranges'][0]['flags']==['-O3']
        return SimpleNamespace(ok=True,obj=b'synthetic',effective_settings={'compiler':settings})
    net._compile=compile;net._evaluate=lambda *args:(object(),True,[])
    monkeypatch.setattr(export,'_single_definition',lambda *args:True)
    assert evidence.apply(net,proj,'local-source-profile-v2:key',lambda line:None)['matched']
    assert net.store.one('SELECT status FROM functions')['status']=='matched'
    assert net.store.one('SELECT header FROM verified_matches')['header']=='new types'
    assert not net.store.one('SELECT * FROM claims')


@pytest.mark.parametrize('verdict',[True,False])
def test_explicit_pair_keeps_body_separate_and_only_publishes_exact_bundle(monkeypatch,verdict):
    from types import SimpleNamespace

    from decompnet import scoring, toolchains
    from decompnet.app.permute import Permuter
    from decompnet.rom import export
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE id=1')
    net._function=lambda *args:net.store.one('SELECT * FROM functions WHERE id=1')
    net.local_agent=lambda name:(Agent(1,'autopilot',None),'');net.permuter=Permuter(net)
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    monkeypatch.setattr(toolchains,'installed',lambda *args:True)
    source='#line 700 "original.c"\nint f(void)\n{\n return 1;\n}\n'
    header='typedef struct {int a;} Item;\n'
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    before=proj['config']
    def compile(p,h,s,f):
        assert s==source and h==header
        return SimpleNamespace(ok=True,obj=b'synthetic',effective_settings={'compiler':settings})
    net._compile=compile;net._evaluate=lambda *args:(object(),verdict,[]);net._target_obj=lambda *args:object()
    monkeypatch.setattr(export,'_single_definition',lambda *args:True)
    monkeypatch.setattr(scoring,'diff',lambda *args:SimpleNamespace(score=0,rows=[],bytes_match=True))
    result=evidence.try_pair(net,proj,'f',source,header,lambda line:None,settings)
    assert result['matched']==verdict
    current=net.store.one('SELECT * FROM functions WHERE id=1')
    if verdict:
        assert current['matched_source']==source and net._context(1,current['unit'])['header']==header
    else:
        assert current['best_source']=='old source' and net.project_row('game')['config']==before
    assert not net.store.one('SELECT * FROM claims')


def test_explicit_refresh_rejudges_complete_stale_evidence_against_current_inputs(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app.permute import Permuter
    from decompnet.rom import export
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE id=1')
    net.local_agent=lambda name:(Agent(1,'autopilot',None),'');net.permuter=Permuter(net)
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    item={**settings,'source':'int f(void){return 1;}','header':'new types','exact':True,'inputs':'old-build'}
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(1,?,?,?,?)',
                   ('local-source-profile-v2:stale','0','test',json.dumps(item)))
    net._compile=lambda *args:SimpleNamespace(ok=True,obj=b'synthetic',effective_settings={'compiler':settings})
    net._evaluate=lambda *args:(object(),True,[])
    monkeypatch.setattr(export,'_single_definition',lambda *args:True)
    assert evidence.apply(net,proj,'local-source-profile-v2:stale',lambda line:None,refresh=True)['matched']
    assert net.store.one('SELECT matched_source FROM functions')['matched_source']==item['source']


@pytest.mark.parametrize('change',['range_checks','compiler_survey','compiler','source','claim'])
def test_reverified_bundle_ignores_only_nonbuild_progress(monkeypatch,change):
    from types import SimpleNamespace

    from decompnet.app import compiler_context
    net,proj,fn=prepared_net()
    net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net.project_row=lambda slug:net._project(slug)
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    expected=prep.inputs(net,proj,fn)
    settings={'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-fno-thread-jumps']}
    calls=[]
    def compile(*args):
        calls.append(True)
        with net.store.tx() as db:
            if change=='source':db.execute("UPDATE functions SET best_source='concurrent C'")
            elif change=='claim':db.execute('DELETE FROM claims')
            else:
                cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=1').fetchone()['config'])
                cfg[change]={'toolchain':'gcc-2.7.2-kmc','flags':['-O1']} if change=='compiler' else {'finished':True}
                db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        return SimpleNamespace(ok=True,obj=b'object',effective_settings={'compiler':settings})
    net._compile=compile;net._evaluate=lambda *a:(object(),True,[])
    monkeypatch.setattr(compiler_context,'acceptable',lambda *a:True)
    if change in ('range_checks','compiler_survey','compiler'):
        # publish_profile expects range-check lists; use a genuine progress structure.
        original=compile
        def compile_valid(*args):
            result=original(*args)
            with net.store.tx() as db:
                cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=1').fetchone()['config'])
                if change=='range_checks':cfg[change]=[{'id':1,'origin':0,'status':'finished'}]
                db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
            return result
        net._compile=compile_valid
        assert evidence.promote_measured(net,proj,fn,'exact C','private types',settings,Agent(1,'autopilot',None),expected)
        assert net._function('f','game')['status']=='matched'
    else:
        with pytest.raises(NetworkError):
            evidence.promote_measured(net,proj,fn,'exact C','private types',settings,Agent(1,'autopilot',None),expected)
        assert net._function('f','game')['status']=='open'
    assert len(calls)==(2 if change=='compiler' else 1)

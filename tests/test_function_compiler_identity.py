"""Overlays sharing a RAM address keep their compiler, declarations and mirrored work separate."""
import json

from decompnet.app import progress, room_matches
from decompnet.app.engine import RomNetwork
from decompnet.project import compiler_for


def test_function_profiles_use_original_image_identity_before_address_ranges():
    default={'toolchain':'gcc','flags':['-O2']}
    cfg={'compiler':default,'compiler_ranges':[{'start':4096,'end':4100,'toolchain':'ido','flags':['-O1']}],
         'function_compilers':{'0':{'function':'first','vram':4096,'toolchain':'gcc','flags':['-O3']}}}
    first={'name':'first','vram':4096,'rom_offset':0}
    second={'name':'second','vram':4096,'rom_offset':4}
    assert compiler_for(cfg,4096,first)['flags']==['-O3']
    assert compiler_for(cfg,4096,second)['flags']==['-O1']
    assert compiler_for(cfg,4096,{'name':'renamed','rom_offset':0})['flags']==['-O3']
    assert compiler_for(cfg,4096,{'name':'first'})['flags']==['-O3']  # a display query with only the name
    assert compiler_for(cfg,4096,{'name':'first','rom':0})['flags']==['-O3']  # exported manifest
    assert compiler_for(cfg,4096)['flags']==['-O1']  # no identity: never borrow a different overlay's profile
    proj={'name':'demo'}
    before=progress._config_key(proj,cfg)
    cfg['function_compilers']['0']['flags']=['-O0']
    assert progress._config_key(proj,cfg)!=before


def test_room_inputs_and_results_select_the_original_overlay(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    cfg={'kind':'rom','platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(1,?,?,?,?)',
                   ('demo','demo',str(tmp_path),json.dumps(cfg)))
        for name,offset in [('first',0),('second',4)]:
            db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,unit) VALUES(1,?,4096,?,4,1,?)',
                       (name,offset,name))
    room_matches.sync_inputs(net,'demo',{'compiler_settings':{**cfg,'function_compilers':{
        '4':{'function':'second','vram':4096,'toolchain':'gcc-2.7.2-kmc','flags':['-O3']}}},
        'function_contexts':[{'vram':4096,'rom_offset':4,'header':'private second declarations'}]})
    a=net._function('first','demo');b=net._function('second','demo')
    assert a['unit']=='first' and net._context(1,b['unit'])['header']=='private second declarations'
    room_matches.sync_inputs(net,'demo',{'function_contexts':[{'vram':4096,'header':'ambiguous legacy header'}]})
    assert net._context(1,b['unit'])['header']=='private second declarations'
    net.apply_remote_match('demo',4096,'second source','remote',rom_offset=4)
    net.apply_remote_best('demo',4096,7,'first source',rom_offset=0)
    assert net._function('second','demo')['matched_source']=='second source'
    assert net._function('first','demo')['status']=='open'
    assert net._function('first','demo')['best_source']=='first source'
    net.apply_remote_match('demo',4096,'ambiguous legacy source','remote')
    assert net._function('first','demo')['status']=='open'


def test_export_build_uses_each_overlays_recorded_profile(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from decompnet.rom import export

    functions=[{'name':'first','vram':4096,'rom':0,'c':'first.c'},
               {'name':'second','vram':4096,'rom':4,'c':'second.c'}]
    manifest={'compiler':{'toolchain':'gcc','flags':['-O0']},'functions':functions,
              'function_compilers':{'0:4096':{'vram':4096,'function':'first','toolchain':'gcc','flags':['-O2']},
                                    '4:4096':{'vram':4096,'function':'second','toolchain':'gcc','flags':['-O3']}}}
    script=tmp_path/'build.py'
    script.write_text(Path(export.__file__).with_name('export_build.py').read_text())
    (tmp_path/'build.json').write_text(json.dumps(manifest))
    (tmp_path/'first.c').write_text('int first(void){return 1;}')
    (tmp_path/'second.c').write_text('int second(void){return 2;}')
    spec=importlib.util.spec_from_file_location('generated_overlay_build',script)
    build=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    seen=[]
    def config(root,cfg):
        seen.append(cfg['compiler']['flags'])
        return SimpleNamespace(cmd=['fake','{src}','{obj}'])
    def compile(cfg,root,argv,*,inputs,outputs):
        for output in outputs:(root/output).write_bytes(b'object')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(build,'ROOT',tmp_path)
    monkeypatch.setattr(build,'B',manifest,raising=False)
    monkeypatch.setattr(build,'binutils',lambda _: 'unused')
    monkeypatch.setattr(build,'compiler_config',config)
    monkeypatch.setattr(build,'run_in',compile)
    monkeypatch.setattr(build,'trim',lambda _:None)
    monkeypatch.setattr(build,'weaken_data',lambda _:None)
    build.build_objects([('c','first.c','first.o'),('c','second.c','second.o')])
    assert seen==[['-O2'],['-O3']]

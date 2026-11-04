"""Git publication and failed builds, with a tiny homemade export and no game assets."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import repository_sync as sync
from decompnet.network import NetworkError


def commit(root):
    sync.git(root,'add','.')
    sync.git(root,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','checkpoint')


@pytest.fixture
def project(tmp_path,monkeypatch):
    if not shutil.which('git'):pytest.skip('needs git')
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    image=b'private homemade image'
    (root/'baserom.z64').write_bytes(image)
    (root/'.gitignore').write_text('*.z64\nbuild/\n')
    (root/'README.md').write_text('Keep this guide.\n')
    (root/'src/game').mkdir(parents=True)
    (root/'src/game/curated.c').write_text('void curated(void) {}\n')
    manifest={'sha1':hashlib.sha1(image).hexdigest(),'image':'baserom.z64','output':'build/game.z64',
              'functions':[{'name':'f','seg':'game','vram':4096}, {'name':'curated','seg':'game','vram':8192}],
              'symbols':{},'compiler_ranges':[]}
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'units':[{'name':'game/f','target_path':'orig/f.o','base_path':'empty.o','metadata':{'complete':False}}]}))
    (root/'build.py').write_text('''import sys,json,pathlib,shutil
r=pathlib.Path('.');b=json.loads((r/'build.json').read_text())
if len(sys.argv)>1:
 f=next(f for f in b['functions']if f['name']==sys.argv[2]);p='src/'+f['seg']+'/'+f['name']+'.c'
 if 'reject_me' in (r/p).read_text():sys.exit(1)
 f['c']=p;(r/'build.json').write_text(json.dumps(b));sys.exit(0)
if (r/'fail-full').exists():sys.exit(1)
(r/'build').mkdir(exist_ok=True);shutil.copyfile(b['image'],b['output'])
''')
    commit(root)
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *args:True)
    cfg={'rom':{'sha1':manifest['sha1']},'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    rows=[{'id':1,'name':'f','vram':4096,'matched_source':'void f(void) {}','unit':''},
          {'id':2,'name':'curated','vram':8192,'matched_source':'void curated(void) { changed(); }','unit':''}]
    class Store:
        def all(self,query,*args):
            if "status='matched'" in query:return rows
            if 'FROM symbols' in query:return []
            return [{'name':x['name'],'vram':x['vram']}for x in rows]
        def one(self,query,*args):return None if 'FROM attempts' in query else {'n':len(rows)}
    net=SimpleNamespace(project_row=lambda slug:{'id':1,'config':json.dumps(cfg)},cfg=lambda proj:cfg,
                        is_rom=lambda proj:True,store=Store(),_context=lambda *args:{'header':'typedef int s32;\n'},
                        _update_cfg=lambda slug,fn:fn(cfg),_compile=lambda *args:SimpleNamespace(ok=True,obj=b'fake object'),
                        _evaluate=lambda *args:(object(),True,[]),
                        _candidate_checks=lambda *args:{'export':{'ready':True}})
    return root,net,rows


def run(project):
    root,net,_=project
    return sync.run(net,'game',str(root))


def test_only_verified_managed_files_are_published(project,monkeypatch):
    root,net,_=project
    result=run(project)
    assert result['accepted']==['f'] and result['verified_build']
    assert (root/'src/game/curated.c').read_text()=='void curated(void) {}\n'
    assert (root/'README.md').read_text()=='Keep this guide.\n'
    assert not (root/'build').exists()
    assert net.cfg(None)['repository_sync_folder']==str(root)
    assert (root/'src/game/f.c').read_text().endswith('void f(void) {}\n')
    unit=json.loads((root/'objdiff.json').read_text())['units'][0]
    assert unit['metadata']=={'complete':True,'source_path':'src/game/f.c'}
    assert unit['target_path']=='orig/f.o'
    assert 'baserom' not in result['changes']
    commit(root)
    def unexpected_compile(*args):raise AssertionError('Unchanged synced C must not be compiled again.')
    monkeypatch.setattr(net,'_compile',unexpected_compile)
    second=run(project)
    assert second['accepted']==[] and second['changes']==''


def test_later_sync_updates_its_sources_and_profile(project):
    root,net,rows=project;run(project);commit(root)
    rows[0]['matched_source']='void f(void) { next(); }'
    result=run(project)
    assert result['accepted']==['f'] and 'next();' in (root/'src/game/f.c').read_text()
    assert json.loads((root/'build.json').read_text())['compiler_ranges'][0]['flags']==['-O2']


def test_committed_manual_edits_stop_source_ownership(project):
    root,net,rows=project;run(project);commit(root)
    path=root/'src/game/f.c';path.write_text('void f(void) { manual(); }');commit(root)
    rows[0]['matched_source']='void f(void) { new_app_match(); }'
    result=run(project)
    assert result['accepted']==[] and 'manual' in path.read_text()
    assert 'src/game/f.c' not in json.loads((root/sync.STATE).read_text())['owned']


def test_individual_failure_is_reported_without_changing_existing_c(project):
    root,net,rows=project;rows[0]['matched_source']='void f(void) { reject_me(); }'
    result=run(project)
    assert result['accepted']==[] and result['skipped'][0]['function']=='f'
    assert not (root/'src/game/f.c').exists()


def test_full_build_failure_publishes_nothing(project):
    root,net,_=project;(root/'fail-full').write_text('fail');commit(root)
    before=(root/'build.json').read_bytes()
    with pytest.raises(NetworkError,match='Full repository build failed'):run(project)
    assert (root/'build.json').read_bytes()==before and not (root/'src/game/f.c').exists()
    assert sync.git(root,'status','--porcelain')==b''


def test_wrong_image_is_rejected(project):
    root,net,_=project;(root/'baserom.z64').write_bytes(b'wrong')
    with pytest.raises(NetworkError,match='verified game file'):run(project)
    assert not (root/sync.STATE).exists()


def test_dirty_checkout_is_not_touched(project):
    root,net,_=project;(root/'README.md').write_text('my edit')
    with pytest.raises(NetworkError,match='existing changes'):run(project)
    assert (root/'README.md').read_text()=='my edit'


def test_edit_during_build_preserves_both_checkpoints(project,monkeypatch):
    root,net,_=project;original=sync.command
    def command(stage,argv,log,**kwargs):
        result=original(stage,argv,log,**kwargs)
        if len(argv)==2:(root/'README.md').write_text('concurrent edit')
        return result
    monkeypatch.setattr(sync,'command',command)
    with pytest.raises(NetworkError,match='existing changes'):run(project)
    assert (root/'README.md').read_text()=='concurrent edit'
    assert not (root/'src/game/f.c').exists()


def test_no_following_repository_links(project,tmp_path):
    root,net,_=project;outside=tmp_path/'outside';outside.write_text('do not touch')
    try:(root/'src/game/link.c').symlink_to(outside)
    except OSError:pytest.skip('symbolic links unavailable on this host')
    commit(root)
    with pytest.raises(NetworkError,match='symbolic links'):run(project)
    assert outside.read_text()=='do not touch'


def test_cancelled_build_leaves_checkout_unchanged(project):
    root,net,_=project
    class Log:
        def __call__(self,text):pass
        def progress(self,state):pass
        def check_cancel(self):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):sync.run(net,'game',str(root),Log())
    assert sync.git(root,'status','--porcelain')==b''


def test_imported_projects_keep_their_existing_checkout(project):
    root,net,_=project;net.is_rom=lambda proj:False
    assert not sync.status(net,'game')['supported']
    with pytest.raises(NetworkError,match='already works'):run(project)


def test_path_escape_is_rejected(tmp_path):
    with pytest.raises(NetworkError,match='inside'):sync.inside(tmp_path,'../escape')


def test_build_cancellation_terminates_process(tmp_path):
    class Log:
        def check_cancel(self):raise RuntimeError('stop')
    with pytest.raises(RuntimeError,match='stop'):
        sync.command(tmp_path,[sys.executable,'-c','import time;time.sleep(30)'],Log())


def test_gui_sync_progress_stop_and_errors():
    node=shutil.which('node')
    if not node:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_repository_sync.js'))],check=True,
                   capture_output=True,text=True,timeout=30)


def test_publish_rolls_back_earlier_files_on_conflict(tmp_path):
    root=tmp_path/'root';stage=tmp_path/'stage';root.mkdir();stage.mkdir()
    for name in ['a','b']:(root/name).write_text('old');(stage/name).write_text('new')
    baseline={name:sync.digest(root/name)for name in ['a','b']}
    (root/'b').write_text('concurrent')
    with pytest.raises(NetworkError,match='destination changed'):
        sync.publish(root,stage,{'a','b'},baseline,print)
    assert (root/'a').read_text()=='old' and (root/'b').read_text()=='concurrent'


def test_success_exit_cannot_bypass_original_image_verification(project):
    root,net,_=project
    script=root/'build.py'
    script.write_text(script.read_text()+"\n(r/b['image']).write_bytes(b'changed original'); (r/b['output']).write_bytes(b'changed original')\n")
    commit(root)
    with pytest.raises(NetworkError,match='differs from the original'):run(project)
    assert not (root/'src/game/f.c').exists() and not (root/sync.STATE).exists()


def test_extra_out_of_line_bodies_remain_out_of_the_build(project,monkeypatch):
    root,net,_=project
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *args:False)
    monkeypatch.setattr('decompnet.app.repository_candidates.single_source',lambda *args:None)
    result=run(project)
    assert result['accepted']==[] and 'Extra function bodies' in result['skipped'][0]['reason']
    assert not (root/'src/game/f.c').exists()


def test_manual_sync_header_edits_are_not_overwritten(project):
    root,net,rows=project;run(project);commit(root)
    header=next((root/'include').glob('decompdecomp_sync_*.h'))
    header.write_text('/* manual header */');commit(root)
    rows[0]['matched_source']='void f(void) { updated(); }'
    result=run(project)
    assert not result['accepted'] and 'manual edits to a sync header' in result['skipped'][0]['reason']
    assert header.read_text()=='/* manual header */'
    assert 'updated' not in (root/'src/game/f.c').read_text()


def test_identified_full_image_failure_keeps_other_verified_additions(project):
    root,net,rows=project
    manifest=json.loads((root/'build.json').read_text())
    manifest['functions'].append({'name':'g','seg':'game','vram':12288})
    (root/'build.json').write_text(json.dumps(manifest))
    rows.append({'name':'g','vram':12288,'matched_source':'void g(void) {}','unit':''})
    script=root/'build.py';text=script.read_text()
    text=text.replace("if (r/'fail-full').exists():sys.exit(1)","if (r/'src/game/f.c').exists():print('differs: first difference at offset 0x10 (in or after f); sizes 10 vs 10');sys.exit(1)")
    script.write_text(text);commit(root)
    result=run(project)
    assert result['accepted']==['g'] and result['verified_build']
    assert not (root/'src/game/f.c').exists() and (root/'src/game/g.c').is_file()
    assert 'full rebuilt image differed' in result['skipped'][0]['reason']
    assert (root/'src/game/curated.c').read_text()=='void curated(void) {}\n'


def compressed(project):
    root, net, _ = project
    original = b'homemade compressed cartridge'
    (root/'original.z64').write_bytes(original)
    manifest = json.loads((root/'build.json').read_text())
    manifest['cartridge'] = {'image':'original.z64', 'sha1':hashlib.sha1(original).hexdigest(), 'blocks':[]}
    net.cfg(None)['rom']['sha1'] = manifest['cartridge']['sha1']
    (root/'build.json').write_text(json.dumps(manifest))
    build = root/'build.py'
    build.write_text(build.read_text().replace("shutil.copyfile(b['image'],b['output'])",
                                             "shutil.copyfile(b.get('cartridge',{}).get('image',b['image']),b['output'])"))
    commit(root)


def test_compressed_export_uses_original_identity_and_verifies_both_images(project):
    compressed(project)
    assert run(project)['accepted'] == ['f']


@pytest.mark.parametrize('image', ['original.z64', 'baserom.z64'])
def test_compressed_sync_rejects_tampering_with_either_input(project, image):
    compressed(project)
    root, _, _ = project
    (root/image).write_bytes(b'corrupt input')
    with pytest.raises(NetworkError, match='checksum|verified game file'):
        run(project)
    assert not (root/'src/game/f.c').exists()


def test_noncontiguous_pool_is_reported_before_a_full_build(project, monkeypatch):
    _, net, _ = project
    net._candidate_checks = lambda *args: {'export': {'ready':False, 'message':'non-contiguous constant pools'}}
    monkeypatch.setattr(sync, 'command', lambda *args: pytest.fail('layout blocker reached a build'))
    result = run(project)
    assert not result['accepted']
    assert 'non-contiguous' in result['skipped'][0]['reason']


def test_a_curated_builder_can_verify_its_own_prefix_normalization(project):
    _, net, _ = project
    net._candidate_checks = lambda *args:{'export':{'state':'requires_normalization','ready':None}}
    assert run(project)['accepted'] == ['f']


def test_export_renames_callback_forward_declaration_in_private_header(project,monkeypatch):
    root,net,rows=project
    rows[0]['name']='old_entry'
    rows[0]['matched_source']='void caller(void){ callback=old_entry; }\nvoid old_entry(void){}'
    net._context=lambda *args:{'header':'void old_entry(void); /* old_entry stays in comments */\n'}
    result=run(project)
    assert result['accepted']==['old_entry']
    header=next((root/'include').glob('decompdecomp_sync_*.h')).read_text()
    assert 'void f(void);' in header and '/* old_entry stays in comments */' in header
    assert 'callback=f;' in (root/'src/game/f.c').read_text()
def test_commit_push_saves_all_progress_and_removes_old_owned_drafts(project, monkeypatch):
    from decompnet.app import repository_git, saves
    root, net, _ = project
    (root/'progress/best').mkdir(parents=True)
    (root/'progress/best/old.c').write_text('old draft')
    commit(root)
    snapshot = {'decompdecomp.json':'{"notes":["all notes"],"compiler_ranges":[]}',
                'best/new.c':'newer draft', 'include/common.h':'typedef int s32;'}
    monkeypatch.setattr(saves, '_render', lambda *args, **kwargs:(snapshot, {}))
    monkeypatch.setattr(saves, '_read_owned', lambda folder:{'best/old.c':'old draft'})
    monkeypatch.setattr(repository_git, 'prepare', lambda *args:('origin', 'refs/heads/main'))
    captured = {}
    def finish(root, files, upstream, log):
        captured['files'] = files
        assert (root/'progress/best/new.c').read_text() == 'newer draft'
        assert not (root/'progress/best/old.c').exists()
        return {'commit':'abc', 'pushed':True}
    monkeypatch.setattr(repository_git, 'finish', finish)
    result = sync.run(net, 'game', str(root), commit_push=True)
    assert result['pushed']
    assert {'progress/'+name for name in snapshot} <= captured['files']
    assert 'progress/best/old.c' in captured['files']


def test_snapshot_failure_does_not_publish_verified_sources(project, monkeypatch):
    from decompnet.app import repository_git, saves
    root, net, _ = project
    monkeypatch.setattr(repository_git, 'prepare', lambda *args:('origin', 'refs/heads/main'))
    def fail(*args, **kwargs):raise NetworkError('snapshot failed')
    monkeypatch.setattr(saves, '_render', fail)
    with pytest.raises(NetworkError, match='snapshot failed'):
        sync.run(net, 'game', str(root), commit_push=True)
    assert not sync.git(root, 'status', '--porcelain').strip()

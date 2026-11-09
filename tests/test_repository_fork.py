"""Fork setup changes remotes only after verifying ownership, ancestry and write permission."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import repository_fork
from decompnet.network import NetworkError
from tests.test_repository_progress import git


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    git(root,'init','--initial-branch=main')
    git(root,'config','user.name','Test');git(root,'config','user.email','test@example.invalid')
    (root/'file').write_text('saved');git(root,'add','file');git(root,'commit','-m','Saved work')
    git(root,'remote','add','origin','https://github.com/original/project.git')
    (root/'file').write_text('uncommitted work')
    monkeypatch.setattr(repository_fork.repository_push,'checkout',lambda *args:root)
    monkeypatch.setattr(repository_fork.repository_push,'status',lambda *args:{'can_push':True})
    real_git=repository_fork.git
    def local_git(root,*args,**kwargs):
        if args[0]=='fetch':return ''
        return real_git(root,*args,**kwargs)
    monkeypatch.setattr(repository_fork,'git',local_git)
    calls=[]
    target={'full_name':'mine/project','id':2,'fork':True,'parent':{'id':1},'permissions':{'push':True}}
    def cli(*args):
        calls.append(args)
        endpoint=args[3]
        if endpoint=='user':value={'login':'mine'}
        elif endpoint=='repos/original/project':value={'id':1}
        elif endpoint=='repos/original/project/forks':value=target
        elif endpoint=='repos/'+target['full_name'] and not any('/forks' in str(call) for call in calls[:-1]):
            return SimpleNamespace(returncode=1,stdout='')
        else:value=target
        return SimpleNamespace(returncode=0,stdout=json.dumps(value))
    monkeypatch.setattr(repository_fork.repository_github,'runner',lambda root:cli)
    return root,calls,target


def test_fork_connects_without_pushing_or_changing_files(checkout):
    root,calls,_=checkout
    head=git(root,'rev-parse','HEAD');status=git(root,'status','--porcelain')
    result=repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],print)
    assert result['fork']=='mine/project' and result['upstream']=='upstream'
    assert git(root,'remote','get-url','origin')=='https://github.com/mine/project.git'
    assert git(root,'remote','get-url','upstream')=='https://github.com/original/project.git'
    assert git(root,'rev-parse','HEAD')==head and git(root,'status','--porcelain')==status
    post=next(call for call in calls if call[3].endswith('/forks'))
    assert post[-2:]==('--method','POST') and '-f' not in post


def test_existing_upstream_is_preserved(checkout):
    root,_,_=checkout
    git(root,'remote','add','upstream','https://github.com/other/project.git')
    result=repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],print)
    assert result['upstream']=='upstream-2'
    assert git(root,'remote','get-url','upstream')=='https://github.com/other/project.git'


@pytest.mark.parametrize('problem',['permission','unrelated','not-fork','wrong-owner'])
def test_unverified_target_never_changes_origin(checkout,problem):
    root,_,target=checkout
    if problem=='permission':target['permissions']['push']=False
    elif problem=='unrelated':target['parent']['id']=99
    elif problem=='not-fork':target['fork']=False
    else:target['full_name']='other/project'
    with pytest.raises(NetworkError):repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],print)
    assert git(root,'remote','get-url','origin')=='https://github.com/original/project.git'
    assert git(root,'remote')=='origin'


def test_origin_change_and_custom_push_url_are_rejected(checkout):
    root,calls,_=checkout
    revision=repository_fork.origin(root)[2]
    git(root,'remote','set-url','origin','https://github.com/different/project.git')
    with pytest.raises(NetworkError,match='Origin changed'):repository_fork.run(None,'game','', 'mine',revision,print)
    assert not calls
    git(root,'config','remote.origin.pushurl','https://github.com/private/destination.git')
    with pytest.raises(NetworkError,match='push URL'):repository_fork.origin(root)


@pytest.mark.parametrize('url,name',[
    ('https://github.com/person/repo.git','person/repo'),('git@github.com:person/repo.git','person/repo'),
    ('ssh://git@github.com/person/repo.git','person/repo'),('https://token@github.com/person/repo.git','person/repo'),
    ('https://elsewhere.invalid/person/repo.git',None),('https://github.com/person/repo?token=secret',None)])
def test_origin_name_is_safe_for_display(url,name):
    assert repository_fork.github_name(url)==name


def test_existing_fork_is_reused(checkout):
    root,calls,_=checkout
    calls.append(('existing /forks',))
    repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],print)
    assert not any('--method' in call for call in calls)


def test_organization_and_ssh_transport(checkout):
    root,calls,target=checkout
    target['full_name']='team/project'
    git(root,'remote','set-url','origin','git@github.com:original/project.git')
    repository_fork.run(None,'game','', 'team',repository_fork.origin(root)[2],print)
    post=next(call for call in calls if '--method' in call)
    assert post[-2:]==('-f','organization=team')
    assert git(root,'remote','get-url','origin')=='git@github.com:team/project.git'


def test_cancel_leaves_remotes_untouched(checkout):
    root,calls,_=checkout
    class Log:
        def __call__(self,line):pass
        def check_cancel(self):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],Log())
    assert not calls
    assert git(root,'remote')=='origin'


def test_origin_changed_during_creation_is_not_overwritten(checkout,monkeypatch):
    root,_,_=checkout
    cli=repository_fork.repository_github.runner(root)
    def changed(*args):
        result=cli(*args)
        if '--method' in args:git(root,'remote','set-url','origin','https://github.com/other/repo.git')
        return result
    monkeypatch.setattr(repository_fork.repository_github,'runner',lambda root:changed)
    with pytest.raises(NetworkError,match='origin changed'):
        repository_fork.run(None,'game','', 'mine',repository_fork.origin(root)[2],print)
    assert git(root,'remote','get-url','origin')=='https://github.com/other/repo.git'

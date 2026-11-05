"""Publish verified ROM matches into an existing exported Git build, without replacing its tree."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import hostenv, limits, toolchains
from ..network import NetworkError
from ..project import compiler_for
from . import repository_sync_build

STATE = 'decompdecomp-sync.json'
MAX_CHECKOUT_CHANGES = 40
_LOCK = threading.Lock()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def inside(root, relative):
    """Resolve a repository-relative path without following links or escaping the checkout."""
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise NetworkError('Repository paths must stay inside the selected checkout.')
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise NetworkError('Sync cannot follow repository symbolic links. Replace the link and try again.')
    return path


def command(root, argv, log, timeout=1800):
    """Cancellable, capped builds; keep large compiler output out of the job's in-memory log."""
    with tempfile.TemporaryFile(mode='w+b') as output:
        proc = subprocess.Popen(limits.capped(argv), cwd=root, stdout=output, stderr=output,
                                **({'creationflags':hostenv.NO_WINDOW} if os.name == 'nt' else {'start_new_session':True}))
        started = time.monotonic()
        try:
            while proc.poll() is None:
                check = getattr(log, 'check_cancel', None)
                if check:
                    check()
                if time.monotonic()-started > timeout:
                    raise NetworkError('Repository verification timed out. The checkout was not changed.')
                time.sleep(.1)
        except BaseException:
            if os.name != 'nt':
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
            hostenv.stop_process_tree(proc)
            proc.wait()
            raise
        output.seek(0, 2)
        output.seek(max(0, output.tell()-4000))
        return proc.returncode, output.read().decode('utf-8', errors='replace')


def git(root, *args):
    """Read checkout state with bounded Git commands, without committing or contacting a remote."""
    exe = shutil.which('git')
    if not exe:
        raise NetworkError('Install Git before syncing a repository.')
    res = subprocess.run([exe, '-C', str(root), *args], capture_output=True, timeout=30)
    if res.returncode:
        raise NetworkError('Git could not read this checkout: '+res.stderr.decode(errors='replace')[-300:])
    return res.stdout


def checkout_changes(raw):
    """Describe staged and working-tree changes, preserving renamed and unusual file names."""
    records = iter(raw.split(b'\0'))
    changes = []
    labels = {'M': 'modified', 'A': 'added', 'D': 'deleted', 'R': 'renamed',
              'C': 'copied', 'T': 'type changed', 'U': 'conflicted'}
    for record in records:
        if not record:
            continue
        code = record[:2].decode('ascii', errors='replace')
        name = json.dumps(record[3:].decode('utf-8', errors='replace'), ensure_ascii=True)
        if 'R' in code or 'C' in code:
            original = json.dumps(next(records).decode('utf-8', errors='replace'), ensure_ascii=True)
            name = f'{original} -> {name}'
        if code == '??':
            state = 'untracked'
        elif code in {'DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'}:
            state = 'conflicted'
        else:
            state = '; '.join(f"{labels.get(flag, 'changed')} ({area})"
                              for flag, area in zip(code, ('staged', 'unstaged')) if flag != ' ')
        changes.append(f'  {state}: {name}')
    return changes


def checkout(folder):
    """Require a clean checkout root before starting or publishing verified work."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir() or Path(git(root,'rev-parse','--show-toplevel').decode().strip()).resolve() != root:
        raise NetworkError('Choose the root folder of a local Git checkout.')
    raw = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    if raw:
        changes = checkout_changes(raw)
        # Large autosaved projects can have thousands of dirty files; keep the error readable.
        shown = changes[:MAX_CHECKOUT_CHANGES]
        count = f"{len(changes)} path{'s' if len(changes) != 1 else ''}"
        location = json.dumps(str(root), ensure_ascii=True)
        details = f'\n\nExisting changes in {location} ({count}):\n' + '\n'.join(shown)
        if len(changes) > len(shown):
            details += f'\n  ... and {len(changes) - len(shown)} more. Open this checkout in Git to see all changes.'
        raise NetworkError(f'Commit or move the checkout’s existing changes ({count}) before syncing. '
                           'Nothing was overwritten.' + details)
    return root


def project_checkout(proj, cfg):
    """Recover the checkout bound at import, including a progress save inside a repository."""
    for folder in (cfg.get('progress_folder'), dict(proj).get('root')):
        if not folder:
            continue
        path = Path(folder).expanduser()
        if not path.is_dir():
            continue
        for parent in (path, *path.parents):
            if (parent / '.git').is_dir() or (parent / '.git').is_file():
                return str(parent)
    return ''


def status(net, slug):
    """Prefer an explicit export destination, otherwise show the project's existing checkout."""
    proj = net.project_row(slug)
    cfg = net.cfg(proj)
    folder = cfg.get('repository_sync_folder') or project_checkout(proj, cfg)
    message = ''
    if not net.is_rom(proj):
        message = 'Imported projects already keep their source in their own checkout.'
    elif not cfg.get('repository_sync_folder') and folder and cfg.get('progress_folder') and not (Path(folder) / 'build.json').is_file():
        message = ('This checkout already receives saved progress automatically. '
                   'Verified export sync needs a rebuildable export checkout.')
    return {'folder': folder, 'supported': net.is_rom(proj), 'message': message}


def copy_checkout(root, stage):
    """Stage tracked files and private build inputs without linking back to writable originals."""
    # Copy tracked files and the private inputs a generated build needs. Never hard-link writable files.
    paths = git(root,'ls-files','-z').decode().split('\0')
    for name in filter(None, paths):
        source = inside(root,name)
        if not source.is_file():
            raise NetworkError('Sync needs a checkout with all tracked files present.')
        target = inside(stage,name);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for name in ('asm','assets'):
        source = root/name
        if source.exists():
            for item in source.rglob('*'):
                inside(root,str(item.relative_to(root)))
            shutil.copytree(source,stage/name,dirs_exist_ok=True)


def source_text(source, old, new):
    """Rename only identifier tokens, retaining literals and formatting used by period compilers."""
    # Only replace the function's identifier tokens; preserve literal contents and line numbers.
    tokens = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*',re.S)
    return tokens.sub(lambda m:new if m.group()==old else m.group(),source)


def integrate(net, proj, root, stage, log):
    """Check additions independently; preserve curated C and update only files owned by a prior sync."""
    manifest_path = stage/'build.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    cfg = net.cfg(proj)
    # A compressed cartridge's export hashes its expanded analysis image separately.
    # Identity belongs to the original cartridge; both inputs must still verify below.
    identity = manifest.get('cartridge', {}).get('sha1', manifest.get('sha1'))
    if not identity or identity != (cfg.get('rom') or {}).get('sha1'):
        raise NetworkError('The repository and project must use the same game fingerprint.')
    if not isinstance(manifest.get('functions'),list) or not (stage/'build.py').is_file() or any(
            not isinstance(fn,dict) or not {'name','seg','vram'} <= fn.keys() for fn in manifest.get('functions',[])):
        raise NetworkError('This checkout needs a decompdecomp build.json/build.py export. Other build layouts are not supported yet.')
    image = inside(root,manifest['image'])
    if not image.is_file() or hashlib.sha1(image.read_bytes()).hexdigest() != manifest['sha1']:
        raise NetworkError('Prepare this checkout with its verified game file before syncing.')
    for name in [manifest['image'], *(([manifest['cartridge']['image']]) if manifest.get('cartridge') else [])]:
        if name == manifest.get('cartridge', {}).get('image'):
            if hashlib.sha1(inside(root,name).read_bytes()).hexdigest() != identity:
                raise NetworkError('The original cartridge checksum differs. Restore the verified original before syncing.')
        src=inside(root,name);dst=inside(stage,name);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    previous = json.loads((stage/STATE).read_text(encoding='utf-8')) if (stage/STATE).exists() else {'owned':{}}
    owned = previous.get('owned',{})
    functions = {f['vram']:f for f in manifest['functions']}
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL ORDER BY vram",proj['id'])
    skipped=[];accepted=[];files=set();rollbacks={}
    for index,row in enumerate(rows):
        progress=getattr(log,'progress',None)
        if progress:progress({'phase':'Checking matched C','finished':index,'total':len(rows),'accepted':len(accepted)})
        fn=functions.get(row['vram'])
        if not fn:
            skipped.append({'function':row['name'],'reason':'No corresponding repository function boundary.'});continue
        relative=fn.get('c') or f"src/{fn['seg']}/{fn['name']}.c"
        if not relative.startswith('src/') or not relative.endswith(('.c','.cpp')):
            raise NetworkError('Unsupported source layout: sync only manages source files under src/.')
        target=inside(stage,relative)
        if target.exists() and relative not in owned:
            continue  # a hand-maintained checkpoint is not ours to replace
        if relative in owned and digest(inside(root,relative)) != owned[relative]:
            owned.pop(relative)
            skipped.append({'function':row['name'],'reason':'Preserved committed manual edits; this file is no longer managed by sync.'})
            continue
        # Unchanged managed files need no new compiler run. Reuse only a header
        # actually present in this checkout, compared byte-for-byte with current or
        # recorded exact-attempt context; transformed candidates are checked below.
        from .function_names import canonical_source
        from .repository_candidates import contexts
        unchanged=False
        if target.exists():
            for h in contexts(net,proj,row):
                ctx=source_text(toolchains.source_prelude(toolchains.get(compiler_for(cfg,row['vram'], row)['toolchain']),row['matched_source']+'\n'+h)+h,row['name'],fn['name'])
                hn='decompdecomp_sync_'+hashlib.sha256(ctx.encode()).hexdigest()[:20]+'.h'
                original=source_text(canonical_source(row,row['matched_source']),row['name'],fn['name'])
                original=re.sub(r'(?m)^([ \t]*#[ \t]*include[ \t]*)"context\.h"',lambda m,hn=hn:m[1]+'"'+hn+'"',original)
                if target.read_text(encoding='utf-8')==f'#include "{hn}"\n#line 1 "func.c"\n'+original+'\n':
                    unchanged=True;break
        if unchanged:continue
        profile=compiler_for(cfg,row['vram'], row)
        from .repository_candidates import select
        prepared,reason=select(net,proj,row)
        if prepared is None:
            skipped.append({'function':row['name'],'reason':reason});continue
        candidate,private_header,entry_only=prepared
        if entry_only and 'def normalize_entry(' not in (stage/'build.py').read_text(encoding='utf-8'):
            skipped.append({'function':row['name'],'reason':'This exact candidate needs verified-entry extraction. Update the exported build.py before syncing non-contiguous constant pools.'});continue
        context=source_text(toolchains.source_prelude(toolchains.get(profile['toolchain']),candidate+'\n'+private_header)+private_header,row['name'],fn['name'])
        header='include/decompdecomp_sync_'+hashlib.sha256(context.encode()).hexdigest()[:20]+'.h'
        source=source_text(candidate,row['name'],fn['name'])
        source=re.sub(r'(?m)^([ \t]*#[ \t]*include[ \t]*)"context\.h"',lambda m,header=header:m[1]+'"'+Path(header).name+'"',source)
        rendered=f'#include "{Path(header).name}"\n#line 1 "func.c"\n'+source+'\n'
        if target.exists() and target.read_text(encoding='utf-8')==rendered:
            continue
        existing_header=inside(stage,header)
        if existing_header.exists() and existing_header.read_text(encoding='utf-8')!=context:
            skipped.append({'function':row['name'],'reason':'Preserved manual edits to a sync header. Rename the edited header or restore its original contents before updating this source.'})
            continue
        before=manifest_path.read_bytes();old_source=target.read_bytes() if target.exists() else None
        inside(stage,header).parent.mkdir(parents=True,exist_ok=True);inside(stage,header).write_text(context,encoding='utf-8')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(rendered,encoding='utf-8')
        trial=json.loads(before)
        scoped=fn.get('rom') is not None and 'function_of =' in (stage/'build.py').read_text(encoding='utf-8')
        from ..project import function_compiler_key
        profile_key=function_compiler_key(fn)
        old_profile=trial.get('function_compilers',{}).get(profile_key)
        if scoped:
            trial.setdefault('function_compilers',{})[profile_key]={**profile,'vram':fn['vram'],'function':fn['name']}
        else:
            trial.setdefault('compiler_ranges',[]).insert(0,{**profile,'start':row['vram'],'end':row['vram']+1})
        for symbol in net.store.all('SELECT name,address FROM symbols WHERE project_id=?',proj['id']):
            trial['symbols'].setdefault(symbol['name'],symbol['address'])
        for symbol in net.store.all('SELECT name,vram FROM functions WHERE project_id=?',proj['id']):
            trial['symbols'].setdefault(symbol['name'],symbol['vram'])
        manifest_path.write_text(json.dumps(trial,indent=1)+'\n',encoding='utf-8')
        log('Checking '+row['name']+' in the repository build…')
        code,text=command(stage,[hostenv.console_python(),'build.py','check',fn['name']],log)
        if code:
            manifest_path.write_bytes(before)
            if old_source is None:target.unlink()
            else:target.write_bytes(old_source)
            skipped.append({'function':row['name'],'reason':text[-1200:]});log(row['name']+': kept the repository version; standalone check failed.');continue
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        rollbacks[fn['name']]={'path':relative,'function':dict(fn),'range':None if scoped else trial['compiler_ranges'][0],
                              'profile_key':profile_key if scoped else None,'old_profile':old_profile,'app_name':row['name']}
        files.update((relative,header));accepted.append(row['name'])
    return manifest,owned,files,accepted,skipped,rollbacks


def run(net, slug, folder, log=print, *, commit_push=False):
    """Stage, verify and publish commit-ready source additions; preserve the live checkout on failure."""
    if not _LOCK.acquire(blocking=False):
        raise NetworkError('Another repository sync is running. Wait for it to finish.')
    try:
        proj=net.project_row(slug)
        if not net.is_rom(proj):
            raise NetworkError('This project already works in its checkout. Commit its source there; no export sync is needed.')
        root=checkout(folder);head=git(root,'rev-parse','HEAD')
        upstream = None
        if commit_push:
            from . import repository_git
            upstream = repository_git.prepare(root, log)
        if not (root/'build.json').is_file() or not (root/'build.py').is_file():
            raise NetworkError('Choose an existing decompdecomp build.json/build.py export checkout. No files were changed.')
        baseline={p: digest(inside(root,p)) for p in filter(None,git(root,'ls-files','-z').decode().split('\0'))}
        with tempfile.TemporaryDirectory(prefix='decomp-repository-sync-') as temp:
            stage=Path(temp);log('Copying checkout into a private verification folder…');copy_checkout(root,stage)
            manifest,owned,files,accepted,skipped,rollbacks=integrate(net,proj,root,stage,log)
            if accepted:
                manifest=repository_sync_build.verify(stage,root,manifest,files,accepted,skipped,rollbacks,command,inside,log)
                repository_sync_build.remove_unused_headers(stage,files)
                if accepted:
                    files.add('build.json')
                    update_objdiff(stage,manifest);files.update({'objdiff.json'} if (stage/'objdiff.json').exists() else set())
            for name in files:
                if name.startswith('src/'):
                    owned[name]=digest(inside(stage,name))
            previous=json.loads((stage/STATE).read_text(encoding='utf-8')) if (stage/STATE).exists() else {}
            report={'format':1,'owned':owned,'accepted':sorted(set(previous.get('accepted',[])+accepted)),'skipped':skipped,
                    'verified_build':bool(accepted) or previous.get('verified_build',False),'matched_app':net.store.one("SELECT COUNT(*) n FROM functions WHERE project_id=? AND status='matched'",proj['id'])['n']}
            (stage/STATE).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');files.add(STATE)
            if commit_push:
                from . import saves
                log('Saving all portable progress, including drafts, notes and compiler settings…')
                destination = inside(stage, 'progress')
                snapshot, _ = saves._render(net, slug, destination, chosen=True)
                if (destination/'README.md').is_file():
                    snapshot['README.md'] = (destination/'README.md').read_text(encoding='utf-8')
                previous_save = saves._read_owned(inside(root, 'progress'))
                saves._publish(destination, snapshot, previous_save, chosen=True)
                files.update('progress/' + name for name in snapshot.keys() | previous_save.keys())
            # Recheck before writing: never overwrite work made while the long build ran.
            checkout(root)
            if git(root,'rev-parse','HEAD')!=head or any(digest(inside(root,p))!=v for p,v in baseline.items()):
                raise NetworkError('The checkout changed during verification. Retry after its edits finish.')
            progress=getattr(log,'progress',None)
            if progress:progress({'phase':'Publishing verified changes','finished':0,'total':1})
            publish(root,stage,files,baseline,log)
        net._update_cfg(slug,lambda cfg:cfg.update(repository_sync_folder=str(root)))
        changed=git(root,'status','--short','--untracked-files=all').decode()
        log('Sync complete. Review and commit these files:\n'+changed)
        result = {'folder':str(root),'accepted':accepted,'skipped':skipped,'changes':changed,'verified_build':bool(accepted)}
        if commit_push:
            result.update(repository_git.finish(root, files, upstream, log))
        return result
    finally:
        _LOCK.release()


def update_objdiff(stage, manifest):
    """Refresh existing unit metadata from the successfully linked source selections."""
    path=stage/'objdiff.json'
    if not path.exists():return
    data=json.loads(path.read_text(encoding='utf-8'));functions={f"{f['seg']}/{f['name']}":f for f in manifest['functions']}
    for unit in data.get('units',[]):
        fn=functions.get(unit.get('name'))
        if not fn:continue
        source=fn.get('c') or f"src/{fn['seg']}/{fn['name']}.c"
        if inside(stage,source).is_file():
            unit.setdefault('metadata',{}).update(complete=True,source_path=source)
            unit['base_path']='build/'+str(Path(source).with_suffix('.o')).replace('\\','/')
    path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')


def publish(root,stage,files,baseline,log):
    """Publish owned files atomically and roll back earlier writes if another destination changed."""
    backups={};written=[]
    try:
        for name in sorted(files):
            check=getattr(log,'check_cancel',None)
            if check:check()
            target=inside(root,name)
            expected=baseline.get(name)
            if digest(target)!=expected:
                raise NetworkError('A sync destination changed during publication. Its edits were preserved.')
            backups[name]=target.read_bytes() if target.exists() else None
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as temp:
                source = inside(stage,name)
                if source.exists():temp.write(source.read_bytes())
                temporary=Path(temp.name)
            if source.exists():os.replace(temporary,target)
            else:
                temporary.unlink();target.unlink(missing_ok=True)
            written.append(name)
    except BaseException:
        for name in reversed(written):
            target=inside(root,name)
            if digest(target)!=digest(inside(stage,name)):continue
            if backups[name] is None:target.unlink()
            else:target.write_bytes(backups[name])
        raise

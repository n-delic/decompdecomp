"""Private ROM snapshot and complete-image verification for an atomic return repair."""
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

from ..network import NetworkError
from .purpose_work import Executor

FUNCTION_COLUMNS = ('id','project_id','name','vram','rom_offset','size','n_insns','target','relocs','asm',
                    'unit','best_source','best_score','matched_source','status','meta')


def snapshot(net, proj):
    store = net.project_store(proj['id'])
    functions = [dict(r) for r in store.all('SELECT '+','.join(FUNCTION_COLUMNS)+' FROM functions WHERE project_id=? ORDER BY id',proj['id'])]
    contexts = {unit:dict(ctx) for unit in sorted({''}|net._unit_contexts(proj['id']))
                if (ctx:=net._context(proj['id'],unit)) is not None}
    contexts.setdefault('',{'project_id':proj['id'],'version':0,'unit':'','header':''})
    symbols = [dict(r) for r in store.all('SELECT project_id,name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])]
    return dict(project=dict(proj),functions=functions,contexts=contexts,symbols=symbols)


def fingerprint(state):
    return hashlib.sha256(repr(state).encode()).hexdigest()


def input_files(proj):
    cfg=json.loads(proj['config']);root=Path(proj['root']).resolve()
    files=list(root.glob('*.yaml'))
    for name in {cfg['rom']['file'],cfg['rom'].get('program',cfg['rom']['file']),'reloc_addrs.txt'}:
        file=(root/name).resolve()
        if not file.is_relative_to(root):raise NetworkError('External ROM inputs require a manual return repair review.')
        if file.is_file():files.append(file)
    if not list(root.glob('*.yaml')):raise NetworkError('Automatic return repairs need a rebuildable project with its splat configuration.')
    if any(not file.resolve().is_relative_to(root) for file in files):
        raise NetworkError('External layout inputs require a manual return repair review.')
    required={cfg['rom']['file'],cfg['rom'].get('program',cfg['rom']['file'])}
    if any(not (root/name).is_file() for name in required):
        raise NetworkError('Supply the original ROM before applying return repairs.')
    result={str(file.relative_to(root)):file.read_bytes() for file in sorted(set(files))}
    def relative(value):
        if isinstance(value,str):
            if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
                    or '..' in value.replace('\\','/').split('/') or value.startswith('~')):
                raise NetworkError('Layout paths must stay inside the isolated build; review this project manually.')
        elif isinstance(value,dict):
            for key,item in value.items():relative(key);relative(item)
        elif isinstance(value,list):
            for item in value:relative(item)
    for name,data in result.items():
        if name.endswith('.yaml'):
            try:relative(yaml.safe_load(data))
            except yaml.YAMLError as error:raise NetworkError('The layout configuration is unreadable.') from error
    return result


def verify(net, state, updates, files, directory, log):
    from ..rom.export import ExportError
    from .engine import RomNetwork
    home=Path(directory);root=home/'project';root.mkdir()
    for name,data in files.items():
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    staged=RomNetwork(home/'stage.db',home)
    proj=state['project'];pid=proj['id']
    cfg=json.loads(proj['config'])
    for key in ('progress_folder','save_folder','export','auto_commit_folder','auto_commit_matches','auto_push','autopilot'):
        cfg.pop(key,None)
    began=None
    result={}
    def failed(error):
        if began is not None:
            error.verification=dict(rebuild_verified=False,rebuild_seconds=time.monotonic()-began,
                                    judge_compiles=len(updates),matched_source_rechecks=result.get('matched',0))
        return error
    compiles=Executor(purpose='Repair verification build', project=proj['slug'], max_workers=4)
    try:
        with staged.store.tx() as db:
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)',
                       (pid,proj['slug'],proj['name'],proj['arch'],proj['big_endian'],str(root),json.dumps(cfg)))
            for row in state['functions']:
                db.execute('INSERT INTO functions('+','.join(FUNCTION_COLUMNS)+') VALUES('+','.join('?' for _ in FUNCTION_COLUMNS)+')',tuple(row[c] for c in FUNCTION_COLUMNS))
            for row in state['symbols']:
                db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',tuple(row.values()))
            # Keep per-unit headers independent. Copy only current contexts, not game history.
            for row in {r['version']:r for r in state['contexts'].values()}.values():
                db.execute('INSERT INTO context_versions(project_id,version,unit,header) VALUES(?,?,?,?)',
                           (pid,row['version'],row['unit'],row['header']))
            version=max(r['version'] for r in state['contexts'].values())
            for item in updates:
                row=item['function'];version+=1;unit='return_repair_'+str(row['id'])
                db.execute('INSERT INTO context_versions(project_id,version,unit,header) VALUES(?,?,?,?)',(pid,version,unit,item['header']))
                db.execute("UPDATE functions SET unit=?,status='matched',matched_source=?,best_source=?,best_score=0 WHERE id=?",(unit,item['source'],item['source'],row['id']))
        stage_proj=staged.project_row(proj['slug'])
        original_compile=staged._compile
        from .function_names import canonical_source
        prepared={}
        # Export revisits these immutable inputs. Compile at most four at once and reuse
        # their objects for both affected-function judging and full export rechecks.
        for row in staged.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' ORDER BY vram",pid):
            hdr=staged._context(pid,row['unit'])['header']
            body=canonical_source(row,row['matched_source'])
            prepared[(row['id'],hdr,body)]=compiles.submit(original_compile,stage_proj,hdr,body,row)
        rechecks=[0]
        def checked_compile(project,header,body,row=None):
            log.check_cancel()
            rechecks[0]+=1
            if rechecks[0]%50==0:log('Return repair: rechecked '+str(rechecks[0])+' saved C functions in the isolated build.')
            key=(row['id'],header,canonical_source(row,body)) if row is not None else None
            future=prepared.get(key)
            return future.result() if future is not None else original_compile(project,header,body,row)
        staged._compile=checked_compile
        for item in updates:
            log.check_cancel()
            row=staged._function(item['function']['name'],proj['slug'])
            compiled=staged._compile(stage_proj,item['header'],item['source'],row)
            _,verdict,_=staged._evaluate(row,stage_proj,compiled.obj) if compiled.ok else (None,False,None)
            if verdict is not True:raise NetworkError(row['name']+': staged return repair failed the ordinary byte/data judge.')
        log.check_cancel()
        log('Return repair: rebuilding the isolated project to verify the complete image.')
        output=home/'export'
        began=time.monotonic()
        result=staged.console(stage_proj).export_project(staged,proj['slug'],output,log=log)
        log.check_cancel()
        if not result.get('identical'):raise NetworkError('The staged return repair failed its complete rebuild; saved C is unchanged.\n'+result.get('log','')[-1200:])
        if result.get('fallback'):
            raise NetworkError('An existing matched source fell back to assembly; the return repair was not applied.')
        manifest=json.loads((output/'build.json').read_text())
        expected_names={r['name'] for r in state['functions'] if r['status']=='matched'}|{u['function']['name'] for u in updates}
        compiled_names={f['name'] for f in manifest['functions'] if f.get('c')}
        if not expected_names.issubset(compiled_names):
            raise NetworkError('The staged rebuild omitted matched C; the return repair was not applied.')
        reference=manifest.get('cartridge',{}).get('image',manifest['image'])
        expected=manifest.get('cartridge',{}).get('sha1',manifest['sha1'])
        def inside(name):
            path=(output/name).resolve()
            if not path.is_relative_to(output.resolve()):raise NetworkError('Rebuild output is outside the staging directory.')
            return path
        rebuilt=inside(manifest['output']).read_bytes()
        original=files[cfg['rom'].get('program',cfg['rom']['file'])]
        if (rebuilt!=original or inside(reference).read_bytes()!=original
                or hashlib.sha1(rebuilt).hexdigest()!=expected):
            raise NetworkError('The staged rebuilt image differs; saved C is unchanged.')
        return dict(image_sha256=hashlib.sha256(rebuilt).hexdigest(),image_bytes=len(rebuilt),verified_functions=len(updates),judge_compiles=len(updates),
                    rebuild_verified=True,rebuild_seconds=time.monotonic()-began,matched_source_rechecks=result['matched'])
    except NetworkError as error:
        raise failed(error)
    except (OSError, subprocess.SubprocessError, ExportError) as error:
        raise failed(NetworkError('The staged return rebuild could not finish: '+str(error))) from error
    finally:
        compiles.shutdown(wait=True,cancel_futures=True)
        staged._pool.shutdown(wait=True)
        reader=getattr(staged.store._local,'reader',None)
        if reader is not None:reader.conn.close()
        staged.store.conn.close()
        # No files in the user's project or checkout were written.
        shutil.rmtree(root,ignore_errors=True)

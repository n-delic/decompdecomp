"""Verify complete exported builds, retaining candidates only if the assembled image is unchanged."""
import hashlib
import json
import re
from pathlib import Path

from .. import hostenv
from ..network import NetworkError

CACHED_BUILD = '''import build
from pathlib import Path
original=build.build_objects
def cached(todo):
    original([item for item in todo if item[0]!='c' or not (build.ROOT/item[2]).is_file()
              or (build.ROOT/item[2]).stat().st_mtime < (build.ROOT/item[1]).stat().st_mtime])
build.build_objects=cached
raise SystemExit(0 if build.build() else 1)
'''


def verify(stage, root, manifest, files, accepted, skipped, rollbacks, command, inside, log):
    """A reported failing addition can revert independently; unrelated failures leave the whole checkout alone."""
    retry=False
    while accepted:
        progress=getattr(log,'progress',None)
        if progress:progress({'phase':'Verifying complete build','finished':0,'total':1})
        log('Verifying the complete repository build; this may take several minutes…')
        script=(stage/'build.py').read_text(encoding='utf-8')
        cached=retry and 'def build_objects(' in script and 'def build(' in script
        argv=[hostenv.console_python(),'-c',CACHED_BUILD] if cached else [hostenv.console_python(),'build.py']
        code,text=command(stage,argv,log)
        if not code:
            original=manifest.get('cartridge',{}).get('image',manifest['image'])
            output=inside(stage,manifest['output']).read_bytes()
            expected=manifest.get('cartridge',{}).get('sha1',manifest['sha1'])
            if hashlib.sha1(output).hexdigest()!=expected or output!=inside(stage,original).read_bytes():
                raise NetworkError('The rebuilt image differs from the original. The checkout was not changed.')
            return manifest
        hit=re.search(r'in or after ([^);\n]+)',text)
        failed=rollbacks.pop(hit[1],None) if hit else None
        if not failed:
            raise NetworkError('Full repository build failed. The checkout was not changed.\n'+text)
        relative=failed['path'];target=inside(stage,relative);old=inside(root,relative)
        if old.is_file():target.write_bytes(old.read_bytes())
        else:target.unlink(missing_ok=True)
        manifest=json.loads((stage/'build.json').read_text(encoding='utf-8'))
        manifest['functions']=[failed['function'] if f['name']==failed['function']['name'] else f for f in manifest['functions']]
        for index,value in enumerate(manifest.get('compiler_ranges',[])):
            if value==failed['range']:
                manifest['compiler_ranges'].pop(index);break
        if failed.get('profile_key') is not None:
            profiles=manifest.setdefault('function_compilers',{})
            if failed.get('old_profile') is None:profiles.pop(failed['profile_key'],None)
            else:profiles[failed['profile_key']]=failed['old_profile']
        (stage/'build.json').write_text(json.dumps(manifest,indent=1)+'\n',encoding='utf-8')
        files.discard(relative);accepted.remove(failed['app_name'])
        skipped.append({'function':failed['app_name'],'reason':'Individual check passed, but the full rebuilt image differed. Repository source retained. '+text[-800:]})
        log(failed['app_name']+': full-image verification rejected the addition; retrying the remaining candidates.')
        retry=True
    # No additions survived. Keep only the review report; the original checkout remains the checkpoint.
    files.clear()
    return json.loads((root/'build.json').read_text(encoding='utf-8'))


def remove_unused_headers(stage, files):
    """Do not publish a generated header when every candidate using it was rejected."""
    sources=[(stage/name).read_text(encoding='utf-8') for name in files if name.startswith('src/')]
    for name in list(files):
        if name.startswith('include/') and not any('"'+Path(name).name+'"' in source for source in sources):
            files.remove(name)

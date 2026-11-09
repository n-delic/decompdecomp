"""User-started ROM exploration, saved locally without changing build inputs or progress.

Project-mode adapters need original-image/section mappings first (ROADMAP.md).
Scans never run as routine maintenance or merely because a project is saved.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import re
import time
from pathlib import Path

from ..network import IN_BUILD, NetworkError
from ..rom.explorer import analyse

PREFIX = 'rom-explorer:'
KINDS=('', 'unknown', 'code', 'data', 'strings', 'pointer table', 'compressed', 'image', 'palette', 'audio', 'font', 'container', 'repeated bytes')


def _project(net, slug):
    proj = net.project_row(slug)
    if not net.is_rom(proj):
        raise NetworkError('ROM Explorer needs an imported ROM image. Object-build projects need an original-image mapping adapter first.')
    return proj


def _revision(net, proj):
    # Layout and symbols can change without changing the ROM bytes. Old reports
    # remain visible as stale, and cannot supply previews or accept annotations.
    store = net.project_store(proj['id'])
    rows = store.all('SELECT name,vram,rom_offset,size,unit,relocs FROM functions WHERE project_id=? ORDER BY id',proj['id'])
    syms = store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])
    cfg=net.cfg(proj)
    stat=(Path(proj['root'])/cfg['rom']['file']).stat()
    layout={key:cfg.get(key) for key in ('rom','segments','platform','big_endian')}
    return hashlib.sha256(json.dumps([layout,proj['big_endian'],(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns),[tuple(r) for r in rows],[tuple(r) for r in syms]],sort_keys=True).encode()).hexdigest()


def _read(net, proj):
    row = net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?',PREFIX+str(proj['id']))
    return json.loads(row['value']) if row else {'report':None,'annotations':{}}


def _write(net, proj, state):
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(proj['id']),json.dumps(state)))


def status(net, slug, jobs):
    """Discover scoped work before reading a potentially large saved report."""
    proj = net.project_row(slug)
    with jobs._lock:
        job = next((j for j in reversed(list(jobs.jobs.values())) if j['kind']=='rom-explorer' and j.get('activity_project')==slug),None)
        snapshot = {k:job[k] for k in ('id','state','error','progress') if k in job} if job else None
    return dict(supported=net.is_rom(proj),job=snapshot)


def listing(net, slug):
    """Read the saved byte map with current-layout staleness and local annotations."""
    proj = _project(net,slug)
    state = _read(net,proj)
    report = state['report']
    if not report:return dict(available=False,regions=[])
    # Keep large reference arrays and previews out of the table response.
    regions = [{k:v for k,v in r.items() if k not in ('references','pointers')} |
               {'annotation':state['annotations'].get(r['id'],{}),'used_by':len({v['function'] for v in r['references']}),'function_refs':sorted({v['function'] for v in r['references']})}
               for r in report['regions']]
    return {k:v for k,v in report.items() if k!='regions'} | dict(available=True,stale=report['revision']!=_revision(net,proj),regions=regions)


def run(net, slug, log):
    """Freeze a read-only analysis snapshot without synchronizing saved game work."""
    with net.progress.view():
        return _run(net,slug,log)


def _run(net, slug, log):
    proj = _project(net,slug)
    cfg = net.cfg(proj)
    revision = _revision(net,proj)
    log('Reading the analysis image and imported mappings. No compiler or AI calls are made.')
    path = Path(proj['root'])/cfg['rom']['file']
    store = net.project_store(proj['id'])
    functions = [dict(r) for r in store.all(f'SELECT name,vram,rom_offset,size,unit,relocs FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id'])]
    for f in functions:f['relocs'] = json.loads(f['relocs'] or '[]')
    symbols = [dict(r) for r in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=?',proj['id'])]
    if not path.stat().st_size:raise NetworkError('The analysis image is empty. Restore it or reimport before exploring.')
    with path.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        # ELF flattening and unpacked code produce a different analysis image.
        # rom.sha1 identifies the imported program, not those derived bytes.
        same_image=cfg['rom'].get('program',cfg['rom']['file'])==cfg['rom']['file']
        if same_image and cfg['rom'].get('sha1') and hashlib.sha1(image).hexdigest()!=cfg['rom']['sha1']:
            raise NetworkError('The analysis image differs from the import. Restore it or reimport before exploring.')
        report = analyse(image,cfg['segments'],functions,symbols,big_endian=bool(proj['big_endian']),
                         progress=log.progress,check=log.check_cancel)
    log.check_cancel()
    if revision!=_revision(net,proj):raise NetworkError('The project layout changed during the scan. Scan again to use the current mappings.')
    report.update(revision=revision,scanned_at=time.time(),image_name=path.name,
                  derived=bool(cfg['rom'].get('program') and cfg['rom']['program']!=cfg['rom']['file']))
    if report['derived']:report['note']='This is a derived analysis image (for example a flattened executable or expanded code), not physical-media offsets. Its hash freezes this scan; it does not certify a reconstruction of the original file. '+report['note']
    # Merge notes only at publication, so edits made during a rescan survive.
    with store.tx():
        state = _read(net,proj)
        prior = state['report']
        if not prior or prior['image_hash']!=report['image_hash']:state['annotations'] = {}
        state['report'] = report
        _write(net,proj,state)
    log(f"Mapped {report['size']:,} bytes into {len(report['regions']):,} regions. Saved labels and notes remain separate from code progress.")
    return dict(regions=len(report['regions']),size=report['size'])


def _current(net, slug, region_id, image_hash):
    proj = _project(net,slug)
    state = _read(net,proj)
    report = state['report']
    if not report or report['image_hash']!=image_hash or report['revision']!=_revision(net,proj):
        raise NetworkError('This ROM map changed. Scan or reload it before inspecting or saving notes.')
    row = next((r for r in report['regions'] if r['id']==region_id),None)
    if row is None:raise NetworkError('Choose a region from the current ROM map.')
    return proj,state,row


def detail(net, slug, region_id, image_hash):
    """Inspect a current region; previews never come from a stale engine image cache."""
    proj,state,row = _current(net,slug,region_id,image_hash)
    path = Path(proj['root'])/net.cfg(proj)['rom']['file']
    # Verify the on-disk image, not a potentially stale engine cache.
    lo=row['start']
    with path.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        if hashlib.sha256(image).hexdigest()!=image_hash:
            raise NetworkError('The image changed after this scan. Restore it or reimport and scan again.')
        sample = image[lo:min(row['end'],lo+256)]
        text_sample=image[lo:min(row['end'],lo+4096)]
    lines = [f'{lo+i:08X}  '+sample[i:i+16].hex(' ').ljust(47)+'  '+''.join(chr(v) if 32<=v<127 else '.' for v in sample[i:i+16]) for i in range(0,len(sample),16)]
    # A chain is only imported function -> table region -> sampled pointer word.
    # Do not invent loader/decompressor semantics from a suggestive function name.
    return dict(row,annotation=state['annotations'].get(region_id,{}),preview='\n'.join(lines),
                strings=[m.group()[:-1].decode('ascii')[:160] for m in re.finditer(rb'[\x20-\x7e]{8,}\x00',text_sample)][:20],
                image_hash=image_hash)


def annotate(net, slug, data):
    """Save a human interpretation separately from symbols and automatic evidence."""
    name,notes = data.get('name',''),data.get('notes','')
    kind=data.get('kind','')
    if not isinstance(kind,str) or kind not in KINDS:raise NetworkError('Choose a listed region type or keep the automatic label.')
    if not isinstance(name,str) or not isinstance(notes,str) or len(name)>120 or len(notes)>4000:
        raise NetworkError('Use a label up to 120 characters and notes up to 4,000 characters.')
    proj = _project(net,slug)
    with net.project_store(proj['id']).tx():
        proj,state,row = _current(net,slug,data.get('region'),data.get('image_hash'))
        state['annotations'][row['id']] = dict(name=name.strip(),notes=notes.strip(),kind=kind,updated_at=time.time())
        _write(net,proj,state)
    return dict(saved=True,annotation=state['annotations'][row['id']])

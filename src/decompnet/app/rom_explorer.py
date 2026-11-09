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

from .. import arch
from ..network import IN_BUILD, NetworkError
from ..rom.explorer import analyse

PREFIX = 'rom-explorer:'
CONTRACT_PREFIX='rom-explorer-contracts:'
ANALYSIS_REVISION=2
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
    return hashlib.sha256(json.dumps([ANALYSIS_REVISION,layout,proj['big_endian'],(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns),[tuple(r) for r in rows],[tuple(r) for r in syms],_contracts(net,proj)],sort_keys=True).encode()).hexdigest()


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
    flow=report.get('loader_flow',{'chains':[]})
    users={}
    for row in report['regions']:
        direct={ref['function'] for ref in row['references']}
        traced=set()
        for identity in row.get('loader_chains',[]):
            chain=flow['chains'][int(identity)]
            traced.add(chain['function'])
            traced.update(step['callee'] for step in chain['steps'] if step.get('callee_known'))
        users[row['id']]=(direct,traced-direct)
    regions = [{k:v for k,v in r.items() if k not in ('references','pointers')} |
               {'annotation':state['annotations'].get(r['id'],{}),'used_by':len(users[r['id']][0] | users[r['id']][1]),
                'direct_users':len(users[r['id']][0]),'traced_users':len(users[r['id']][1]),
                'function_refs':sorted(users[r['id']][0] | users[r['id']][1])}
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
    previous=_read(net,proj)['report']
    if previous and previous.get('revision')==revision and 'loader_flow' in previous:
        log.check_cancel();log('ROM image and analysis inputs are unchanged; using the current saved map.')
        log.progress(dict(phase='ROM map is current',done=1,total=1))
        return dict(regions=len(previous['regions']),size=previous['size'],cached=True)
    log('Reading the analysis image and imported mappings. No compiler or AI calls are made.')
    path = Path(proj['root'])/cfg['rom']['file']
    store = net.project_store(proj['id'])
    functions = [dict(r) for r in store.all(f'SELECT name,vram,rom_offset,size,unit,relocs FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id'])]
    for f in functions:f['relocs'] = json.loads(f['relocs'] or '[]')
    symbols = [dict(r) for r in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=?',proj['id'])]
    if not path.stat().st_size:raise NetworkError('The analysis image is empty. Restore it or reimport before exploring.')
    with path.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        from .analysis_image import verify
        verify(image,proj,cfg,check=log.check_cancel)
        from ..rom import asset_flow
        console=net.console(proj)
        flow=asset_flow.trace(image,cfg['segments'],functions,arch.get(proj['arch']).asset_flow,
                              contracts=console.asset_calls | _contracts(net,proj),argument_count=console.asset_argument_count,
                              big_endian=bool(proj['big_endian']),gp=cfg.get('gp',0),cpu=console.cpu_variant,ram=console.ram,
                              check=log.check_cancel,progress=log.progress)
        report = analyse(image,cfg['segments'],functions,symbols,big_endian=bool(proj['big_endian']),
                         progress=log.progress,check=log.check_cancel,ranges=[c['source'] for c in flow['chains']])
        asset_flow.attach(report,flow)
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
                image_hash=image_hash,loader_flow={**state['report'].get('loader_flow',{}),
                    'chains':[state['report']['loader_flow']['chains'][int(i)] for i in row.get('loader_chains',[])]})


def annotate(net, slug, data):
    """Save a human interpretation separately from symbols and automatic evidence."""
    name,notes = data.get('name',''),data.get('notes','')
    kind=data.get('kind','')
    preview_settings=_preview_settings(data.get('preview_settings',{}))
    if not isinstance(kind,str) or kind not in KINDS:raise NetworkError('Choose a listed region type or keep the automatic label.')
    if not isinstance(name,str) or not isinstance(notes,str) or len(name)>120 or len(notes)>4000:
        raise NetworkError('Use a label up to 120 characters and notes up to 4,000 characters.')
    proj = _project(net,slug)
    with net.project_store(proj['id']).tx():
        proj,state,row = _current(net,slug,data.get('region'),data.get('image_hash'))
        state['annotations'][row['id']] = dict(name=name.strip(),notes=notes.strip(),kind=kind,preview_settings=preview_settings,updated_at=time.time())
        _write(net,proj,state)
    return dict(saved=True,annotation=state['annotations'][row['id']])


def _contracts(net,proj):
    row=net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?',CONTRACT_PREFIX+str(proj['id']))
    return json.loads(row['value']) if row else {}


def loader_contracts(net,slug,data=None):
    """Human-declared call roles are analysis hypotheses, never symbol or build edits."""
    from ..rom.asset_formats import integer
    proj=_project(net,slug);store=net.project_store(proj['id'])
    functions=store.all('SELECT name FROM functions WHERE project_id=? ORDER BY name',proj['id'])
    if data is None:return dict(items=_contracts(net,proj),functions=[r['name'] for r in functions])
    name=data.get('function')
    if not data.get('remove') and name not in {r['name'] for r in functions}:raise NetworkError('Choose an existing function for this loader role.')
    if not isinstance(name,str):raise NetworkError('Choose a loader role to save or remove.')
    with store.tx() as db:
        roles=_contracts(net,proj)
        if data.get('remove'):roles.pop(name,None)
        else:
            role=data.get('role');domain=data.get('domain')
            if role not in ('copy','dma','decompress') or domain not in ('rom','address'):
                raise NetworkError('Choose copy, DMA or decompression, and a ROM-offset or runtime-address source.')
            source=integer(data.get('source'),'Source argument',0,7)
            destination=integer(data.get('destination'),'Destination argument',0,7)
            length=integer(data.get('length'),'Length argument',0,7)
            if len({source,destination,length})!=3:raise NetworkError('Source, destination and byte length must use different arguments.')
            roles[name]=dict(role=role,domain=domain,source=source,destination=destination,length=length,basis='user')
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(CONTRACT_PREFIX+str(proj['id']),json.dumps(roles)))
    return dict(items=roles,functions=[r['name'] for r in functions])


def _preview_settings(options):
    from ..rom.asset_formats import FORMATS, PALETTES
    if not isinstance(options,dict):raise NetworkError('Preview settings must be named fields.')
    allowed={'format','offset','source','block_offset','width','height','palette_offset','palette_format','channels','rate','length'}
    if set(options)-allowed:raise NetworkError('Use only the listed preview controls.')
    if options.get('format','auto') not in FORMATS or options.get('palette_format','rgba5551-be') not in PALETTES:
        raise NetworkError('Choose a listed preview format and palette format.')
    if options.get('source','rom') not in ('rom','decoded'):raise NetworkError('Choose ROM bytes or a decompressed block.')
    if any(isinstance(v,(dict,list,bool)) or len(str(v))>40 for v in options.values()):raise NetworkError('Preview controls must contain short text or numeric values.')
    return options


def asset_preview(net,slug,data):
    """Serve a bounded local preview, preserving actionable errors at the API boundary."""
    from ..rom.asset_formats import AssetError
    try:return _asset_preview(net,slug,data)
    except AssetError as exc:raise NetworkError(str(exc)) from None


def _asset_preview(net,slug,data):
    """Serve a bounded local image/audio data URL, leaving the ROM and saved code alone."""
    from ..rom import asset_formats
    proj,state,row=_current(net,slug,data.get('region'),data.get('image_hash'))
    options=_preview_settings(data.get('options',{}))
    path=Path(proj['root'])/net.cfg(proj)['rom']['file']
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        if hashlib.sha256(image).hexdigest()!=data['image_hash']:raise NetworkError('The image changed. Reopen ROM Explorer before previewing assets.')
        offset=asset_formats.integer(options.get('offset',row['start']),'Data offset',0,len(image)-1) if options.get('source','rom')=='rom' else row['start']
        header=image[offset:offset+4]
        compressed=header in (b'Yaz0',b'Yay0',b'MIO0') or header.startswith(b'\x1f\x8b\x08') or len(header)>=2 and header[0]==0x78 and int.from_bytes(header[:2],'big')%31==0
        auto_decode=options.get('source','rom')=='rom' and options.get('format','auto')=='auto' and compressed
        if options.get('source','rom')=='decoded' or auto_decode:
            block=offset if auto_decode else asset_formats.integer(options.get('block_offset',row['start']),'Compressed block offset',0,len(image)-1)
            raw,codec=asset_formats.unpack(image[block:block+asset_formats.MAX_BYTES])
            selected={**options,'offset':0 if auto_decode else options.get('offset',0)}
            result=asset_formats.preview(raw,selected)
            result['decoded']=dict(codec=codec,bytes=len(raw),block_offset=block)
        else:
            result=asset_formats.preview(image,{**options,'offset':options.get('offset',row['start'])})
    result['settings']=options
    return result

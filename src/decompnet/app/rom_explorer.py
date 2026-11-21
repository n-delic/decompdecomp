"""User-started ROM exploration, saved locally without changing build inputs or progress.

A ROM project explores its analysis image. A forked decomp explores the user's own copy of the game inside it,
the file whose SHA-1 the import recorded, with the splat config's segments when that config splits exactly
this file (a fork's functions have no ROM offsets yet, so their references are not drawn). Forks whose
original is a disc or a folder still need an image adapter (ROADMAP.md).
Scans never run as routine maintenance or merely because a project is saved.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import re
import time
from importlib.metadata import version
from pathlib import Path

from ..network import IN_BUILD, NetworkError
from . import rom_scan

PREFIX = 'rom-explorer:'
CONTRACT_PREFIX='rom-explorer-contracts:'
ANALYSIS_REVISION=32
KINDS=('', 'unknown', 'code', 'data', 'strings', 'pointer table', 'compressed', 'image', '3d model', 'palette', 'audio', 'animation', 'collision', 'object placements', 'world grid', 'music sequence', 'demo', 'font', 'container', 'repeated bytes')


def _source(net, proj):
    """What ROM Explorer reads: the image's path, its code segments, $gp, processor and byte order, or None."""
    cfg=net.cfg(proj)
    console=net.console(proj)
    if net.is_rom(proj):
        return dict(path=Path(proj['root'])/cfg['rom']['file'],segments=cfg['segments'],gp=cfg.get('gp',0),
                    arch=proj['arch'],big_endian=bool(proj['big_endian']),fork=False)
    if cfg.get('kind')!='fork' or not console.arch:return None
    from ..forks.project import ForkError, ForkProject
    try:
        fp=ForkProject(Path(proj['root']),create_state=False)
        try:need=fp.needs(cfg.get('version'))
        except ForkError:need={}
        known={h.lower() for h in (need.get('sha1'),cfg.get('game_sha1')) if isinstance(h,str)}
        image=fp.original_image(cfg.get('version'),known,console.image_ext)
        if image is None:return None
        config=fp.splat_config_for(image,cfg.get('version'))
        segments,gp=fp.segments_for(image,cfg.get('version'),config) if config else ([],0)
    except (OSError,ForkError):return None
    from ..rom import config_images
    # The images the project's own config declares (its textures, where it has worked them out).
    images=(config_images.images(config) if config else [])+config_images.asset_list_images(fp.root,cfg.get('version'))
    return dict(path=image,segments=segments,gp=gp,arch=console.arch,big_endian=console.big_endian,fork=True,images=images,
                sheets=config_images.sheets(config) if config else [])


def _project(net, slug):
    proj = net.project_row(slug)
    if _source(net,proj) is None:
        if net.cfg(proj).get('kind')=='fork':
            raise NetworkError('ROM Explorer reads your own copy of the game, and none was found in this project with the SHA-1 '
                               'recorded when it was imported. Put the game file the project asks for back in its folder, then open ROM Explorer again.')
        raise NetworkError('ROM Explorer needs an imported ROM image. Object-build projects need an original-image mapping adapter first.')
    return proj


def _revision(net, proj):
    # Layout and symbols can change without changing the ROM bytes. Old reports
    # remain visible as stale, and cannot supply previews or accept annotations.
    store = net.project_store(proj['id'])
    source=_source(net,proj)
    # A fork's functions carry no ROM offsets, so its own matching work never makes the map stale.
    # A ROM project's revision is computed as it always was, so its saved maps stay current.
    rows = store.all('SELECT name,vram,rom_offset,size,unit,relocs FROM functions WHERE project_id=? '+('AND rom_offset IS NOT NULL ' if source['fork'] else '')+'ORDER BY id',proj['id'])
    syms = store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])
    cfg=net.cfg(proj)
    stat=source['path'].stat()
    layout={key:cfg.get(key) for key in ('rom','segments','platform','big_endian','gp')}
    if source['fork']:layout['source']=[str(source['path']),source['segments'],source['gp'],source['images'],source['sheets']]
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
    return dict(supported=_source(net,proj) is not None,job=snapshot)


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
        traced.update(ref['function'] for ref in (row.get('detected_asset') or {}).get('loader_refs',[]))
        users[row['id']]=(direct,traced-direct)
    regions = [{k:v for k,v in r.items() if k not in ('references','pointers')} |
               {'annotation':state['annotations'].get(r['id'],{}),'used_by':len(users[r['id']][0] | users[r['id']][1]),
                'direct_users':len(users[r['id']][0]),'traced_users':len(users[r['id']][1]),
                'function_refs':sorted(users[r['id']][0] | users[r['id']][1]),
                'direct_function_refs':sorted(users[r['id']][0]),'traced_function_refs':sorted(users[r['id']][1])}
               for r in report['regions']]
    from ..rom.asset_inspection import groups
    return {k:v for k,v in report.items() if k!='regions'} | dict(available=True,stale=report['revision']!=_revision(net,proj),regions=regions,asset_groups=groups({**report,'regions':regions}))


def run(net, slug, log):
    """Freeze a read-only analysis snapshot without synchronizing saved game work."""
    with net.progress.view():
        return _run(net,slug,log)


def _run(net, slug, log):
    proj = _project(net,slug)
    cfg = net.cfg(proj)
    revision = _revision(net,proj)
    worker_source = rom_scan.WORKER_SOURCE.read_bytes()
    worker_revision = hashlib.sha256(worker_source).hexdigest()
    asset_revision = version('decompassets')
    previous=_read(net,proj)['report']
    if (previous and previous.get('revision')==revision and 'loader_flow' in previous
            and previous.get('worker_revision') == worker_revision
            and previous.get('asset_revision') == asset_revision):
        log.check_cancel();log('ROM image and analysis inputs are unchanged; using the current saved map.')
        log.progress(dict(phase='ROM map is current',done=1,total=1))
        return dict(regions=len(previous['regions']),size=previous['size'],cached=True)
    log('Reading the analysis image and imported mappings. No compiler or AI calls are made.')
    source = _source(net,proj)
    path = source['path']
    store = net.project_store(proj['id'])
    # A fork's functions have no addresses yet: only its image and segments are read.
    functions = [] if source['fork'] else [dict(r) for r in store.all(f'SELECT name,vram,rom_offset,size,unit,relocs FROM functions WHERE project_id=? AND {IN_BUILD}',proj['id'])]
    for f in functions:f['relocs'] = json.loads(f['relocs'] or '[]')
    symbols = [dict(r) for r in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=?',proj['id'])]
    report = rom_scan.run(worker_source, {
        'source': source | {'path': str(path)}, 'root': proj['root'], 'rom': cfg.get('rom'),
        'console': net.console(proj).id, 'functions': functions, 'symbols': symbols,
        'contracts': _contracts(net,proj),
    }, log)
    log.check_cancel()
    current = net.project_row(slug)
    if current['id'] != proj['id'] or revision != _revision(net,current):
        raise NetworkError('The project layout changed during the scan. Scan again to use the current mappings.')
    report.update(revision=revision,worker_revision=worker_revision,asset_revision=asset_revision,
                  scanned_at=time.time(),image_name=path.name,
                  derived=bool(not source['fork'] and cfg['rom'].get('program') and cfg['rom']['program']!=cfg['rom']['file']))
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
    path = _source(net,proj)['path']
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
    from ..rom.asset_inspection import groups, usage
    flow={**state['report'].get('loader_flow',{}),
          'chains':[state['report']['loader_flow']['chains'][int(i)] for i in row.get('loader_chains',[])]}
    asset_groups=[group for group in groups(state['report']) if region_id in group['region_ids']]
    return dict(row,annotation=state['annotations'].get(region_id,{}),preview='\n'.join(lines),
                strings=[m.group()[:-1].decode('ascii')[:160] for m in re.finditer(rb'[\x20-\x7e]{8,}\x00',text_sample)][:20],
                image_hash=image_hash,loader_flow=flow,usage=usage(row,flow),asset_groups=asset_groups)



def annotate(net, slug, data):
    """Save a human interpretation separately from symbols and automatic evidence."""
    name,notes = data.get('name',''),data.get('notes','')
    kind=data.get('kind','')
    preview_settings=_preview_settings(data.get('preview_settings',{}))
    from ..rom.asset_formats import AssetError
    from ..rom.asset_inspection import model_settings
    try:geometry_settings=model_settings(data.get('model_settings',{}))
    except AssetError as exc:raise NetworkError(str(exc)) from None
    if not isinstance(kind,str) or kind not in KINDS:raise NetworkError('Choose a listed region type or keep the automatic label.')
    if not isinstance(name,str) or not isinstance(notes,str) or len(name)>120 or len(notes)>4000:
        raise NetworkError('Use a label up to 120 characters and notes up to 4,000 characters.')
    proj = _project(net,slug)
    with net.project_store(proj['id']).tx():
        proj,state,row = _current(net,slug,data.get('region'),data.get('image_hash'))
        state['annotations'][row['id']] = dict(name=name.strip(),notes=notes.strip(),kind=kind,preview_settings=preview_settings,model_settings=geometry_settings,updated_at=time.time())
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
    allowed={'format','offset','source','block_offset','width','height','palette_offset','palette_format','channels','rate','length','frame','palette','mip','sample','mesh','texture','record_page','record_component','assembly','animation','dialogue_scene','dialogue_speaker'}
    if set(options)-allowed:raise NetworkError('Use only the listed preview controls.')
    if options.get('format','auto') not in FORMATS or options.get('palette_format','rgba5551-be') not in PALETTES:
        raise NetworkError('Choose a listed preview format and palette format.')
    if options.get('source','rom') not in ('rom','decoded','archive'):raise NetworkError('Choose ROM bytes, a decompressed block or a validated archive member.')
    if any(isinstance(v,(dict,list,bool)) or len(str(v))>40 for v in options.values()):raise NetworkError('Preview controls must contain short text or numeric values.')
    return options


def asset_preview(net,slug,data):
    """Serve a bounded local preview, preserving actionable errors at the API boundary."""
    from ..rom.asset_formats import AssetError
    try:return _asset_preview(net,slug,data)
    except AssetError as exc:raise NetworkError(str(exc)) from None


def _asset_preview(net,slug,data):
    from ..rom.asset_formats import preview
    return _asset_read(net,slug,data,lambda image,options:preview(image,options),detect=True)


def _asset_read(net,slug,data,read, *, detect=False):
    """Verify and read a bounded interpretation from the original or decoded image."""
    from ..rom import asset_formats
    proj,state,row=_current(net,slug,data.get('region'),data.get('image_hash'))
    options=_preview_settings(data.get('options',{}))
    detected=row.get('detected_asset')
    view = data.get('asset_view')
    if detect and view is not None and options.get('format', 'auto') == 'auto' and options.get('source', 'rom') == 'rom':
        if view not in ('image', '3d model'):
            raise NetworkError('Choose a texture or model preview.')
        detected = next((asset for asset in row['evidence']
                         if asset.get('kind') == view and 'preview_settings' in asset), None)
        if detected is None:
            raise NetworkError('This region has no detected asset for that view. Scan ROM Explorer again.')
    using_detected=bool(detect and detected and options.get('format','auto')=='auto' and
                        options.get('source','rom')=='rom' and
                        asset_formats.integer(options.get('offset',row['start']),'Data offset',0,state['report']['size']-1) in (row['start'],detected['offset']))
    if using_detected:
        selection=dict(options)
        options=dict(detected['preview_settings'])
        original_read=read
        if detected.get('provider')=='splat-config':
            from ..rom import config_images
            read=lambda image,settings:config_images.preview(image,detected,selection)
        elif detected.get('provider') in ('portable-font','portable-records'):
            from ..rom import resource_catalog
            source=_source(net,proj)
            read=lambda image,settings:resource_catalog.preview(image,detected,selection,source['segments'],source['big_endian'])
        else:
            read=lambda image,settings:net.console(proj).asset_preview(image,detected,selection) or original_read(image,settings)
    path=_source(net,proj)['path']
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        if hashlib.sha256(image).hexdigest()!=data['image_hash']:raise NetworkError('The image changed. Reopen ROM Explorer before previewing assets.')
        offset=asset_formats.integer(options.get('offset',row['start']),'Data offset',0,len(image)-1) if options.get('source','rom')=='rom' else row['start']
        header=image[offset:offset+4]
        compressed=header[:3]==b'EDL' and len(header)==4 and (header[3]&127)<=2 or header in (b'Yaz0',b'Yay0',b'MIO0',b'RNC\x01',b'RNC\x02') or header.startswith(b'\x1f\x8b\x08') or len(header)>=2 and header[0]==0x78 and int.from_bytes(header[:2],'big')%31==0
        auto_decode=not using_detected and options.get('source','rom')=='rom' and options.get('format','auto')=='auto' and compressed
        scene=net.console(proj).asset_scene(image,detected,selection,list({r['detected_asset']['offset']:r['detected_asset'] for r in state['report']['regions'] if r.get('detected_asset')}.values())) if using_detected else None
        if scene is not None:
            result=scene
        elif options.get('source')=='archive':
            from ..rom.asset_archives import member_bytes
            member=(row.get('detected_asset') or {}).get('archive')
            if not member:raise NetworkError('This region has no validated archive member. Choose ROM bytes or a supported detected archive.')
            raw=member_bytes(image,member);result=read(raw,options)
            result['decoded']=dict(codec='ZIP member',bytes=len(raw),block_offset=member['offset'],member=member['name'])
        elif options.get('source','rom')=='decoded' or auto_decode:
            block=offset if auto_decode else asset_formats.integer(options.get('block_offset',row['start']),'Compressed block offset',0,len(image)-1)
            block_data=image[block:block+asset_formats.MAX_BYTES]
            raw,codec=net.console(proj).asset_decode(block_data) or asset_formats.unpack(block_data)
            selected={**options,'offset':0 if auto_decode else options.get('offset',0)}
            result=read(raw,selected)
            result['decoded']=dict(codec=codec,bytes=len(raw),block_offset=block)
        else:
            result=read(image,{**options,'offset':options.get('offset',row['start'])})
    if using_detected:
        _resource_links(result,detected,state['report'])
        result['settings']={**result.get('settings',{}),'record_page':selection.get('record_page',0)}
    if 'settings' not in result:result['settings']=options
    return result


def asset_inspect(net,slug,data):
    """Bounded texture comparisons and manual geometry share preview integrity checks."""
    from ..rom import asset_inspection
    from ..rom.asset_formats import AssetError
    action=data.get('action')
    if action not in ('textures','model'):raise NetworkError('Choose texture discovery or a model preview.')
    try:
        def read(image,options):
            if action=='model':return asset_inspection.model(image,options,data.get('model_settings',{}))
            return asset_inspection.textures(image,options)
        return _asset_read(net,slug,data,read)
    except AssetError as exc:raise NetworkError(str(exc)) from None


def _resource_links(result,asset,report):
    """Resolve local directory IDs and verified cross-directory asset references."""
    import bisect
    rows=report['regions'];starts=[r['start'] for r in rows]
    assets={r['detected_asset']['offset']:r['detected_asset'] for r in rows if r.get('detected_asset')}
    root=asset.get('directory_offset');objects={};textures={};related=[]
    for candidate in assets.values():
        if root is None or candidate.get('directory_offset')!=root:continue
        if (asset.get('resource_type')=='dialogue' and candidate.get('resource_type')=='dialogue'
                and candidate['offset']!=asset['offset']):
            related.append(dict(label=candidate['label'],offset=candidate['offset']))
        info=candidate.get('resource_fields',{})
        if candidate.get('resource_type')=='object' and info.get('object_id') is not None:objects[info['object_id']]=candidate['offset']
        path=candidate.get('resource_path','')
        if path.startswith('directory/textures/texture bank['):
            try:textures[int(path.split('[')[-1].rstrip(']'))]=candidate['offset']
            except ValueError:pass
        parent=asset.get('resource_path','').split('/geometry')[0].split('/animations')[0]
        if '/object[' in parent and candidate.get('resource_path') in (parent,parent+'/animations',parent+'/geometry') and candidate['offset']!=asset['offset']:
            related.append(dict(label=candidate.get('label','Related resource'),offset=candidate['offset']))
    def link(offset,label):
        i=bisect.bisect_right(starts,offset)-1
        if i>=0 and offset<rows[i]['end']:return dict(label=label,region=rows[i]['id'],offset=offset)
    for row in result.get('table',{}).get('rows',[]):
        links=[]
        audio=assets.get(row.get('audio_offset'),{})
        if asset.get('resource_type')=='dialogue' and audio.get('audio_kind')=='dean stream':
            if value:=link(audio['offset'],'Open voice audio'):
                links.append(dict(value,inspect=True))
        if row.get('object_id') in objects:links.append(link(objects[row['object_id']],f"Object {row['object_id']}"))
        if root is not None and 'offset' in row and row.get('size',1)>0:links.append(link(row['offset'],'Open resource'))
        for offset in row.get('destinations',[]):links.append(link(offset,f'ROM {offset:#x}'))
        row['links']=[v for v in links if v]
    material=result.get('material',{})
    if material.get('texture_id') in textures:related.append(dict(label=f"Texture {material['texture_id']}",offset=textures[material['texture_id']]))
    result['links']=[value for row in related if (value:=link(row['offset'],row['label']))]
    cutscenes=[]
    for row in asset.get('cutscene_resources',[]):
        target=assets.get(row['offset'],{})
        if (target.get('layout')=='scene-records' and target.get('label')=='NCS cutscene commands'
                and (value:=link(row['offset'],row['label']))):
            cutscenes.append(dict(value,inspect=True))
    if cutscenes:result['cutscene_links']=cutscenes

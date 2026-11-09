"""Reproducible byte checks and structural neighbours for advisory asset findings."""
from __future__ import annotations

import hashlib
import mmap
from pathlib import Path

from ..network import NetworkError
from ..rom.asset_formats import AssetError
from . import rom_explorer


def validate(net,slug,report,region,proposal):
    """Separate parser facts, static usage and unsupported interpretations from confidence."""
    row=next(r for r in report['regions'] if r['id']==region)
    checks=[dict(check='Proposed extent',status='pass',evidence=f"{proposal['end']-proposal['start']} bytes at {proposal['start']:#x}; boundaries remain advisory.")]
    settings=proposal.get('preview_settings',{});model=proposal.get('model_settings',{})
    if settings or model:
        packet=dict(region=region,image_hash=report['image_hash'],options=settings)
        try:
            result=rom_explorer.asset_inspect(net,slug,{**packet,'action':'model','model_settings':model}) if model else rom_explorer.asset_preview(net,slug,packet)
            details=result.get('details',{})
            checks.append(dict(check='Byte interpretation',status='pass',evidence=details or {k:v for k,v in result.items() if k not in ('vertices','edges','points','data_url')},decoded=result.get('decoded')))
            if not result.get('decoded'):
                offset=int(settings.get('offset',row['start']));length=details.get('bytes',details.get('size'))
                if length is not None:checks.append(dict(check='Layout within proposed span',status='pass' if proposal['start']<=offset and offset+length<=proposal['end'] else 'differs',evidence=f'Layout uses {length} bytes at {offset:#x}; compare with proposed boundaries.'))
        except NetworkError as exc:checks.append(dict(check='Byte interpretation',status='fail',evidence=str(exc)))
    elif proposal.get('kind') in ('image','audio','compressed') or row['kind']=='compressed':
        packet=dict(region=region,image_hash=report['image_hash'],options={})
        try:
            if proposal.get('kind')=='compressed' or row['kind']=='compressed':
                # Imported symbols can split a validated compressed stream into display regions.
                clue=next((c for c in row.get('evidence',[]) if c.get('kind')=='compressed' and c.get('confidence')=='validated' and 'offset' in c),None)
                if clue:packet['options']=dict(source='decoded',block_offset=clue['offset'],offset=0)
                decoded=rom_explorer._asset_read(net,slug,packet,lambda image,options:dict(bytes=len(image)))
                checks.append(dict(check='Decompression',status='pass' if decoded.get('decoded') else 'unchecked',evidence=decoded.get('decoded','No supported compression header at the region start.')))
            else:
                result=rom_explorer.asset_preview(net,slug,packet)
                checks.append(dict(check='Self-describing format',status='pass',evidence=result['details']))
        except (NetworkError,AssetError) as exc:checks.append(dict(check='Self-describing format',status='fail',evidence=str(exc)))
    else:checks.append(dict(check='Byte interpretation',status='unchecked',evidence='No explicit supported preview layout. A name or format claim alone is not validated.'))
    start,end=proposal['start'],proposal['end']
    chains=[c for c in report.get('loader_flow',{}).get('chains',[]) if c['source']['start']<end and start<c['source']['end']]
    for chain in chains:
        source=chain['source'];exact=start==source['start'] and end==source['end']
        checks.append(dict(check='Static loader extent',status='consistent' if exact else 'differs',evidence=f"{chain['function']} at {chain['instruction']:#x}: source {source['start']:#x}–{source['end']:#x}; {chain['reason']}",role=chain['role'],steps=chain.get('steps',[])))
    checks.append(dict(check='Function usage',status='recorded' if row.get('references') else 'unchecked',evidence=row.get('references',[])))
    return dict(checks=checks,note='A successful parser checks a layout, not its intended meaning. Static arguments do not prove execution; consumer dimensions and runtime behaviour remain unverified.')


def similar(net,slug,report,region,proposal,page=0):
    """Search every non-code region using bounded byte samples; never propagate labels."""
    from ..rom.asset_formats import AssetError, integer
    try:page=integer(page,'Similar regions page',0,1000000)
    except AssetError as exc:raise NetworkError(str(exc)) from None
    source=next(r for r in report['regions'] if r['id']==region)
    proj=rom_explorer._project(net,slug);path=Path(proj['root'])/net.cfg(proj)['rom']['file']
    matches=[];examined=0
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as image:
        if hashlib.sha256(image).hexdigest()!=report['image_hash']:raise NetworkError('The image changed. Finish a fresh ROM Explorer scan.')
        sample=image[source['start']:min(source['end'],source['start']+64)]
        for row in report['regions']:
            if row['id']==region or row['functions'] or row['kind']=='code':continue
            examined+=1;reasons=[]
            other=image[row['start']:min(row['end'],row['start']+64)]
            # Equal size alone is common for display pages and is not an asset family.
            if len(sample)>=16 and other==sample and len(set(sample))>=4:reasons.append('Identical first 64 bytes (or complete shorter sample)')
            if source['kind'] not in ('unknown','repeated bytes','data') and row['kind']==source['kind']:reasons.append('Same detected data category')
            settings=proposal.get('preview_settings',{})
            fmt=settings.get('format','auto')
            if settings and fmt!='auto' and settings.get('source','rom')=='rom':
                from ..rom import asset_formats
                if not fmt.startswith('pcm-') and not fmt.startswith('ci'):
                    try:
                        width=int(settings.get('width',32));height=int(settings.get('height',32))
                        required=((width*asset_formats._bits(fmt)+7)//8)*height
                        if required<=row['size'] and row['size']==source['size'] and abs(row.get('entropy',0)-source.get('entropy',0))<.25:
                            reasons.append(f'Same {width}×{height} {fmt} byte layout fits; similar entropy and region size')
                    except (ValueError,TypeError):pass
            if not reasons:continue
            if row['size']==source['size']:reasons.append('Same analysed region size')
            if abs(row.get('entropy',0)-source.get('entropy',0))<.25:reasons.append('Similar sampled entropy')
            matches.append(dict(region=row['id'],start=row['start'],end=row['end'],kind=row['kind'],reasons=reasons,score=len(reasons),layout_checked=False))
    matches.sort(key=lambda r:(-r['score'],r['start']))
    return dict(matches=matches[page*50:(page+1)*50],total=len(matches),examined=examined,page=page,note='Structural leads only. Page boundaries, shared headers and entropy can coincide. Inspect and validate each candidate; no labels are copied.')

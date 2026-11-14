"""Jump-table ordering: the rewrites are `decomprepair.switch_tables`; reading a ROM's tables needs the project, so
`read` stays here."""
import re

from decomprepair.switch_tables import *  # noqa: F403 - the rewrites (variants, hints, value)


def read(net, proj, fn):
    """MIPS assembly records the lower case bound and table address; other ISAs keep generic repairs."""
    if proj['arch']!='mips' or not getattr(net,'is_rom',lambda p:False)(proj):
        return []
    asm=fn['asm'] or ''; result=[]
    for hit in re.finditer(r'%hi\(((?:jtbl_|D_)[A-Za-z0-9_]+)\)',asm):
        prefix=asm[max(0,hit.start()-700):hit.start()]
        length=list(re.finditer(r'sltiu\s+\$\w+,\s*(\$\w+),\s*(0x[0-9A-Fa-f]+|\d+)',prefix))
        if not length:continue
        tested=length[-1][1]
        bound=list(re.finditer(r'addiu\s+'+re.escape(tested)+r',\s*\$(?!sp\b)\w+,\s*(-?(?:0x[0-9A-Fa-f]+|\d+))',prefix[:length[-1].start()]))
        low=-int(bound[-1][1],0) if bound else 0
        count=int(length[-1][2],0)
        address=net.symbols(proj['id']).resolve(hit[1])
        if address is None or not 2<=count<=256:
            continue
        data=net.reader(proj,fn)(address,count*4)
        if data is None:
            # Some basic imports describe code ranges only. A single consistent linear image
            # mapping can propose a nearby data table; ambiguous/overlay mappings are excluded.
            # Destination bounds below and the ordinary exact judge remain mandatory.
            cfg=net.cfg(proj);segments=cfg.get('segments') or []
            biases={s['vram']-s['rom_start'] for s in segments}
            last=max((s['rom_end'] for s in segments),default=0)
            if len(biases)==1 and last:
                offset=address-next(iter(biases))
                if last<=offset and offset+count*4<=last+65536:
                    image,_=net._rom(proj)
                    if offset+count*4<=len(image):data=image[offset:offset+count*4]
        if data is not None and len(data)==count*4:
            order='big' if proj['big_endian'] else 'little'
            destinations=[int.from_bytes(data[i:i+4],order) for i in range(0,len(data),4)]
            if all(fn['vram']<=n<fn['vram']+fn['size'] for n in destinations):
                result.append((low,destinations))
    return result

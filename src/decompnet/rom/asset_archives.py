"""Read embedded ZIP members in place, with bounded expansion and no filesystem extraction."""
from __future__ import annotations

import re
import struct
import zlib

from .asset_formats import AssetError

MAX_MEMBERS=16384
MAX_MEMBER=4*1024*1024
MAX_ARCHIVES=64


def zip_members(image, *, check=lambda:None, archives=None):
    """Validate central/local records even when ROM padding follows the end record.

ZIP64, split archives, encryption and methods other than stored/deflate are not
supported. Member paths are display labels only and are never opened as paths. A list passed as `archives` gets each
valid archive's central directory span and its folder entries, which hold no data and aren't members.
"""
    result=[];seen=set()
    for index,match in enumerate(re.finditer(rb'PK\x05\x06',image)):
        if index>=MAX_ARCHIVES:break
        check();end=match.start()
        if end+22>len(image):continue
        disk,central_disk,count_here,count,size,relative,comment=struct.unpack_from('<4H2IH',image,end+4)
        central=end-size;base=central-relative
        if disk or central_disk or count_here!=count or not 0<count<=MAX_MEMBERS or central<0 or base<0 or end+22+comment>len(image):continue
        cursor=central;members=[];folders=[];valid=True
        for _ in range(count):
            check()
            if cursor+46>end or image[cursor:cursor+4]!=b'PK\x01\x02':valid=False;break
            flags,method=struct.unpack_from('<HH',image,cursor+8)
            crc,packed,expanded=struct.unpack_from('<III',image,cursor+16)
            name_size,extra_size,comment_size,start_disk=struct.unpack_from('<4H',image,cursor+28)
            local=base+struct.unpack_from('<I',image,cursor+42)[0]
            after=cursor+46+name_size+extra_size+comment_size
            if start_disk or not 0<name_size<=1024 or after>end or local<base or local+30>central or image[local:local+4]!=b'PK\x03\x04':valid=False;break
            local_flags,local_method=struct.unpack_from('<HH',image,local+6)
            local_name,local_extra=struct.unpack_from('<HH',image,local+26)
            payload=local+30+local_name+local_extra
            name=bytes(image[cursor+46:cursor+46+name_size])
            if local_flags!=flags or local_method!=method or local_name!=name_size or image[local+30:local+30+local_name]!=name or payload+packed>central:valid=False;break
            if name.endswith(b'/') and not packed and not expanded:
                folders.append(dict(name=name.decode('utf-8' if flags&0x800 else 'cp437','replace'),offset=local,size=payload-local))
            if not flags&0x41 and method in (0,8) and 0<expanded<=MAX_MEMBER and 0<packed<=MAX_MEMBER:
                if not flags&8 and struct.unpack_from('<III',image,local+14)!=(crc,packed,expanded):valid=False;break
                try:label=name.decode('utf-8' if flags&0x800 else 'cp437')
                except UnicodeDecodeError:valid=False;break
                if not label.endswith('/'):
                    members.append(dict(name=label,offset=local,size=payload+packed-local,payload=payload,packed=packed,expanded=expanded,crc=crc,method=method))
            cursor=after
        if not valid or cursor!=end:continue
        if archives is not None:archives.append(dict(central=central,end=end+22+comment,count=count,folders=folders))
        for member in members:
            if member['offset'] not in seen:result.append(member);seen.add(member['offset'])
    return result


def member_bytes(image, member, *, prefix=False):
    """Only complete reads establish size, stream-end and CRC evidence."""
    start=member['payload'];end=start+member['packed'];length=member['expanded']
    if not 0<length<=MAX_MEMBER or not 0<=start<end<=len(image) or end-start>MAX_MEMBER:raise AssetError('The archive member exceeds its validated bounds. Rescan the ROM.')
    payload=image[start:end];limit=min(32,length) if prefix else length+1
    if member['method']==0:raw=bytes(payload[:limit])
    elif member['method']==8:
        decoder=zlib.decompressobj(-15)
        try:raw=decoder.decompress(payload,limit)
        except zlib.error:raise AssetError('The ZIP member has an invalid deflate stream. Rescan or restore the original image.') from None
        if not prefix and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):raise AssetError('The ZIP member is truncated or exceeds its declared size.')
    else:raise AssetError('This ZIP compression method is not supported.')
    if not prefix and (len(raw)!=length or zlib.crc32(raw)!=member['crc']):raise AssetError('The ZIP member size or checksum failed. Rescan or restore the original image.')
    return raw

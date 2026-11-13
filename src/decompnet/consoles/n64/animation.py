"""Bounded three-bit, shared-header animation ADPCM and local node tracks."""
import math
import struct

from ...rom.asset_formats import AssetError
from .resources import SchemaError, blocks, stream

MAX_NODE_FRAMES=65536
FILTERS=((0,0),(61440,0),(124928,-61440),(117760,-53248))


def signed32(value):
    return (value+2147483648)%4294967296-2147483648


def adpcm(data,signals,frames):
    if not 1<=signals<=4 or not 1<=frames<=4096 or len(data)<2 or int.from_bytes(data[:2],'big')!=frames:
        raise AssetError('Animation ADPCM frame count is invalid or exceeds 4096 frames.')
    headers=(frames+3)//4;header_bits=headers*6;required=header_bits+signals*(8+frames*3)
    if required>8*(len(data)-2):raise AssetError('Animation ADPCM signal data is truncated.')
    def bits(at,width):
        byte,shift=divmod(at,8)
        return (int.from_bytes(data[2+byte:2+byte+(shift+width+7)//8],'little')>>shift)&((1<<width)-1)
    codes=[bits(i*6,6) for i in range(headers)];cursor=header_bits;result=[]
    for signal in range(signals):
        initial=bits(cursor,8);cursor+=8;prediction=(initial-256 if initial&128 else initial)*256
        previous=older=0;values=[]
        for frame in range(frames):
            code=codes[frame//4];a,b=FILTERS[code>>4];value=bits(cursor,3);cursor+=3
            delta=(value-8 if value&4 else value)<<(code&15)
            # MIPS multiplies/adds in 32 bits before the arithmetic right shift.
            decoded=signed32(delta+(signed32(previous*a+older*b)>>16))
            older,previous=previous,decoded;prediction=signed32(prediction+decoded)
            values.append(max(-32768,min(32767,prediction)))
        result.append(values)
    return result


def tracks(data):
    from .resources import fields
    try:
        _,info=fields(data,'animation');parts=blocks(data,(0,len(data)),7)
        frames=info['frames'];nodes=info['nodes']
        if frames>4096 or nodes>256 or frames*nodes>MAX_NODE_FRAMES:raise AssetError('Animation exceeds the preview limit of 256 nodes, 4096 frames or 65,536 node frames.')
        translations=stream(data,parts[3]);rotations=stream(data,parts[4]);positions=[];quaternions=[]
        if max(translations['records'],rotations['records'])>256 or frames*(translations['records']+rotations['records']+nodes)>4*MAX_NODE_FRAMES:raise AssetError('Animation track storage exceeds the bounded preview budget.')
        for i in range(translations['records']):
            width=translations['record_bytes'];at=parts[3][0]+8+i*width;raw=data[at:at+width]
            if len(raw)<20:raise AssetError('Animation translation header is truncated.')
            compressed,*params=struct.unpack_from('>I4f',raw);offset=params[:3];scale=params[3]
            if compressed not in (0,1) or not all(math.isfinite(v) for v in params):raise AssetError('Animation translation encoding or scale is invalid.')
            if compressed:
                decoded=adpcm(raw[20:],3,frames)
                positions.append([[decoded[k][f]/32767*scale+offset[k] for k in range(3)] for f in range(frames)])
            else:
                if width<20+frames*12:raise AssetError('Animation translation frames are truncated.')
                positions.append([list(struct.unpack_from('>3f',raw,20+f*12)) for f in range(frames)])
        for i in range(rotations['records']):
            width=rotations['record_bytes'];at=parts[4][0]+8+i*width;decoded=adpcm(data[at:at+width],4,frames)
            quaternions.append([[decoded[k][f]/32767 for k in range(4)] for f in range(frames)])
        result=[]
        for i in range(nodes):
            t,r=struct.unpack_from('>hh',data,parts[1][0]+8+i*4)
            if (t==-1 or r==-1) and i>=info['initial_orientations']:raise AssetError('This animation lacks an initial transform needed by a node. Choose another animation.')
            initial=struct.unpack_from('>3f4h',data,parts[2][0]+8+i*20) if i<info['initial_orientations'] else None
            p=positions[t] if t>=0 else [list(initial[:3]) for _ in range(frames)]
            q=quaternions[r] if r>=0 else [[v/32767 for v in initial[3:]] for _ in range(frames)]
            if not all(math.isfinite(v) for rows in (p,q) for row in rows for v in row):raise AssetError('Animation contains non-finite transforms.')
            result.append(dict(positions=p,rotations=q))
        return dict(frames=frames,nodes=result)
    except SchemaError as exc:raise AssetError(str(exc)) from None

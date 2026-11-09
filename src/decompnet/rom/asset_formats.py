"""Bounded asset inspection and browser previews, with no external tools or game writes.

Raw texture and PCM layouts are user-selected interpretations. Self-describing PNG,
JPEG and PCM WAVE files validate their dimensions/chunks before being served locally.
"""
from __future__ import annotations

import base64
import binascii
import io
import struct
import wave
import zlib

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
MAX_DIMENSION = 2048
MAX_SECONDS = 30
FORMATS = ('auto','rgba8888','rgb888','rgba5551-be','rgb565-le','rgb565-be','ia4','ia8','ia16','i4','i8',
           'ci4','ci8','palette-rgba5551','palette-ia16','pcm-s16le','pcm-s16be','pcm-u8')
PALETTES = ('rgba5551-be','ia16','rgba8888')


class AssetError(ValueError):
    """An actionable format/extent error for the preview panel."""


def integer(value, name, low, high):
    if isinstance(value,bool):raise AssetError(f'{name} must be a whole number from {low} to {high}.')
    try:result=int(value,0) if isinstance(value,str) else int(value)
    except (ValueError,TypeError,OverflowError):raise AssetError(f'Enter a whole number for {name}.') from None
    if isinstance(value,float) or not low<=result<=high:raise AssetError(f'{name} must be a whole number from {low} to {high}.')
    return result


def _take(data,offset,size):
    if offset<0 or size<0 or size>MAX_BYTES or offset+size>len(data):
        raise AssetError('The selected data is truncated or exceeds the 4 MiB preview limit. Reduce its dimensions or length.')
    return data[offset:offset+size]


def unpack(data):
    """Expand a supported stream under input/output caps, returning bytes and its codec."""
    data=data[:MAX_BYTES]
    if data.startswith(b'\x1f\x8b\x08') or len(data)>=2 and data[0]==0x78 and int.from_bytes(data[:2],'big')%31==0:
        codec='gzip' if data.startswith(b'\x1f\x8b') else 'zlib'
        try:
            decoder=zlib.decompressobj(31 if codec=='gzip' else 15)
            result=decoder.decompress(data,MAX_BYTES+1)
        except zlib.error as exc:raise AssetError('This compressed stream could not be validated. Select ROM bytes or check the block offset.') from exc
        if len(result)>MAX_BYTES or not decoder.eof:raise AssetError('The compressed block is incomplete or expands beyond the 4 MiB preview limit.')
        return result,codec
    if data[:4] not in (b'Yaz0',b'MIO0',b'Yay0'):
        raise AssetError('No supported compressed header here. Choose ROM bytes, or a gzip, zlib, Yaz0, MIO0 or Yay0 block.')
    codec=data[:4].decode();size=int.from_bytes(_take(data,4,4),'big')
    if not 0<size<=MAX_BYTES:raise AssetError('The declared expanded size exceeds the 4 MiB preview limit or is empty.')
    out=bytearray();control=0;bits=0
    if codec=='Yaz0':cursor=16
    else:
        links=int.from_bytes(_take(data,8,4),'big');cursor=int.from_bytes(_take(data,12,4),'big');mask_at=16
        if not 16<=links<=len(data) or not 16<=cursor<=len(data):raise AssetError('Compressed table offsets are outside this block.')
    while len(out)<size:
        if bits==0:
            if codec=='Yaz0':control=_take(data,cursor,1)[0];cursor+=1;bits=8
            elif codec=='MIO0':control=_take(data,mask_at,1)[0];mask_at+=1;bits=8
            else:control=int.from_bytes(_take(data,mask_at,4),'big');mask_at+=4;bits=32
        bits-=1
        if control & (1<<bits):out.append(_take(data,cursor,1)[0]);cursor+=1
        else:
            if codec=='Yaz0':token=int.from_bytes(_take(data,cursor,2),'big');cursor+=2
            else:token=int.from_bytes(_take(data,links,2),'big');links+=2
            distance=(token&0xfff)+1;length=token>>12
            if codec=='MIO0':length+=3
            elif length:length+=2
            else:length=_take(data,cursor,1)[0]+0x12;cursor+=1
            if distance>len(out) or len(out)+length>size:raise AssetError('The compressed block contains an invalid back-reference or output length.')
            for _ in range(length):out.append(out[-distance])
    return bytes(out),codec


def _png(width,height,pixels):
    def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',binascii.crc32(kind+payload)&0xffffffff)
    raw=b''.join(b'\0'+pixels[y*width*4:(y+1)*width*4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def _rgba(raw,fmt,count):
    pixels=bytearray()
    for n in range(count):
        if fmt=='rgba8888':color=raw[n*4:n*4+4]
        elif fmt=='rgb888':color=(*raw[n*3:n*3+3],255)
        elif fmt.startswith('rgb565'):
            value=int.from_bytes(raw[n*2:n*2+2],'little' if fmt.endswith('le') else 'big')
            color=((value>>11)*255//31,((value>>5)&63)*255//63,(value&31)*255//31,255)
        elif fmt=='rgba5551-be':
            value=int.from_bytes(raw[n*2:n*2+2],'big');color=((value>>11)*255//31,((value>>6)&31)*255//31,((value>>1)&31)*255//31,(value&1)*255)
        elif fmt=='ia16':color=(raw[n*2],)*3+(raw[n*2+1],)
        elif fmt=='ia8':v=raw[n];color=(v>>4<<4 | v>>4,)*3+((v&15)*17,)
        elif fmt in ('i4','ia4'):
            v=(raw[n//2]>>(4 if n%2==0 else 0))&15
            color=(v*17,)*3+(255,) if fmt=='i4' else ((v>>1)*255//7,)*3+((v&1)*255,)
        else:color=(raw[n],)*3+(255,)
        pixels.extend(color)
    return bytes(pixels)


def _bits(fmt):return {'rgba8888':32,'rgb888':24,'rgba5551-be':16,'rgb565-le':16,'rgb565-be':16,'ia16':16,'ia4':4,'i4':4,'ci4':4}.get(fmt,8)


def texture_pixels(data,options):
    """Decode row-major packed pixels; no guessed dimensions, swizzling or palettes."""
    fmt=options['format'];palette_only=fmt.startswith('palette-')
    width=integer(options.get('width',16 if palette_only else 32),'Width',1,MAX_DIMENSION)
    height=integer(options.get('height',16 if palette_only else 32),'Height',1,MAX_DIMENSION)
    if width*height>MAX_PIXELS:raise AssetError('Choose dimensions totalling at most 1,048,576 pixels.')
    if palette_only:fmt={'palette-rgba5551':'rgba5551-be','palette-ia16':'ia16'}[fmt]
    offset=integer(options.get('offset',0),'Data offset',0,len(data))
    # Four-bit rows have byte padding when their pixel width is odd.
    row_bytes=(width*_bits(fmt)+7)//8
    raw=_take(data,offset,row_bytes*height)
    if fmt in ('ci4','ci8'):
        palette_format=options.get('palette_format','rgba5551-be')
        if palette_format not in PALETTES:raise AssetError('Choose a supported palette format.')
        entries=16 if fmt=='ci4' else 256
        palette_offset=integer(options.get('palette_offset',0),'Palette offset',0,len(data))
        palette=_rgba(_take(data,palette_offset,entries*_bits(palette_format)//8),palette_format,entries)
        pixels=bytearray()
        for y in range(height):
            row=raw[y*row_bytes:(y+1)*row_bytes]
            for x in range(width):
                idx=row[x] if fmt=='ci8' else (row[x//2]>>(4 if x%2==0 else 0))&15
                pixels.extend(palette[idx*4:idx*4+4])
    else:pixels=b''.join(_rgba(raw[y*row_bytes:(y+1)*row_bytes],fmt,width) for y in range(height))
    return bytes(pixels),dict(width=width,height=height,format=options['format'],bytes=row_bytes*height,
                                                note='Manual row-major interpretation. Dimensions, palette and format do not establish the original asset type.')


def texture(data,options):
    pixels,details=texture_pixels(data,options)
    return _png(details['width'],details['height'],pixels),details


def pcm(data,options):
    """Wrap selected raw PCM in a browser-playable WAV, never autoplaying it."""
    fmt=options['format'];channels=integer(options.get('channels',1),'Channels',1,2)
    rate=integer(options.get('rate',22050),'Sample rate',1000,192000)
    length=integer(options.get('length',min(len(data),rate*channels*2)), 'Audio byte length',1,MAX_BYTES)
    offset=integer(options.get('offset',0),'Data offset',0,len(data))
    width=1 if fmt=='pcm-u8' else 2;frame=channels*width
    if length%frame:raise AssetError('Audio byte length must contain whole sample frames.')
    if length>rate*frame*MAX_SECONDS:raise AssetError('Limit the preview to 30 seconds of audio.')
    raw=_take(data,offset,length)
    if fmt=='pcm-s16be':raw=b''.join(raw[i:i+2][::-1] for i in range(0,len(raw),2))
    out=io.BytesIO()
    with wave.open(out,'wb') as f:f.setnchannels(channels);f.setsampwidth(width);f.setframerate(rate);f.writeframes(raw)
    return out.getvalue(),dict(format=fmt,channels=channels,rate=rate,seconds=round(length/(rate*frame),3),bytes=length,
                              note='Manual PCM interpretation. Wrong format/rate can sound noisy; playback starts only when you press Play.')


def vag(data):
    """Preview standard mono VAGp PS-ADPCM, excluding interleaved/custom variants.

    Integer predictors follow the standard five coefficient pairs described by
    vgmstream's PS-ADPCM decoder; hardware/tool rounding can differ slightly.
    https://github.com/vgmstream/vgmstream/blob/master/src/coding/psx_decoder.c
    """
    _take(data,0,48)
    size=int.from_bytes(data[12:16],'big');rate=int.from_bytes(data[16:20],'big')
    if not size or size%16 or size+48>min(len(data),MAX_BYTES) or not 1000<=rate<=192000:
        raise AssetError('VAGp needs a complete mono stream of 16-byte frames and a valid sample rate.')
    if data[30:32]!=b'\0\0':raise AssetError('This VAGp header may describe a custom channel layout; automatic preview supports standard mono VAGp only.')
    if size//16*28*2>MAX_BYTES-44:raise AssetError('Decoded VAGp audio exceeds the 4 MiB preview limit.')
    if size//16*28>rate*MAX_SECONDS:raise AssetError('This VAGp stream exceeds the 30 second preview limit.')
    coefficients=((0,0),(60,0),(115,-52),(98,-55),(122,-60));samples=[];h1=h2=0
    for at in range(48,48+size,16):
        header,flags=data[at],data[at+1];predictor=header>>4;shift=header&15
        if predictor>4 or shift>12 or flags>7:raise AssetError('VAGp contains unsupported predictor, shift or flag values.')
        if flags==7:break
        a,b=coefficients[predictor]
        for byte in data[at+2:at+16]:
            for nibble in (byte&15,byte>>4):
                value=((nibble if nibble<8 else nibble-16)<<12)>>shift
                value+=((h1*a+h2*b+32)>>6)
                value=max(-32768,min(32767,value));samples.append(value);h2,h1=h1,value
        if flags&1:break
    raw=struct.pack('<'+str(len(samples))+'h',*samples)
    payload,details=pcm(raw,dict(format='pcm-s16le',channels=1,rate=rate,length=len(raw)))
    details.update(format='VAGp PS-ADPCM',size=size+48,note='Standard mono VAGp decoded with integer predictors; custom/interleaved variants are unsupported. Audio starts only when you press Play.')
    return payload,details


def identify(data):
    """Validate a self-describing asset's extent, dimensions and container structure."""
    if data.startswith(b'VAGp'):
        _,details=vag(data)
        return dict(mime='audio/wav',**details)
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        at=8;width=height=0;seen_data=False;idat=bytearray();row_bytes=0;palette=False
        while at+12<=len(data) and at<MAX_BYTES:
            length=int.from_bytes(data[at:at+4],'big');kind=data[at+4:at+8]
            payload=_take(data,at+8,length);crc=_take(data,at+8+length,4)
            if binascii.crc32(kind+payload)&0xffffffff!=int.from_bytes(crc,'big'):raise AssetError('PNG chunk checksum failed.')
            if not width and kind!=b'IHDR':raise AssetError('PNG must begin with its image header.')
            if kind==b'IHDR':
                if width or length!=13:raise AssetError('PNG has an invalid or repeated image header.')
                width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',payload)
                channels={0:1,2:3,3:1,4:2,6:4}.get(color)
                allowed={0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}
                if not 0<width<=MAX_DIMENSION or not 0<height<=MAX_DIMENSION or width*height>MAX_PIXELS or channels is None or depth not in allowed[color]:raise AssetError('PNG dimensions or color format exceed preview limits.')
                if compression or filtering or interlace:raise AssetError('This preview supports non-interlaced PNG images with standard compression and filtering.')
                row_bytes=(width*channels*depth+7)//8+1
                if row_bytes*height>MAX_BYTES:raise AssetError('PNG expands beyond the 4 MiB preview limit.')
            elif kind==b'PLTE':
                if not length or length%3 or length>768:raise AssetError('PNG palette length is invalid.')
                palette=True
            elif kind==b'IDAT':idat.extend(payload);seen_data=True
            elif kind==b'IEND':
                if length or not seen_data or color==3 and not palette:raise AssetError('PNG is missing valid image data or its indexed palette.')
                try:
                    decoder=zlib.decompressobj();decoded=decoder.decompress(bytes(idat),row_bytes*height+1)
                except zlib.error as exc:raise AssetError('PNG compressed pixels are invalid.') from exc
                if not decoder.eof or len(decoded)!=row_bytes*height or decoder.unused_data:raise AssetError('PNG pixel length does not match its dimensions.')
                if any(decoded[y*row_bytes]>4 for y in range(height)):raise AssetError('PNG has an invalid row filter.')
                return dict(mime='image/png',size=at+12,width=width,height=height,format='PNG')
            at+=12+length
        raise AssetError('PNG is truncated or exceeds the preview limit.')
    if data.startswith(b'RIFF') and data[8:12]==b'WAVE':
        end=int.from_bytes(_take(data,4,4),'little')+8
        if not 12<=end<=min(len(data),MAX_BYTES):raise AssetError('WAVE container is truncated or exceeds 4 MiB.')
        at=12;spec=None;audio=None
        while at+8<=end:
            kind=data[at:at+4];length=int.from_bytes(data[at+4:at+8],'little');payload=_take(data,at+8,length)
            if at+8+length>end:raise AssetError('WAVE chunk exceeds its container.')
            if kind==b'fmt ':
                if spec is not None:raise AssetError('WAVE has repeated format headers.')
                if len(payload)<16:raise AssetError('WAVE format header is truncated.')
                encoding,channels,rate,byte_rate,align,bits=struct.unpack('<HHIIHH',payload[:16])
                if encoding!=1 or channels not in (1,2) or bits not in (8,16) or not 1000<=rate<=192000 or align!=channels*bits//8 or byte_rate!=rate*align:
                    raise AssetError('Automatic audio preview supports PCM WAVE, mono/stereo, 8 or 16 bit.')
                spec=dict(channels=channels,rate=rate,frame=align)
            elif kind==b'data':
                if audio is not None:raise AssetError('WAVE has repeated audio data chunks.')
                audio=length
            at+=8+length+(length&1)
        if not spec or audio is None or audio%spec['frame']:raise AssetError('WAVE needs a valid format and whole audio frames.')
        seconds=audio/(spec['rate']*spec['frame'])
        if seconds>MAX_SECONDS:raise AssetError('This WAVE exceeds the 30 second preview limit. Select raw PCM and a shorter range.')
        return dict(mime='audio/wav',size=end,format='PCM WAVE',seconds=round(seconds,3),**spec)
    if data.startswith(b'\xff\xd8\xff'):
        at=2;width=height=None;components=set()
        while at+4<=min(len(data),MAX_BYTES):
            if data[at]!=255:raise AssetError('JPEG marker structure is invalid.')
            while at<len(data) and data[at]==255:at+=1
            marker=_take(data,at,1)[0];at+=1
            if marker==0xd9:break
            if marker not in (*range(0xc0,0xd0),0xda,0xdb,0xdc,0xdd,0xde,0xdf,*range(0xe0,0xf0),0xfe):
                raise AssetError('JPEG marker structure is invalid.')
            length=int.from_bytes(_take(data,at,2),'big')
            if length<2:raise AssetError('JPEG segment length is invalid.')
            payload=_take(data,at+2,length-2)
            if marker in (0xc0,0xc1,0xc2):
                if len(payload)<6 or payload[0]!=8 or not 1<=payload[5]<=4 or len(payload)!=6+3*payload[5]:
                    raise AssetError('JPEG frame header is invalid.')
                components=set(payload[6::3])
                if len(components)!=payload[5]:raise AssetError('JPEG frame components are invalid.')
                height,width=struct.unpack('>HH',payload[1:5])
                if not 0<width<=MAX_DIMENSION or not 0<height<=MAX_DIMENSION or width*height>MAX_PIXELS:raise AssetError('JPEG dimensions exceed preview limits.')
            at+=length
            if marker==0xda:
                if not payload or not 1<=payload[0]<=4 or len(payload)!=4+2*payload[0] or not set(payload[1:-3:2])<=components:
                    raise AssetError('JPEG scan header is invalid.')
                # Entropy bytes can contain escaped FF and restart markers. EOI
                # terminates the contained JPEG; browsers validate the full scan.
                end=data.find(b'\xff\xd9',at,min(len(data),MAX_BYTES))
                if end<0 or width is None:raise AssetError('JPEG scan or dimensions are incomplete.')
                return dict(mime='image/jpeg',size=end+2,format='JPEG',width=width,height=height,note='Marker structure and dimensions checked; the browser validates the compressed scan.')
        raise AssetError('JPEG has no bounded image scan.')
    raise AssetError('No supported image or PCM WAVE header here. Choose a raw texture, palette or PCM format and enter its layout.')


def preview(data,options):
    """Produce only bounded image/audio payloads; manual choices never become facts."""
    fmt=options.get('format','auto')
    if fmt not in FORMATS:raise AssetError('Choose one of the listed preview formats.')
    if fmt=='auto':
        offset=integer(options.get('offset',0),'Data offset',0,len(data));raw=data[offset:offset+MAX_BYTES]
        if raw.startswith(b'VAGp'):payload,details=vag(raw);mime='audio/wav'
        else:details=identify(raw);payload=raw[:details['size']];mime=details.pop('mime')
    elif fmt.startswith('pcm-'):payload,details=pcm(data,options);mime='audio/wav'
    else:payload,details=texture(data,options);mime='image/png'
    return dict(media='audio' if mime.startswith('audio') else 'image',mime=mime,details=details,
                data_url=f'data:{mime};base64,'+base64.b64encode(payload).decode('ascii'))

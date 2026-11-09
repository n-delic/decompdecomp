"""Source-assisted libultra B1 banks: bounded pointer graphs and explicit table evidence.

Control pointers are relative to the B1 header, sample pointers to a separate table.
ADPCM headers alone cannot locate that table. Named loader arguments corroborate it;
a unique adjacent structural candidate stays explicitly tentative without that evidence.
"""
from __future__ import annotations

import base64
import io
import itertools
import re
import struct
import wave

from ...rom.asset_formats import MAX_BYTES, MAX_SECONDS, AssetError, integer

MAX_CONTROL = 1024*1024
MAX_CANDIDATES = 128
MAX_SOUNDS = 4096
MAX_SAMPLES = 2048
MAX_TABLE_PROBES = 32
MAX_HEADER_CHECKS = 4*1024*1024


class BankError(ValueError):
    pass


def bank(image, offset, *, check=lambda: None):
    """Reject relocated flags, invalid graphs, unsupported books and truncated records."""
    end = offset+8
    samples = {}; sounds = 0; visited = set()
    limit = min(len(image),offset+MAX_CONTROL)

    def take(at, size, alignment=4):
        nonlocal end
        if at<4 or at%alignment or size<0 or offset+at+size>limit:raise BankError('Invalid bank pointer or extent.')
        end=max(end,offset+at+size)
        return image[offset+at:offset+at+size]

    def pointer(at):return int.from_bytes(take(at,4),'big')

    def instrument(at, rate, bank_index, instrument_index):
        nonlocal sounds
        if not at:return
        key=(at,rate)
        if key in visited:return
        visited.add(key)
        head=take(at,16);count=int.from_bytes(head[14:16],'big')
        if head[0]>127 or head[1]>127 or head[3] or not 1<=count<=MAX_SOUNDS:raise BankError('Invalid instrument.')
        take(at,16+count*4)
        for index in range(count):
            check();sounds+=1
            if sounds>MAX_SOUNDS:raise BankError('Bank sound limit reached.')
            sound=pointer(at+16+index*4)
            if not sound:continue
            data=take(sound,16);envelope,keymap,wavetable=struct.unpack_from('>III',data)
            if data[12]>127 or data[13]>127 or data[14]:raise BankError('Invalid sound flags or gain.')
            env=take(envelope,16);keys=take(keymap,6,2)
            if any(value < -1 for value in struct.unpack_from('>iii',env)) or env[12]>127 or env[13]>127:raise BankError('Invalid envelope.')
            if keys[0]>keys[1] or keys[2]>keys[3] or max(keys[:5])>127:raise BankError('Invalid key map.')
            if (wavetable,rate) in samples:continue
            data=take(wavetable,20);base,length=struct.unpack_from('>II',data);codec,flags=data[8:10]
            loop,book=struct.unpack_from('>II',data,12)
            if codec not in (0,1) or flags or not 0<length<=MAX_BYTES:raise BankError('Invalid wavetable.')
            sample=dict(relative_offset=base,size=length,rate=rate,codec='N64 VADPCM' if codec==0 else 'PCM16 big endian',
                        bank=bank_index,instrument=instrument_index,sound=index,key_base=keys[4])
            if codec==0:
                # Some bank builders retain an even-byte pad after odd frame counts.
                if length%9 not in (0,1) or length<9 or length%9==1 and length%2:raise BankError('ADPCM needs complete nine-byte frames, with at most an even-byte pad.')
                order,predictors=struct.unpack('>II',take(book,8))
                if order!=2 or not 1<=predictors<=8:raise BankError('Unsupported ADPCM book.')
                take(book,8+order*predictors*16)
                sample.update(book_offset=offset+book+8,predictors=predictors,samples=length//9*16,encoded_size=length//9*9)
            else:
                if length%2:raise BankError('PCM needs complete samples.')
                sample['samples']=length//2
            if loop:
                start,finish,count=struct.unpack_from('>III',take(loop,44 if codec==0 else 12))
                if not start<=finish<=sample['samples']:raise BankError('Loop exceeds sample extent.')
                sample['loop']=dict(start=start,end=finish,count=count)
            samples[wavetable,rate]=sample
            if len(samples)>MAX_SAMPLES:raise BankError('Bank sample limit reached.')

    if offset+8>len(image) or image[offset:offset+2]!=b'B1':raise BankError('Not a B1 bank.')
    count=int.from_bytes(image[offset+2:offset+4],'big')
    if not 1<=count<=128:raise BankError('Invalid bank count.')
    take(4,count*4)
    for index in range(count):
        at=pointer(4+index*4);header=take(at,12)
        instruments,flags,pad,rate,percussion=struct.unpack('>HBBII',header)
        if flags or pad or not 0<=instruments<=256 or not 1000<=rate<=192000:raise BankError('Invalid bank header.')
        take(at,12+instruments*4)
        instrument(percussion,rate,index,'percussion')
        for number in range(instruments):instrument(pointer(at+12+number*4),rate,index,number)
    if not samples:raise BankError('Bank has no supported samples.')
    return dict(offset=offset,size=end-offset,samples=list(samples.values()),sound_count=sounds)


def loader_tables(image, context, *, check=lambda: None):
    """Read explicit constant table arguments; never execute or patch the game's code."""
    from ...arch.mips_assets import read
    functions=context.get('functions',[])
    targets={f['vram'] for f in functions if f['name'].lstrip('_')=='alBnkfNew'}
    targets.update(s['address'] for s in context.get('symbols',[]) if s['name'].lstrip('_')=='alBnkfNew')
    if not targets:return {}
    needles=[struct.pack('>I',3<<26 | (target>>2)&0x3ffffff) for target in targets]
    result={}
    for f in functions:
        check();offset=f.get('rom_offset')
        if offset is None or not 0<=offset<offset+f['size']<=len(image):continue
        code=image[offset:offset+min(f['size'],128*1024)]
        if not any(needle in code for needle in needles):continue
        for event in read(f['vram'],code,gp=context.get('gp',0),check=check)['events']:
            if event['kind']!='call' or event.get('target') not in targets:continue
            value=event['args'][1]
            if not value or value['kind']!='constant':continue
            base=value['value']
            if not 0<=base<len(image):continue
            result.setdefault(base,[]).append(dict(function=f['name'],instruction=event['instruction'],role='sample-table argument',
                                                   reason='Named alBnkfNew call supplies a constant sample-table argument; runtime execution is not established.'))
    return result


def _fits(image, info, base, budget, check):
    """Validate every frame selector, not a statistical sample of high-entropy bytes."""
    for sample in info['samples']:
        start=base+sample['relative_offset'];end=start+sample['size']
        if start<base or end>len(image):return False
        if start<info['offset']+info['size'] and end>info['offset']:return False
        if sample['codec']=='N64 VADPCM':
            if any(image[start+sample['encoded_size']:end]):return False
            for at in range(start,start+sample['encoded_size'],9):
                if not budget[0]:return False
                budget[0]-=1
                if at%4096<9:check()
                if (image[at]&15)>=sample['predictors']:return False
        elif not info.get('corroborated'):return False
    return True


def _candidates(image):
    for hit in re.finditer(rb'B1\x00[\x01-\x80]',image):
        offset=hit.start()
        if offset+8>len(image):continue
        count=int.from_bytes(image[offset+2:offset+4],'big')
        first=int.from_bytes(image[offset+4:offset+8],'big')
        if not 4+count*4<=first<=MAX_CONTROL or first%4 or offset+first+12>len(image):continue
        header=image[offset+first:offset+first+12]
        if header[2] or header[3] or not 1000<=int.from_bytes(header[4:8],'big')<=192000:continue
        yield offset


def regions(image, *, context=None, check=lambda: None, progress=lambda _: None):
    context=context or {};tables=loader_tables(image,context,check=check);results=[];budget=[MAX_HEADER_CHECKS]
    candidates=list(itertools.islice(_candidates(image),MAX_CANDIDATES))
    for number,offset in enumerate(candidates):
        check();progress(dict(phase='Recognizing audio banks',done=number,total=len(candidates)))
        try:info=bank(image,offset,check=check)
        except BankError:continue
        adjacent=(offset+info['size']+15)//16*16
        bases=sorted(tables,key=lambda at:(at!=adjacent,at))[:MAX_TABLE_PROBES] if tables else [adjacent]
        fits=[]
        for base in bases:
            info['corroborated']=base in tables
            if _fits(image,info,base,budget,check):fits.append(base)
        if len(fits)!=1:
            # The control graph is useful even when table placement is unresolved.
            results.append(dict(offset=offset,size=info['size'],kind='audio',label='N64 sound-bank controls',confidence='validated',
                                reason='B1 control graph, envelopes, key maps, sample extents and predictor books passed. Sample-table placement is unresolved; no sample bytes are classified.',
                                media='audio',audio_kind='unresolved bank',sample_count=len(info['samples']),samples=[],
                                preview_settings=dict(format='auto',source='rom',offset=offset)))
            continue
        base=fits[0];confidence='validated' if base in tables else 'tentative'
        reason=('B1 control graph and every ADPCM frame selector passed. '+
                ('A named alBnkfNew call supplies the sample-table base.' if base in tables else
                 'Sample-table placement is inferred from adjacent aligned data; it is not confirmed by a named loader call.')+
                ' Static structure does not prove runtime use. Preview uses the bank nominal sample rate, without pitch, mixing or loop playback.')
        samples=[{**sample,'offset':base+sample['relative_offset']} for sample in info['samples']]
        common=dict(media='audio',audio_kind='sample bank',table_confidence=confidence,loader_refs=tables.get(base,[]),table_offset=base)
        results.append(dict(offset=offset,size=info['size'],kind='audio',label='N64 sound-bank controls',confidence='validated',
                            reason=reason,preview_settings=dict(format='auto',source='rom',offset=offset),samples=samples,**common))
        seen=set()
        for sample in samples:
            key=sample['offset'],sample['size']
            if key in seen:continue
            seen.add(key)
            results.append(dict(offset=sample['offset'],size=sample['size'],kind='audio',label=sample['codec']+' sample',confidence=confidence,
                                reason=reason,preview_settings=dict(format='auto',source='rom',offset=sample['offset']),samples=[sample],**common))
    return results


def vadpcm(raw, coefficients, predictors, limit, *, check=lambda: None):
    """Decode order-2 mono VADPCM using eight-sample vectors and signed Q11 books.

    Equations checked against vgmstream src/coding/vadpcm_decoder.c. This is a
    local preview, not a cycle-accurate audio microcode emulator.
    """
    if len(raw)%9 or len(coefficients)!=predictors*16:raise AssetError('The ADPCM frames or predictor book are incomplete.')
    history=[0,0];out=[]
    for frame in range(min(len(raw)//9,(limit+15)//16)):
        if frame%256==0:check()
        data=raw[frame*9:frame*9+9];shift=data[0]>>4;predictor=data[0]&15
        if predictor>=predictors:raise AssetError('An ADPCM frame selects a missing predictor.')
        book=coefficients[predictor*16:predictor*16+16]
        residuals=[(n if n<8 else n-16)*(1<<shift) for byte in data[1:] for n in (byte>>4,byte&15)]
        for group in (residuals[:8],residuals[8:]):
            decoded=[]
            for i in range(8):
                value=(group[i]<<11)+book[i]*history[0]+book[8+i]*history[1]
                value+=sum(book[8+k]*group[i-1-k] for k in range(i))
                decoded.append(max(-32768,min(32767,value>>11)))
            history=decoded[-2:];out.extend(decoded)
    return struct.pack('<'+str(min(limit,len(out)))+'h',*out[:limit])


def preview(image, asset, selection):
    if not asset.get('samples'):raise AssetError('The bank descriptors are valid, but sample-table placement is unresolved. Reimport with loader symbols or record the table evidence before previewing.')
    index=integer(selection.get('sample',0),'Sample',0,len(asset['samples'])-1);sample=asset['samples'][index]
    offset=sample['offset'];length=sample['size'];rate=sample['rate']
    if not 0<=offset<offset+length<=len(image):raise AssetError('The detected sample exceeds the current image. Refresh ROM Explorer.')
    limit=min(sample['samples'],rate*MAX_SECONDS,(MAX_BYTES-44)//2)
    raw=image[offset:offset+sample.get('encoded_size',length)]
    if sample['codec']=='N64 VADPCM':
        at=sample['book_offset'];count=sample['predictors']*16
        if not 0<=at<at+count*2<=len(image):raise AssetError('The ADPCM predictor book exceeds the current image.')
        coefficients=struct.unpack_from('>'+str(count)+'h',image,at)
        pcm=vadpcm(raw,coefficients,sample['predictors'],limit)
    else:pcm=b''.join(raw[i:i+2][::-1] for i in range(0,limit*2,2))
    output=io.BytesIO()
    with wave.open(output,'wb') as stream:stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate);stream.writeframes(pcm)
    return dict(media='audio',mime='audio/wav',data_url='data:audio/wav;base64,'+base64.b64encode(output.getvalue()).decode(),
                details=dict(format=sample['codec'],sample=index,rate=rate,channels=1,seconds=round(limit/rate,3),
                             original_samples=sample['samples'],truncated=limit<sample['samples'],note=asset['reason']),
                settings=dict(source='rom',format='auto',offset=asset['offset'],sample=index))

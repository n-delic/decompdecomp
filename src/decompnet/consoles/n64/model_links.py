"""Read-only loader-script links between archive members, never script execution."""
from __future__ import annotations

import re

from ...rom import asset_archives
from ...rom.asset_formats import AssetError


def path(value):
    parts=value.replace('\\','/').strip('"/').split('/')
    if any(p in ('.','..') for p in parts):return ''
    return '/'.join(p for p in parts if p).casefold()


def index(image,members,check=lambda:None):
    """Retain only unique archive paths and explicit loader commands in bounded text."""
    names={};duplicates=set();links={};budget=0
    for m in members:
        key=path(m['name'])
        if key in names:duplicates.add(key)
        else:names[key]=m
    for key in duplicates:names.pop(key)
    for script in members:
        check()
        if not script['name'].lower().endswith('.ini') or script['expanded']>65536:continue
        if budget+script['expanded']>4*1024*1024:break
        budget+=script['expanded']
        try:text=asset_archives.member_bytes(image,script).decode('latin1')
        except AssetError:continue
        directory=''
        for number,line in enumerate(text.splitlines(),1):
            words=re.findall(r'"[^"\r\n]*"|[^\s,]+',line.split('#',1)[0])
            if not words:continue
            command=words[0].upper()
            if command=='SETDIR' and len(words)==2:directory=path(words[1]);continue
            targets=[];companions=[];folder=''
            if command=='LOADN3D' and len(words)==3:
                prefix=path(directory+'/'+words[1]);folder=path(words[2])
                targets=[m for key,m in names.items() if re.fullmatch(re.escape(prefix)+r'_\d+\.v3d',key)]
            elif command=='LOADV3D' and len(words)==5:
                target=names.get(path(directory+'/'+words[1]));folder=path(words[4])
                companions=[names.get(path(directory+'/'+v)) for v in words[2:4]]
                if target and all(companions):targets=[target]
            if not folder:continue
            for target in targets:
                record=dict(script=script,line=number,command=command,texture_folder=folder,companions=companions)
                entries=links.setdefault(target['offset'],[])
                if not any((e['texture_folder'],[c['offset'] for c in e['companions']])==(folder,[c['offset'] for c in companions]) for e in entries):entries.append(record)
    return names,links


def resolve(materials,names,records):
    """Ambiguous folders stay unresolved; a basename coincidence is insufficient."""
    result=[]
    for name in materials:
        candidates={}
        for record in records:
            member=names.get(path(record['texture_folder']+'/'+name+'.n2d'))
            if member:candidates[member['offset']]=member
        result.append(dict(name=name,state='linked' if len(candidates)==1 else 'ambiguous' if candidates else 'missing',
                           archive=next(iter(candidates.values())) if len(candidates)==1 else None))
    return result

"""Narrow saved-field evidence across a call; no inferred C layout or ABI."""
import re

REG=r'\$\w+'
NUM=r'(?:0x[0-9a-fA-F]+|\d+)'


def read(asm):
    lines=[line.split('*/',1)[-1].strip() for line in asm.splitlines()]
    result=[]
    for i,line in enumerate(lines[:24]):
        load=re.fullmatch(r'lw\s+('+REG+r'),\s*('+NUM+r')\(\$a0\)',line)
        if not load:continue
        slot=None
        for text in lines[i+1:i+8]:
            save=re.fullmatch(r'sw\s+'+re.escape(load[1])+r',\s*('+NUM+r')\(\$sp\)',text)
            if save:slot=save[1];break
            if re.search(r'\b(?:jal|jalr|j|b\w*)\b',text):break
        if slot is None:continue
        for j,text in enumerate(lines):
            restore=re.fullmatch(r'lw\s+('+REG+r'),\s*'+re.escape(slot)+r'\(\$sp\)',text)
            if not restore:continue
            for k in range(j+1,min(j+6,len(lines))):
                store=re.fullmatch(r'sw\s+'+re.escape(restore[1])+r',\s*'+re.escape(load[2])+r'\(('+REG+r')\)',lines[k])
                if not store:continue
                # The saved register must not have been redefined before the restore store.
                if any(re.match(r'\w+\s+'+re.escape(restore[1])+r',',x) for x in lines[j+1:k]):continue
                calls=[(p,re.fullmatch(r'jal\s+(\w+)',lines[p])) for p in range(max(j,k-2),min(k+2,len(lines)))]
                calls=[(p,m[1]) for p,m in calls if m]
                if len(calls)!=1:continue
                p,callee=calls[0]
                if not any(re.fullmatch(r'sw\s+\$zero,\s*'+re.escape(load[2])+r'\('+re.escape(store[1])+r'\)',x) for x in lines[max(k,p)+1:max(k,p)+6]):continue
                # The callee's nth occurrence disambiguates repeated calls in the source.
                ordinal=sum(bool(re.fullmatch(r'jal\s+'+re.escape(callee),x)) for x in lines[:p+1])
                result.append({'offset':int(load[2],0),'callee':callee,'ordinal':ordinal})
    return list({(r['offset'],r['callee'],r['ordinal']):r for r in result}.values())[:2]

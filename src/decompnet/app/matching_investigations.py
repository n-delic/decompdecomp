"""Read-only near-miss evidence; no boundary, declaration or target is changed by a hint."""
import re


def paired_loads(rows):
    changed=[r for r in rows or [] if len(r)==3 and r[0]!=' ']
    if len(changed)!=2:return False
    a,b=changed
    return (a[1]==b[2] and a[2]==b[1] and a[1]!=a[2]
            and all(re.match(r'(?:lw|ld|lh|lhu|lb|lbu|lwc1|ldc1)\s',x) for x in (a[1],a[2])))


def epilogue(rows, arch):
    if arch!='mips':return False
    extra=[r[2].strip() for r in rows or [] if len(r)==3 and r[0]!=' ']
    if not extra or any(len(r)!=3 or r[0] not in {' ','>'} for r in rows or []):return False
    if not 3<=len(extra)<=16 or extra[-2]!='jr $ra' or not re.match(r'addiu \$sp, \$sp, (?:0x[0-9a-f]+|\d+)$',extra[-1]):return False
    return all(re.fullmatch(r'(?:lw|ld) \$(?:ra|fp|s[0-7]), (?:0x[0-9a-f]+|\d+)\(\$sp\)',line) for line in extra[:-2])


def hints(rows, arch):
    result=[]
    if paired_loads(rows):result.append('Only two adjacent loads change order. Prioritize paired_load_order, scalar_schedule_order and declaration/helper boundaries before broad random permutation. Do not reorder volatile accesses; each proposal still needs the byte judge.')
    if epilogue(rows,arch):result.append('The candidate only adds a register-restore/return epilogue after otherwise identical target code. Inspect the next original padding/function entry for an unreachable epilogue before further permutations. Do not trim candidate bytes, insert padding or change boundaries without fresh whole-image verification.')
    return result

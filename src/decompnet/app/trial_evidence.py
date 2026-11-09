"""Reserve sparse exploration for broad line trials when current output lacks scheduling evidence."""
BROAD_LINES={'debug_statement_boundary','source_lines','cpu-statement-lines','cpu-statement-lines-body'}


def order(families,rows, *, evidence=None):
    if not rows or any(not isinstance(row,(tuple,list)) or len(row)!=3 or not all(isinstance(x,str) for x in row) for row in rows):return families
    from .allocation_evidence import diagnose
    if evidence is None:evidence=diagnose(rows)
    # Branch-label and delay-slot misses can be debug-line sensitive. Do not require
    # proof that line changes will work; this is ranking, never an equivalence verdict.
    if evidence['scheduling'] or evidence['allocation_schedule'] or evidence['branch_target'] or evidence['delay_slot']:
        return families
    lines=[f for f in families if f in BROAD_LINES]
    ordinary=[f for f in families if f not in BROAD_LINES]
    if not lines:return families
    # Preserve one family in each eight-family window rather than spending the first
    # page on line spellings. The existing cursor retains every family across pages.
    result=[]
    while ordinary or lines:
        result+=ordinary[:7];ordinary=ordinary[7:]
        if lines:result.append(lines.pop(0))
        if not ordinary:result+=lines;break
    return result

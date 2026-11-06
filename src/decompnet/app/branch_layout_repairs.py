"""Alternative call/leaf and switch layouts, with bounded work and explicit control-flow guards."""
import re

from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, integer, view
from .pattern_repairs import close


def call_statement(text, start):
    """A direct call statement (optionally assigned), with nested argument parentheses kept intact."""
    hit = re.match(rf'\s*(?:{IDENT}\s*=\s*)?({IDENT})\s*\(', text[start:])
    if not hit or hit[1] in {'if', 'for', 'while', 'switch', 'sizeof', 'return'}:
        return None
    end = close(text, start+hit.end()-1)
    tail = re.match(r'\s*;', text[end:]) if end else None
    return (start+hit.start(), end+tail.end()) if tail else None


def flat_assignments(text):
    return bool(re.fullmatch(rf'(?:\s*{IDENT}\s*=(?!=)[^;{{}}]*;)+\s*', text)) and not re.search(
        r'\b(?:return|goto|break|continue|case|default|for|while|do)\b', text)


def terminal_case(text):
    terminal = re.search(r'\b(?:break\s*;|return\b[^;{}]*;|goto\s+'+IDENT+r'\s*;)\s*$', text)
    if not terminal or text[:terminal.start()].count('{') != text[:terminal.start()].count('}'):
        return False
    prefix = text[:terminal.start()].rstrip()
    return not prefix or prefix[-1] in ';}'


def controls(part):
    """Braced controls and their ends; an unrecognised scope is a boundary for leaf traversal."""
    result = {}
    for pos in range(part.opening+1, part.end-1):
        if part.clean[pos] != '{':
            continue
        prefix = part.clean[:pos].rstrip()
        kind = 'else' if re.search(r'\belse$', prefix) else ''
        if prefix.endswith(')'):
            depth = 1
            start = len(prefix)-2
            while start >= 0 and depth:
                depth += (prefix[start] == ')')-(prefix[start] == '(')
                start -= 1
            hit = re.search(r'\b(if|switch|for|while)\s*$', prefix[:start+1])
            kind = hit[1] if hit else ''
        if re.search(r'\bdo$', prefix):
            kind = 'do'
        end = close(part.clean, pos, '{', '}')
        if end:
            result[end-1] = (pos, kind)
    return result


def leaf_transfer(part, start, scopes):
    """Follow closing if arms past their else arms, stopping at one explicit terminal transfer."""
    pos = start
    while True:
        while pos < part.end and part.clean[pos].isspace():
            pos += 1
        if part.clean[pos:pos+1] != '}':
            break
        if pos not in scopes or scopes[pos][1] not in {'if', 'else'}:
            return None
        pos += 1
        other = re.match(r'\s*else\s*{', part.clean[pos:])
        if other:
            end = close(part.clean, pos+other.end()-1, '{', '}')
            if not end:
                return None
            pos = end
        elif re.match(r'\s*else\b', part.clean[pos:]):
            return None  # shortcut: leave unbraced/else-if continuation to a full CFG parser
    terminal = re.match(rf'\s*(?:goto\s+({IDENT})|return(?:\s+[^;{{}}]+)?|break)\s*;', part.clean[pos:])
    return (pos, pos+terminal.end(), terminal[1]) if terminal else None


def variants(source, name):
    part = view(source, name)
    if not part:
        return []
    result = []
    clean = part.clean
    # Distribute a call following an if/else which only selects scalar arguments. Retain assignments:
    # they may still be read afterwards, and evaluating an RHS twice would change its meaning.
    for hit in re.finditer(r'\bif\s*\(', part.body):
        cond_end = close(clean, part.opening+1+hit.end()-1)
        arm = re.match(r'\s*{', clean[cond_end:]) if cond_end else None
        if not arm:
            continue
        left = cond_end+arm.end()-1
        left_end = close(clean, left, '{', '}')
        other = re.match(r'\s*else\s*{', clean[left_end:]) if left_end else None
        if not other:
            continue
        right = left_end+other.end()-1
        right_end = close(clean, right, '{', '}')
        if not right_end or not all(flat_assignments(clean[a+1:b-1]) for a, b in [(left, left_end), (right, right_end)]):
            continue
        call = call_statement(clean, right_end)
        if not call:
            continue
        statement = source[call[0]:call[1]]
        result.append(('branch_call', edit(source, [(left_end-1, left_end-1, statement),
                                                  (right_end-1, right_end-1, statement), (*call, '')])))
        if sum(family == 'branch_call' for family, _ in result) >= 4:
            break
    # Inline a leaf with its terminal transfer, never a label whose following code would fall through.
    # Duplicate local names could change lookup at the new site, so leave those functions alone.
    decls = list(re.finditer(rf'(?:^|[;{{}}])\s*(?!goto\b|return\b|break\b|continue\b)'
                            rf'(?:{IDENT}\s+)+\**\s*({IDENT})\s*(?=[;=,\[])', part.body))
    declarations = [decl[1] for decl in decls]
    nested = any(part.body[:decl.start(1)].count('{') != part.body[:decl.start(1)].count('}') for decl in decls)
    if not nested and len(declarations) == len(set(declarations)):
        scopes = controls(part)
        # Unbraced loops can change a copied break's destination too; don't reason through them.
        unbraced = False
        for loop in re.finditer(r'\b(?:for|while)\s*\(', part.body):
            loop_start = part.opening+1+loop.start()
            if loop[0].startswith('while') and any(kind == 'do' and ending < loop_start
                    and not clean[ending+1:loop_start].strip() for ending, (_, kind) in scopes.items()):
                continue  # the latch of a braced do/while is not a second, unbraced loop
            loop_end = close(clean, part.opening+1+loop.end()-1)
            if loop_end and not re.match(r'\s*{', clean[loop_end:]):
                unbraced = True

        def break_scope(pos):
            enclosing = [(opening, ending) for ending, (opening, kind) in scopes.items()
                         if opening < pos < ending and kind in {'switch', 'for', 'while', 'do'}]
            return max(enclosing, default=None)

        for label in re.finditer(rf'\b({IDENT})\s*:(?!:)', part.body):
            start = part.opening+1+label.end()
            call = call_statement(clean, start)
            if not call:
                continue
            terminal = leaf_transfer(part, call[1], scopes)
            if not terminal:
                continue
            transfer_start, end, destination = terminal
            leaf = source[start:call[1]]+source[transfer_start:end]
            if destination == label[1]:
                continue
            for jump in re.finditer(rf'\bgoto\s+{re.escape(label[1])}\s*;', part.body):
                pos = part.opening+1+jump.start()
                if start <= pos < end:
                    continue
                if re.search(r'\bbreak\b', clean[transfer_start:end]) and (
                        unbraced or break_scope(pos) is None or break_scope(pos) != break_scope(start)):
                    continue
                result.append(('goto_leaf', edit(source, [(pos, part.opening+1+jump.end(), '{'+leaf+'}')])) )
                if sum(family == 'goto_leaf' for family, _ in result) >= 4:
                    break
            if sum(family == 'goto_leaf' for family, _ in result) >= 4:
                break
    for switch in re.finditer(r'\bswitch\s*\(', part.body):
        condition = close(clean, part.opening+1+switch.end()-1)
        arm = re.match(r'\s*{', clean[condition:]) if condition else None
        if not arm:
            continue
        opening = condition+arm.end()-1
        end = close(clean, opening, '{', '}')
        if not end:
            continue
        inside = clean[opening+1:end-1]
        labels = [hit for hit in re.finditer(rf'\b(?:case\s+(-?{NUMBER})|default)\s*:', inside)
                  if inside[:hit.start()].count('{') == inside[:hit.start()].count('}')]
        if not labels or inside[:labels[0].start()].strip():
            continue
        # Reject non-literal/range labels, not merely the subset our regex understands.
        all_labels = [hit for hit in re.finditer(r'\b(?:case\b[^:]*|default)\s*:', inside)
                      if inside[:hit.start()].count('{') == inside[:hit.start()].count('}')]
        if len(labels) != len(all_labels):
            continue
        if not any(label[1] is None or integer(label[1].lstrip('-')) == 0 for label in labels):
            result.append(('switch_layout', edit(source, [(opening+1, opening+1, '\ncase 0: break;\n')])))
        starts = []
        pending = labels[0].start()
        valid = True
        for index, label in enumerate(labels):
            stop = labels[index+1].start() if index+1 < len(labels) else len(inside)
            code = inside[label.end():stop]
            if not code.strip():
                continue  # grouped labels travel with their one body
            # Only block-scoped declarations can move with a case. No fallthrough or cross-case labels.
            if re.search(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{IDENT}', code):
                valid = False
                break
            if not terminal_case(code):
                valid = False
                break
            starts.append((pending, stop))
            pending = stop
        if not valid or not 2 <= len(starts) <= 6:
            continue
        chunks = [source[opening+1+a:opening+1+b] for a, b in starts]
        orders = [list(range(rotation, len(chunks)))+list(range(rotation)) for rotation in range(1, len(chunks))]
        orders += [list(reversed(range(len(chunks))))]
        for order in orders[:6]:
            result.append(('switch_layout', edit(source, [(opening+1+starts[0][0], opening+1+starts[-1][1],
                                                          ''.join(chunks[index] for index in order))])))
        break  # bounded per function, not factorial permutations of every nested switch
    return list(dict.fromkeys(result))



def store_tails(source, name, header=''):
    """Duplicate a trailing store in both arms without moving its evaluation on either path."""
    from . import pointer_pair_repairs
    part, _, macros = pointer_pair_repairs.context(source, name, header)
    if not part:
        return []
    out = []
    lhs = rf'{IDENT}(?:\s*->\s*{IDENT}|\s*\[\s*{NUMBER}\s*\])+'
    store = re.compile(rf'\s*(?P<store>(?:{lhs}\s*=(?!=)\s*)+{IDENT}\s*;)')
    for hit in re.finditer(r'\bif\s*\(', part.body):
        cond = close(part.clean, part.opening+hit.end())
        left = re.match(r'\s*{', part.clean[cond:]) if cond else None
        if not left:
            continue
        a = cond+left.end()-1
        b = close(part.clean, a, '{', '}')
        other = re.match(r'\s*else\s*{', part.clean[b:]) if b else None
        if not other:
            continue
        c = b+other.end()-1
        d = close(part.clean, c, '{', '}')
        tail = store.match(part.clean, d) if d else None
        if not tail:
            continue
        arms = part.clean[a+1:b-1]+'\n'+part.clean[c+1:d-1]
        if re.search(r'\b(?:goto|return|break|continue|case|default|asm|setjmp|longjmp)\b|'+IDENT+r'\s*:(?!:)', arms):
            continue
        names = set(re.findall(IDENT, tail['store']))
        if names & macros.keys():
            continue
        # A name declared inside an arm would acquire a different meaning when
        # the store moves into that arm. Reject declarations rather than guessing.
        if re.search(rf'(?:^|[;{{}}])\s*(?:struct\s+)?{IDENT}\s+\**\s*{IDENT}\s*(?=[=;,\[])', arms):
            continue
        text = source[tail.start('store'):tail.end('store')]
        out.append(('branch_store_tail', edit(source, [(b-1, b-1, '\n'+text+'\n'),
                                                       (d-1, d-1, '\n'+text+'\n'), (d, tail.end(), '')])))
        if len(out) >= 4:
            break
    return out



def scalar_tails(source, name, header=''):
    """Sink identical final scalar products from both arms, optionally past one independent literal store."""
    from . import pointer_pair_repairs
    from .integer_repair_support import locals_for
    part, _, macros = pointer_pair_repairs.context(source, name, header)
    if not part:
        return []
    locals_ = {decl['name'] for decl in locals_for(part)
               if part.clean[part.opening+1:decl.start()].count('{')
               == part.clean[part.opening+1:decl.start()].count('}')}
    out = []
    tail = re.compile(rf'(?P<lhs>{IDENT})\s*=\s*(?P<rhs>{IDENT})\s*\*\s*(?P<n>{NUMBER})\s*;\s*$')
    for hit in re.finditer(r'\bif\s*\(', part.body):
        prefix = part.body[:hit.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue  # no unbraced parent or else-if ownership changes
        condition = close(part.clean, part.opening+1+hit.end()-1)
        arm = re.match(r'\s*{', part.clean[condition:]) if condition else None
        if not arm:
            continue
        a = condition+arm.end()-1
        b = close(part.clean, a, '{', '}')
        other = re.match(r'\s*else\s*{', part.clean[b:]) if b else None
        if not other:
            continue
        c = b+other.end()-1
        d = close(part.clean, c, '{', '}')
        if not d:
            continue
        left, right = part.clean[a+1:b-1], part.clean[c+1:d-1]
        matches = [tail.search(text) for text in (left, right)]
        if not all(matches):
            continue
        one, two = matches
        if one.groupdict() != two.groupdict():
            continue
        names = {one['lhs'], one['rhs']}
        if not names <= locals_ or names & macros.keys():
            continue
        # Flat scalar arms only: no incoming labels, transfers, calls, nested
        # scopes or declarations that could change lookup at the successor.
        arms = left+'\n'+right
        if set(re.findall(IDENT, arms)) & macros.keys():
            continue  # object macros can hide transfers or declarations too
        if re.search(r'[{}:]|\b(?:goto|return|break|continue|case|default|if|for|while|do)\b|'
                     + IDENT+r'\s*\(', arms):
            continue
        if re.search(rf'\b{IDENT}\s+\**\s*{IDENT}\s*(?=[=;,\[])', arms):
            continue
        starts = (a+1+one.start(), c+1+two.start())
        if any(part.clean[start-1] not in ';{} \t\n' for start in starts):
            continue
        # Match a complete statement, not the suffix of an array/member store.
        if any(text[:match.start()].rstrip() and text[:match.start()].rstrip()[-1] != ';'
               for text, match in zip((left, right), matches)):
            continue
        statement = source[starts[0]:a+1+one.end()].rstrip()
        removals = [(starts[0], a+1+one.end(), ''), (starts[1], c+1+two.end(), '')]
        out.append(('common_scalar_tail', edit(source, removals+[(d, d, '\n'+statement+'\n')])))
        following = re.match(rf'\s*({IDENT})\s*=\s*{NUMBER}\s*;', part.clean[d:])
        if following and following[1] in locals_-names and following[1] not in macros:
            end = d+following.end()
            out.append(('common_scalar_tail', edit(source, removals+[(end, end, '\n'+statement+'\n')])))
        if len(out) >= 8:
            break
    return list(dict.fromkeys(out))

"""Keep useful search handoffs while suppressing obsolete lease bookkeeping."""

import re

_TRY_LIMIT = re.compile(r"User's max tries reached \(\d+/\d+\); best attempt retained\.")


def boilerplate(text):
    text = text.strip()
    return text == 'Deep permutation turn ended; improvements shared.' or bool(_TRY_LIMIT.fullmatch(text))


def useful(rows):
    """Rows arrive newest first; retain the latest summary per runner."""
    seen = set()
    result = []
    for row in rows:
        text = row['text'].strip()
        if boilerplate(text):
            continue
        if text.startswith('Deep permuter:'):
            key = row['agent'] if 'agent' in row.keys() else row['name']
            if key in seen:
                continue
            seen.add(key)
        result.append(row)
    return result

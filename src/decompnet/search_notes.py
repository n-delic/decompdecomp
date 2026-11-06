"""Keep useful search handoffs while suppressing obsolete lease bookkeeping."""


def useful(rows):
    """Rows arrive newest first; retain the latest summary per runner."""
    seen = set()
    result = []
    for row in rows:
        text = row['text'].strip()
        if text == 'Deep permutation turn ended; improvements shared.':
            continue
        if text.startswith('Deep permuter:'):
            key = row['agent'] if 'agent' in row.keys() else row['name']
            if key in seen:
                continue
            seen.add(key)
        result.append(row)
    return result

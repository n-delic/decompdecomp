"""Generated function names and the database rule for polished progress."""

from fnmatch import fnmatchcase

ADDRESS_PREFIXES = ('fn_', 'fun_', 'sub_', 'loc_', 'nullsub_')
ADDRESS_CHARS = '0-9a-f_'


def generated(name: str) -> bool:
    symbol = name.lower().lstrip('_@').partition('@')[0].rstrip("'")
    prefix, _, address = symbol.partition('_')
    return (not name.strip(' ') or prefix == 'func' and '_' in symbol
            or prefix + '_' in ADDRESS_PREFIXES and bool(address.strip('_'))
            and not fnmatchcase(address, f'*[!{ADDRESS_CHARS}]*'))


def install(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='polished_name_update'").fetchone():
        return

    def unnamed(row):
        name = f"COALESCE(NULLIF(json_extract({row}meta, '$.real'), ''), {row}name)"
        bare = f"ltrim(lower({name}), '_@')"
        symbol = f"rtrim(substr({bare}, 1, instr({bare} || '@', '@') - 1), char(39))"
        split = f"instr({symbol}, '_')"
        prefix = f"substr({symbol}, 1, {split})"
        address = f"substr({symbol}, {split} + 1)"
        prefixes = ', '.join(repr(value) for value in ADDRESS_PREFIXES)
        return (f"(trim({name}) = '' OR {prefix} = 'func_' OR "
                f"({prefix} IN ({prefixes}) AND trim({address}, '_') != '' "
                f"AND {address} NOT GLOB '*[^{ADDRESS_CHARS}]*'))")

    # Install the guards and migrate existing flags together. Normal progress and
    # map invalidation already watch writes to readable.
    statements = [f"UPDATE functions SET readable = 0 WHERE readable = 1 AND {unnamed('')};"]
    for event in ('INSERT', 'UPDATE OF name, meta, readable'):
        statements.append(f"""
            CREATE TRIGGER IF NOT EXISTS polished_name_{event.split()[0].lower()} AFTER {event} ON functions
            WHEN NEW.readable = 1 AND {unnamed('NEW.')}
            BEGIN
                UPDATE functions SET readable = 0 WHERE id = NEW.id;
            END;
        """)
    db.executescript("BEGIN IMMEDIATE;" + "".join(statements) + "COMMIT;")

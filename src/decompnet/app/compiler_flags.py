"""Remember only explicit compiler rejection of a requested flag, scoped to the pinned compiler ID."""
import json
import re

PREFIX='rejected-compiler-flags:'


def known(net, toolchain, flags):
    row=net.store.one('SELECT value FROM settings WHERE key=?',PREFIX+toolchain)
    rejected=json.loads(row['value']) if row else []
    bad=[flag for flag in flags if flag in rejected]
    return ('Previously rejected by '+toolchain+': '+', '.join(bad)+'. No compiler run. Remove these flags or choose another compiler version.') if bad else ''


def remember(net, toolchain, flags, log):
    bad=set(re.findall(r'''(?:invalid option|unrecognized (?:command.line )?option|unknown (?:argument|option))\s*[:=]?\s*[`'"‘](-[^`'"’\s]+)[`'"’]''',log,re.I)).intersection(flags)
    if not bad:
        return
    with net.store.tx() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(PREFIX+toolchain,)).fetchone()
        previous=set(json.loads(row['value'])) if row else set()
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+toolchain,json.dumps(sorted(previous|bad)[:64])))

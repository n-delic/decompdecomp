"""Last computed answer of a slow tab, kept as JSON so the tab opens at once and refreshes behind it.

The Porting and ROM Explorer tabs recompute their whole report on every visit. The GUI now asks for the saved copy
first (`?cached=1`), draws it, then asks for the fresh one and redraws; every fresh answer is saved here. The copy
lives in the project's own settings table, so it follows the project and is never shared with another one.
"""

from __future__ import annotations

import json
import time

PREFIX = 'view-cache:'


def _key(name: str, project_id: int) -> str:
    return f'{PREFIX}{name}:{project_id}'


def load(net, proj, name: str) -> dict | None:
    """The saved answer with `cached_at`, or None when this tab was never computed (or the copy is unreadable)."""
    row = net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?', _key(name, proj['id']))
    if not row:
        return None
    try:
        saved = json.loads(row['value'])
        return saved['payload'] | {'cached_at': saved['at']}
    except (ValueError, KeyError, TypeError):
        return None


def save(net, proj, name: str, payload: dict) -> None:
    """Replace the saved answer. A failed write only costs the next visit its head start, so it never fails the tab."""
    body = {k: v for k, v in payload.items() if k != 'cached_at'}
    try:
        with net.project_store(proj['id']).tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       (_key(name, proj['id']), json.dumps({'at': time.time(), 'payload': body})))
    except Exception:
        pass

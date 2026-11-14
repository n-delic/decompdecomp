"""Expression recipes: making and applying them is `decomprepair.expression_recipes`; this keeps them, per compiler
profile, learned only after an ordinary exact verdict. Hypotheses, never acceptance rules: the judge tests them."""
import hashlib
import json

from decomprepair.expression_recipes import *  # noqa: F403 - significant, template, apply, KEYWORDS
from decomprepair.expression_recipes import apply, template

SCHEMA = '''CREATE TABLE IF NOT EXISTS expression_recipes(
 profile TEXT NOT NULL, recipe TEXT NOT NULL, successes INTEGER NOT NULL,
 PRIMARY KEY(profile,recipe))'''

def learn(net, profile, before, after):
    recipe=template(before,after)
    if not recipe:
        return
    with net.store.tx() as db:
        db.execute(SCHEMA)
        db.execute('''INSERT INTO expression_recipes VALUES(?,?,1) ON CONFLICT(profile,recipe)
                      DO UPDATE SET successes=successes+1''',(profile,json.dumps(recipe,sort_keys=True)))


def variants(net, profile, source):
    with net.store.tx() as db:
        db.execute(SCHEMA)
    rows=net.store.all('SELECT recipe FROM expression_recipes WHERE profile=? ORDER BY successes DESC LIMIT 16',profile)
    result=[]
    for row in rows:
        candidate=apply(source,json.loads(row['recipe']))
        if candidate and candidate!=source and candidate not in result:
            result.append(candidate)
    return result


def signature(net, profile):
    """New recipes invalidate a completed iterative frontier, without retrying old misses."""
    with net.store.tx() as db:
        db.execute(SCHEMA)
    rows=net.store.all('SELECT recipe FROM expression_recipes WHERE profile=? ORDER BY recipe',profile)
    return hashlib.sha256(repr([r['recipe'] for r in rows]).encode()).hexdigest()

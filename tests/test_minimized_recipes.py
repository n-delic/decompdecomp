from types import SimpleNamespace

from decompnet.app import expression_recipes
from decompnet.app import minimized_recipes as recipes
from decompnet.store import Store

BEFORE='int f(int a,int b){ return (a + b) * 2; }'
AFTER='int f(int a,int b){ return (b + a) * 2; }'


def test_minimized_recipe_needs_independent_unmatched_exact_heldout_case(tmp_path):
    proposed=recipes.propose(BEFORE,AFTER,lambda s:'b + a' in s,trials=8)
    assert proposed['recipe']
    calls=[]
    def judge(s):calls.append(s);return dict(matched='b + a' in s,score=0 if 'b + a' in s else 10)
    validated=recipes.validate(proposed['recipe'],[dict(group='heldout',source=BEFORE,judge=judge)],training_group='training',profile='profile')
    assert validated['approved'] and validated['compiles']==2
    net=SimpleNamespace(store=Store(tmp_path/'db'))
    assert recipes.learn(net,'profile',BEFORE,proposed['minimization']['source'],validated)
    assert expression_recipes.variants(net,'profile',BEFORE)==[AFTER]
    assert not recipes.learn(net,'wrong profile',BEFORE,AFTER,validated)
    assert not recipes.validate(proposed['recipe'],[dict(group='training',source=BEFORE,judge=judge)],training_group='training',profile='profile')['approved']


def test_existing_exact_or_unscored_baselines_and_small_budget_cannot_teach():
    recipe=expression_recipes.template(BEFORE,AFTER)
    for baseline in [dict(matched=True,score=0),dict(matched=False,score=None)]:
        result=recipes.validate(recipe,[dict(group='test',source=BEFORE,judge=lambda s,baseline=baseline:baseline)],training_group='train',profile='p')
        assert not result['approved']
    assert not recipes.validate(recipe,[dict(group='test',source=BEFORE,judge=lambda s:dict(matched=True))],training_group='train',profile='p',budget=1)['approved']





from decompnet.app import debug_boundary_repairs as boundaries
from decompnet.app import repair_localisation


def tagged(count=30):
    return [(boundaries.FAMILY,f'int f(){{return {i};}}') for i in range(count)]


def test_localisation_ranks_whole_nearby_copy_and_line_edits_without_discarding_broad_candidates():
    source='void f(){\n a=1;\n b=a;\n use(b);\n x=2;\n y=x;\n use(y);\n}'
    near=('dead_copy_source',source.replace('b=a;','b=a;a=0;'))
    far=('dead_copy_source',source.replace('y=x;','y=x;x=0;'))
    mapping=repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})
    pool=[far,('unrelated',source+' '),near]
    ordered=repair_localisation.order(source,pool,mapping)
    assert ordered[0]==near and set(ordered)==set(pool)
    assert repair_localisation.order(source,pool,None)==pool
    assert repair_localisation.order(source+'\n',pool,mapping)==pool


def test_localisation_reserves_broad_probes_and_does_not_split_scopes():
    source='int f(){\n int a;\n a=read();\n return a;\n}'
    mapping=repair_localisation.bind(source,{'available':True,'lines':[{'line':3}]})
    near=[('dead_copy_source',source.replace('a=read();',f'a=read();a={i};')) for i in range(8)]
    broad=('other','opaque whole-function hypothesis')
    result=repair_localisation.order(source,[broad]+near,mapping)
    assert result[3]==broad and len(result)==9

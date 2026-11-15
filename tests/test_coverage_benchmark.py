"""Ground-truth scoring is separate from the inputs visible to the CPU audit."""
from pathlib import Path

import pytest

from decompnet.app.coverage_benchmark import run, truth


def test_real_demo_corruptions_are_found_without_giving_truth_to_the_audit():
    root=Path(__file__).parents[1]/'projects/demo-rom/build'
    if not (root/'demo-quest-64.elf').exists():pytest.skip('Build the owned CC0 demo first.')
    image=(root/'demo-quest-64.z64').read_bytes()
    functions,segments=truth(image,root/'demo-quest-64.elf',0x1000,0x80000400)
    result=run(image,functions,segments,limit=8)
    assert result['ground_truth_functions']>=8 and result['sampled_functions']==8
    assert result['metrics']['truncated']=={'detected':8,'tested':8}
    assert result['metrics']['misclassified']=={'detected':8,'tested':8}
    assert all(not r['search_limited'] for r in result['cases'])
    with pytest.raises(ValueError,match='mapping'):truth(bytes(len(image)),root/'demo-quest-64.elf',0x1000,0x80000400)

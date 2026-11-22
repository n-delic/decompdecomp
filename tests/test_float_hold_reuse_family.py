"""The float hold/inline bundles reach the permuter lane and the agents like every other family."""
from decompnet.app import draft_repairs
from decomprepair import float_hold_reuse_repairs

SOURCE = '''extern f32 A[], B[], T[];
void func_80001000(s16 *a, s16 *b, u32 x, u32 y, u32 z, s16 *c, u32 w, s16 *d) {
 f32 p,q,r,t,v; u32 i;
 i=x>>16;
 p=(f32)(x&0xffff)*0.5f;
 q=(1.0f-p)*A[i]+p*B[i];
 i=z>>22;
 p=(f32)(z&0x3fffff)*0.25f;
 t=(1.0f-p)*T[i]+p*T[i+1];
 v=q*t;
 *c=(s32)(v*23195.76f);
 i=z>>22;
 p=(f32)(z&0x3fffff)*0.25f;
 r=q*((1.0f-p)*T[i]+p*T[i+1]);
 if(y==0x800000) { *a=(s32)(r*23195.76f); v=r*-23195.76f; *b=(s32)v; }
 else {
 i=y>>22; p=(f32)(y&0x3fffff)*0.25f;
 *a=(s32)(r*((1.0f-p)*T[i]+p*T[i+1])*32767.0f);
 i=y>>22; p=(f32)(y&0x3fffff)*0.25f;
 v=r*((1.0f-p)*T[i]+p*T[i+1])*32767.0f;
 *b=(s32)v;
 }
 i=w>>16; p=(f32)(w&0xffff)*0.5f;
 *d=(s32)(((1.0f-p)*A[i]+p*B[i])*23195.76f);
}
'''


def test_offered_to_the_permuter_and_the_agents():
    labels = {}
    draft_repairs.variants(SOURCE, 'func_80001000', 'mips', labels=labels, limit=None)
    assert float_hold_reuse_repairs.FAMILY in set(labels.values())
    assert set(float_hold_reuse_repairs.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    assert any(' q = (1.0f-p)*A[i]+p*B[i]; *d=(s32)(q*23195.76f);' in text for text in labels)

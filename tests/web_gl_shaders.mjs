// The WebGL renderer's shaders (consoles/n64/port_wasm/web/gl.js), made from the same other modes and combine
// words render.c reads: the blender's frame-buffer reads become GL's blending, the rest is the shader's.
import assert from "node:assert/strict";
import { _testing } from "../src/decompnet/consoles/n64/port_wasm/web/gl.js";

const { blenderPlan, combinerUses, cycleCode, fragmentShader, programKey } = _testing;
const FORCE_BL = 0x4000;

// G_RM_XLU_SURF (one cycle): IN * IN_ALPHA + MEMORY * (1 - IN_ALPHA), force blend: the shader's share, its weight
// for the frame buffer in the alpha (GL's ONE, SRC_ALPHA), both 5-bit as the RDP takes them; at full alpha, IN alone
let plan = blenderPlan(1 << 22 | FORCE_BL, 0);
assert.equal(plan.blend, true);
assert.match(plan.code, /int A = px\.a, B = 255 - A, a5 = A >> 3, b5 = B >> 3/);
assert.match(plan.code, /px\.a >= 255 \? vec4\(vec3\(px\.rgb\) \/ 255\.0, 0\.0\)/);
assert.match(plan.code, /float\(mb\) \/ 32\.0/);
// G_RM_ADD-like: IN * FOG_ALPHA + MEMORY * 1, no partial reject
plan = blenderPlan(1 << 26 | 1 << 22 | 2 << 18 | FORCE_BL, 0);
assert.equal(plan.blend, true);
assert.match(plan.code, /int A = FOG\.a, B = 255/);
assert.doesNotMatch(plan.code, /px\.a >= 255/);
// opaque (no force blend): the pixel as it is, no GL blending
plan = blenderPlan(1 << 22, 0);
assert.equal(plan.blend, false);
assert.equal(plan.write, true);
// a last cycle whose P is the frame buffer, without force blend, leaves the colour as it was
plan = blenderPlan(1 << 30, 0);
assert.equal(plan.write, false);
// two cycles: the first fogs in the shader (FOG * SHADE_ALPHA + IN * (1 - SHADE_ALPHA)), shifted down 5
plan = blenderPlan((3 << 30 | 2 << 26) >>> 0, 1);
assert.equal(plan.blend, false);
assert.match(plan.code, /int A = SH\.a/);
assert.match(plan.code, /\(FOG\.rgb \* a5 \+ px\.rgb \* mb\) >> 5/);

// the combiner: (A - B) * C + D, one cycle reading the second cycle's selectors (as render.c's combiner())
const TEXEL0 = { c0: 15 << 20 | 31 << 15 | 7 << 12 | 7 << 9 | 15 << 5 | 31, c1: (15 << 28 | 15 << 24 | 7 << 21 | 7 << 18 | 1 << 15 | 7 << 12 | 1 << 9 | 1 << 6 | 7 << 3 | 1) >>> 0 };
assert.equal(cycleCode(TEXEL0.c0, TEXEL0.c1, 1),
  "ivec4(cc3(ivec3(0), ivec3(0), ivec3(0), T0.rgb), cc1(0, 0, 0, T0.a))");
// the second cycle's alpha: A at bit 21, B at 3, C at 18, D at 0 (TEXEL0 * SHADE's alpha)
assert.match(cycleCode(0, (1 << 21 | 7 << 3 | 4 << 18 | 7) >>> 0, 1), /cc1\(T0\.a, 0, SH\.a, 0\)/);
assert.deepEqual(combinerUses(TEXEL0.c0, TEXEL0.c1, 0), { t1: false, noise: false });
// TEXEL1 as D in the second cycle: the second tile is read
const TEXEL1 = { c0: TEXEL0.c0, c1: (TEXEL0.c1 & ~(7 << 6)) | 2 << 6 };
assert.equal(combinerUses(TEXEL1.c0, TEXEL1.c1, 0).t1, true);
// in two cycles the second cycle's TEXEL0 is the first's TEXEL1
assert.equal(combinerUses(TEXEL0.c0, TEXEL0.c1, 1).t1, true);

// a program per setting: the same words, the same program; what only changes uniforms doesn't make another
const draw = { kind: 0, flags: 16, modeH: 0, modeL: 1 << 22, ...TEXEL0 };
assert.equal(programKey(draw), programKey({ ...draw, modeL: draw.modeL | 0x30 })); // depth bits: GL state
assert.notEqual(programKey(draw), programKey({ ...draw, modeL: draw.modeL | 1 })); // alpha compare: the shader's
const shader = fragmentShader(draw);
assert.match(shader, /texels\(t0, t1, lodFrac, false\)/);
assert.doesNotMatch(shader, /gl_FragDepth/);
assert.match(fragmentShader({ ...draw, modeL: draw.modeL | 4 }), /gl_FragDepth = u_primZ/); // G_ZS_PRIM
assert.match(fragmentShader({ ...draw, modeH: 0x10000 }), /dFdx\(v_st\)/); // G_TL_LOD
assert.match(fragmentShader({ kind: 1, flags: 0, modeH: 2 << 20, modeL: 1, c0: 0, c1: 0 }), /t\.a < I\(u_blend\)\.a/);
console.log("ok");

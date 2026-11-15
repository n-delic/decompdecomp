/* The N64 browser build's GPU renderer (WebGL2), for the player who chooses it over the software one: render.c's GPU
   path (render_gpu.h) reads and transforms the display lists as for software, then sends each run of primitives
   here with the RDP's state, and this draws them. The colour combiner, the blender, alpha compare, the samplers'
   wrapping and filtering and the depth test are made into a shader per setting, from the same other modes and
   combine words render.c reads, and follow it: where they differ, render.c is right and this is the bug.

   Frame buffers are textures here, one per address the game draws to (`cimg`), at its size times the chosen
   resolution; depth buffers one per z image. What's shown is the frame buffer the VI was given (present), or, for a
   frame the CPU wrote (RDRAM's, which render.c decides), its pixels as software shows them (showRGB).

   What isn't drawn here (render_gpu.h says why): the RDP's coverage anti-aliasing and dither, and the VI's filters
   besides gamma. The combiner's input lists agree with noclip.website's N64 RDP (MIT, Jasper St. Pierre), which
   draws several N64 games' display lists in WebGL, but for C's 15: K5 here, as render.c and the RDP take it. */

const COMMAND = { TEXTURE: 1, DRAW: 2, CLEAR_DEPTH: 3 };
const KIND = { COMBINE: 0, COPY: 1, FILL: 2 };
const FLAG = { DEPTH: 1, ONE_TEXEL: 2, POINT: 4, COPY_ALPHA: 8, TEXTURED: 16 };
const VERTEX = 10; // floats: x, y, z, w, s, t, r, g, b, a (render_gpu.h's GPU_VERTEX)
const DRAW_WORDS = 156; // render_gpu.h's GpuDraw, every field 4 bytes
const TILE_WORDS = 13;

const VERTEX_SHADER = `#version 300 es
precision highp float;
layout(location = 0) in vec4 a_pos;   // the frame's pixels (x, y), depth 0 to 1, w
layout(location = 1) in vec2 a_st;
layout(location = 2) in vec4 a_shade;
uniform vec2 u_size;
out vec2 v_st;
out vec4 v_shade;
void main() {
  float w = a_pos.w;
  vec2 ndc = vec2(a_pos.x / u_size.x * 2.0 - 1.0, 1.0 - a_pos.y / u_size.y * 2.0);
  gl_Position = vec4(ndc * w, (a_pos.z * 2.0 - 1.0) * w, w);
  v_st = a_st;
  v_shade = a_shade;
}`;

/* ---------------------------------------------------------------- the combiner, as render.c's combine() */

/* An RGB input: `which` 0 to 3 for A, B, C, D (render.c's rgb_slot). */
function rgbInput(which, sel) {
  switch (sel) {
    case 0: return "comb.rgb"; case 1: return "T0.rgb"; case 2: return "T1.rgb"; case 3: return "u_prim.rgb";
    case 4: return "shade.rgb"; case 5: return "u_env.rgb";
    case 6: return which === 1 ? "u_keyCenter.rgb" : which === 2 ? "u_keyScale.rgb" : "vec3(1.0)";
    case 7: return which === 0 ? "vec3(nz)" : which === 1 ? "vec3(u_misc.x)" : which === 2 ? "vec3(comb.a)" : "vec3(0.0)";
  }
  if (which !== 2) return "vec3(0.0)";
  return ({ 8: "vec3(T0.a)", 9: "vec3(T1.a)", 10: "vec3(u_prim.a)", 11: "vec3(shade.a)", 12: "vec3(u_env.a)",
    13: "vec3(lodFrac)", 14: "vec3(u_misc.z)", 15: "vec3(u_misc.y)" })[sel] || "vec3(0.0)";
}
/* An alpha input (render.c's alpha_slot). */
function alphaInput(which, sel) {
  switch (sel) {
    case 0: return which === 2 ? "lodFrac" : "comb.a"; case 1: return "T0.a"; case 2: return "T1.a";
    case 3: return "u_prim.a"; case 4: return "shade.a"; case 5: return "u_env.a";
    case 6: return which === 2 ? "u_misc.z" : "1.0";
  }
  return "0.0";
}
/* The selectors of a cycle, as G_SETCOMBINE lays them out: [a, b, c, d] for RGB and alpha. */
function selectors(c0, c1, cycle) {
  if (cycle === 0) return { rgb: [(c0 >>> 20) & 15, (c1 >>> 28) & 15, (c0 >>> 15) & 31, (c1 >>> 15) & 7],
    alpha: [(c0 >>> 12) & 7, (c1 >>> 12) & 7, (c0 >>> 9) & 7, (c1 >>> 9) & 7] };
  return { rgb: [(c0 >>> 5) & 15, (c1 >>> 24) & 15, c0 & 31, (c1 >>> 6) & 7],
    alpha: [(c1 >>> 21) & 7, (c1 >>> 18) & 7, (c1 >>> 3) & 7, c1 & 7] };
}
function cycleCode(c0, c1, cycle) {
  const { rgb, alpha } = selectors(c0, c1, cycle);
  const r = rgb.map((sel, w) => rgbInput(w, sel)), a = alpha.map((sel, w) => alphaInput(w, sel));
  return `clamp(vec4((${r[0]} - ${r[1]}) * ${r[2]} + ${r[3]}, (${a[0]} - ${a[1]}) * ${a[2]} + ${a[3]}), 0.0, 1.0)`;
}
/* Whether the combiner reads TEXEL1, or noise (render.c's combiner_uses). */
function combinerUses(c0, c1, cyc) {
  let t1 = false, noise = false;
  for (let cycle = 0; cycle < 2; cycle++) {
    if (cyc === 0 ? cycle === 0 : cyc !== 1) continue;
    const { rgb: [a, b, c, d], alpha: [aa, ab, ac, ad] } = selectors(c0, c1, cycle);
    const t0InSecond = cyc === 1 && cycle === 1 && ([a, b, c, d, aa, ab, ac, ad].includes(1) || c === 8);
    if ([a, b, c, d, aa, ab, ac, ad].includes(2) || c === 9 || t0InSecond) t1 = true;
    if (a === 7) noise = true;
  }
  return { t1, noise };
}

/* ---------------------------------------------------------------- the blender, as render.c's blender() */

/* What the shader leaves for GL's blending: P * A + M * B with M (or P) the frame buffer's colour. Its alpha is
   taken as 1 (the targets' alpha isn't written). Returns the shader's code and GL's blend factors (null: none). */
function blenderPlan(modeL, cyc) {
  const two = cyc === 1, pick = (n) => ["px", "px", "u_blend", "u_fog"][n] + ".rgb";
  const at = (cycle) => {
    const s = cycle === 0 ? 30 : 28;
    return { p: (modeL >>> s) & 3, a: (modeL >>> (s - 4)) & 3, m: (modeL >>> (s - 8)) & 3, b: (modeL >>> (s - 12)) & 3 };
  };
  const alpha = (n) => ["px.a", "u_fog.a", "shade.a", "0.0"][n];
  let code = "";
  if (two) { // the first cycle (fog) in the shader; the frame buffer it may name is taken as the pixel itself
    const { p, a, m, b } = at(0);
    code += `{ float A = ${alpha(a)}; float B = ${["1.0 - A", "1.0", "1.0", "0.0"][b]};
      px = vec4((${pick(p)} * A + ${pick(m)} * B) / max(A + B, 1.0), px.a); }\n`;
  }
  const last = at(two ? 1 : 0);
  if (!(modeL & 0x4000)) { // no force blend: the last cycle's P passes through
    if (last.p === 1) return { code, blend: null, write: false };
    code += `px.rgb = ${pick(last.p)};\n`;
    return { code, blend: null, write: true };
  }
  const { p, a, m, b } = last, pMem = p === 1, mMem = m === 1;
  code += `float A = ${alpha(a)};\n`;
  if (pMem && mMem) return { code, blend: null, write: false };
  if (!pMem && !mMem) {
    code += `{ float B = ${["1.0 - A", "1.0", "1.0", "0.0"][b]}; px.rgb = (${pick(p)} * A + ${pick(m)} * B) / max(A + B, 1.0); }\n`;
    return { code, blend: null, write: true };
  }
  if (!pMem) { // P * A + memory * B
    code += `px = vec4(${pick(p)} * A, A);\n`;
    return { code, blend: ["ONE", ["ONE_MINUS_SRC_ALPHA", "DST_ALPHA", "ONE", "ZERO"][b]], write: true };
  }
  code += `px = vec4(${pick(m)} * ${["(1.0 - A)", "1.0", "1.0", "0.0"][b]}, A);\n`; // memory * A + M * B
  return { code, blend: ["ONE", "SRC_ALPHA"], write: true };
}

/* ---------------------------------------------------------------- the fragment shader */

function fragmentShader(d) {
  const { kind, flags, modeH, modeL, c0, c1 } = d, cyc = (modeH >>> 20) & 3;
  const textured = (flags & FLAG.TEXTURED) !== 0;
  const filter = kind === KIND.COPY || (flags & FLAG.POINT) ? 0 : (modeH >>> 12) & 3;
  const lodOn = kind === KIND.COMBINE && (modeH & 0x10000) !== 0;
  const uses = combinerUses(c0, c1, cyc);
  const zsPrim = kind === KIND.COMBINE && (modeL & 4) !== 0;
  let body;
  if (kind === KIND.FILL) body = "o = u_fill;";
  else if (kind === KIND.COPY) {
    const test = (flags & FLAG.COPY_ALPHA) ? "if (t.a <= 0.0) discard;" : (modeL & 3) === 1 ? "if (t.a <= u_blend.a) discard;" : "";
    body = `vec4 t = textureTile(u_texInfo.x, v_st); ${test} o = vec4(t.rgb, 1.0);`;
  } else {
    const plan = blenderPlan(modeL, cyc);
    const comb = cyc === 1
      ? `vec4 comb = vec4(0.0); vec4 T0 = t0, T1 = t1; comb = ${cycleCode(c0, c1, 0)};
         T0 = t1; T1 = t0; vec4 c = ${cycleCode(c0, c1, 1)};`
      : `vec4 comb = vec4(0.0); vec4 T0 = t0, T1 = t1; vec4 c = ${cycleCode(c0, c1, 1)};`;
    const ac = modeL & 3;
    body = `
      vec4 shade = v_shade;
      float nz = ${uses.noise || ac === 3 ? "rnd()" : "0.0"};
      vec4 t0 = vec4(1.0), t1 = vec4(1.0); float lodFrac = 0.0;
      ${textured ? `texels(t0, t1, lodFrac, ${uses.t1 && !(flags & FLAG.ONE_TEXEL) ? "true" : "false"});` : ""}
      ${comb}
      ${ac === 1 ? "if (c.a <= u_blend.a) discard;" : ac === 3 ? "if (c.a <= rnd()) discard;" : ""}
      ${modeL & 0x3000 ? `float cvg = ${modeL & 0x1000 ? "floor(8.0 * c.a + 0.5)" : "8.0"};
        ${modeL & 0x2000 ? "c.a = cvg / 8.0;" : ""} if (cvg <= 0.0) discard;` : ""}
      vec4 px = c;
      ${plan.code}
      o = px;
      ${zsPrim ? "gl_FragDepth = u_primZ;" : ""}`;
  }
  return `#version 300 es
precision highp float;
precision highp int;
in vec2 v_st;
in vec4 v_shade;
out vec4 o;
uniform sampler2D u_tex[8];
uniform vec4 u_tileF[8];   // the corner (uls, ult) and the shift's factors
uniform ivec4 u_tileI[8];  // the texture's size, the tile's size
uniform ivec4 u_tileJ[8];  // cms, cmt, masks, maskt
uniform vec4 u_prim, u_env, u_fog, u_blend, u_keyCenter, u_keyScale, u_fill;
uniform vec4 u_misc;       // K4, K5, the primitive's LOD fraction, its minimum level
uniform ivec4 u_texInfo;   // G_TEXTURE's tile, its levels, the noise's seed, the resolution's scale
uniform float u_primZ;

float rnd() {
  float r = fract(sin(dot(gl_FragCoord.xy + float(u_texInfo.z), vec2(12.9898, 78.233))) * 43758.5453);
  return floor(r * 256.0) / 255.0;
}
vec4 fetch(int i, ivec2 p) {
  p = clamp(p, ivec2(0), u_tileI[i].xy - 1);
  if (i == 0) return texelFetch(u_tex[0], p, 0);
  if (i == 1) return texelFetch(u_tex[1], p, 0);
  if (i == 2) return texelFetch(u_tex[2], p, 0);
  if (i == 3) return texelFetch(u_tex[3], p, 0);
  if (i == 4) return texelFetch(u_tex[4], p, 0);
  if (i == 5) return texelFetch(u_tex[5], p, 0);
  if (i == 6) return texelFetch(u_tex[6], p, 0);
  return texelFetch(u_tex[7], p, 0);
}
/* render.c's WRAP_COORD: clamped (its clamp bit, or no mask), then wrapped or mirrored inside the mask */
int wrapped(int s, int size, int cm, int mask) {
  if ((cm & 2) != 0 || mask == 0) s = clamp(s, 0, size - 1);
  if (mask != 0) {
    int m = 1 << mask;
    s = ((cm & 1) != 0 && ((s >> mask) & 1) != 0) ? m - 1 - (s & (m - 1)) : s & (m - 1);
  }
  return max(s, 0);
}
/* render.c's sample(): point, the RDP's three-texel bilinear, or the average of four */
vec4 textureTile(int i, vec2 st) {
  vec4 f = u_tileF[i]; ivec4 n = u_tileI[i], w = u_tileJ[i];
  float s = st.x * f.z, t = st.y * f.w, s0 = floor(s), t0 = floor(t);
  int si = int(s0) - int(f.x), ti = int(t0) - int(f.y);
  int c0 = wrapped(si, n.z, w.x, w.z), r0 = wrapped(ti, n.w, w.y, w.w);
  ${filter === 0 ? "return fetch(i, ivec2(c0, r0));" : `
  int c1 = wrapped(si + 1, n.z, w.x, w.z), r1 = wrapped(ti + 1, n.w, w.y, w.w);
  vec4 c10 = fetch(i, ivec2(c1, r0)), c01 = fetch(i, ivec2(c0, r1));
  ${filter === 3 ? "return (fetch(i, ivec2(c0, r0)) + c10 + c01 + fetch(i, ivec2(c1, r1))) / 4.0;" : `
  float fs = s - s0, ft = t - t0;
  if (fs + ft < 1.0) { vec4 a = fetch(i, ivec2(c0, r0)); return a + fs * (c10 - a) + ft * (c01 - a); }
  vec4 a = fetch(i, ivec2(c1, r1)); return a + (1.0 - fs) * (c01 - a) + (1.0 - ft) * (c10 - a);`}`}
}
/* render.c's texels_at: TEXEL0 and TEXEL1, by the level of detail with G_TL_LOD */
void texels(out vec4 t0, out vec4 t1, out float lodFrac, bool second) {
  int tile = u_texInfo.x, a = tile, b = tile + 1;
  lodFrac = 0.0;
  ${lodOn ? `
  vec2 dx = dFdx(v_st), dy = dFdy(v_st);
  float lod = max(max(abs(dx.x), abs(dx.y)), max(abs(dy.x), abs(dy.y))) * float(u_texInfo.w);
  int detail = ${(modeH & 0x40000) ? 1 : 0}, maxLevel = u_texInfo.y;
  if (lod < u_misc.w) lod = u_misc.w;
  if (lod < 1.0) {
    lodFrac = ${(modeH & 0x40000) ? "1.0 - lod" : (modeH & 0x20000) ? "lod - 1.0" : "0.0"};
  } else {
    int level = 0;
    for (int k = 0; k < 7; k++) if (level < maxLevel && lod >= float(2 << level)) level++;
    if (level >= maxLevel) { level = maxLevel; lodFrac = 1.0; } else lodFrac = lod / float(1 << level) - 1.0;
    a = tile + level + detail; b = a + 1;
    if (b > tile + maxLevel + detail) b = a;
  }` : ""}
  t0 = textureTile(a & 7, v_st);
  t1 = second ? textureTile(b & 7, v_st) : t0;
}
void main() {
  ${body}
}`;
}

/* The program's key: everything its code is made from. */
function programKey(d) {
  const cyc = (d.modeH >>> 20) & 3;
  if (d.kind === KIND.FILL) return "fill";
  if (d.kind === KIND.COPY) return `copy ${d.flags & FLAG.COPY_ALPHA} ${d.modeL & 3}`;
  return [d.flags & (FLAG.ONE_TEXEL | FLAG.POINT | FLAG.TEXTURED), d.c0, d.c1, cyc, (d.modeH >>> 12) & 3,
    d.modeH & 0x70000, d.modeL & 0xFFFF7007].join(" ");
}

const PRESENT_VERTEX = `#version 300 es
layout(location = 0) in vec2 a_pos;
uniform vec4 u_area; // the texture's corner and size used, in 0 to 1
out vec2 v_uv;
void main() { v_uv = u_area.xy + (a_pos * 0.5 + 0.5) * u_area.zw; gl_Position = vec4(a_pos, 0.0, 1.0); }`;
const PRESENT_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_image;
uniform int u_gamma;
out vec4 o;
void main() { vec3 c = texture(u_image, v_uv).rgb; o = vec4(u_gamma != 0 ? sqrt(c) : c, 1.0); }`;

/* ---------------------------------------------------------------- the renderer */

export function gpuSupported() {
  try { return !!document.createElement("canvas").getContext("webgl2"); } catch { return false; }
}

let renderers = 0;

export class GpuRenderer {
  /* `canvas`: where frames are shown; `scale`: the resolution, times the console's (1 to 4). */
  constructor(canvas, scale = 2) {
    const gl = canvas.getContext("webgl2", { antialias: false, depth: false, alpha: false, preserveDrawingBuffer: true });
    if (!gl) throw new Error("This browser has no WebGL 2: choose the software renderer.");
    this.gl = gl;
    this.id = ++renderers; // the host sends a renderer it hasn't seen every texture again
    this.canvas = canvas;
    this.scale = scale;
    this.targets = new Map(); // frame buffer address -> {fbo, tex, w, h, depth}
    this.depths = new Map();  // `${z image} ${w}x${h}` -> {rb, zimg, clear}
    this.textures = [];       // id -> WebGLTexture
    this.programs = new Map();
    this.seed = 0;
    this.stats = { draws: 0, triangles: 0, textures: 0, gpuFrames: 0, rdramFrames: 0 }; // for ?verbose and tests
    this.vbo = gl.createBuffer();
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 4, gl.FLOAT, false, VERTEX * 4, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, VERTEX * 4, 16);
    gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 4, gl.FLOAT, false, VERTEX * 4, 24);
    this.quadVao = gl.createVertexArray();
    gl.bindVertexArray(this.quadVao);
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    gl.bindVertexArray(null);
    this.present_ = this.link(PRESENT_VERTEX, PRESENT_FRAGMENT);
    this.rgbTex = gl.createTexture();
    this.blank = this.texture(1, 1, new Uint8Array([255, 255, 255, 255]));
  }

  link(vs, fs) {
    const gl = this.gl, p = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error("A WebGL shader didn't compile: " + gl.getShaderInfoLog(s));
      gl.attachShader(p, s);
    }
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("A WebGL shader didn't link: " + gl.getProgramInfoLog(p));
    const u = {};
    for (let i = 0, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i < n; i++) {
      const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, "");
      u[name] = gl.getUniformLocation(p, name);
    }
    return { p, u };
  }

  texture(w, h, pixels) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    return t;
  }

  program(d) {
    const key = programKey(d);
    let prog = this.programs.get(key);
    if (!prog) {
      prog = this.link(VERTEX_SHADER, fragmentShader(d));
      this.gl.useProgram(prog.p);
      this.gl.uniform1iv(prog.u.u_tex, [0, 1, 2, 3, 4, 5, 6, 7]);
      this.programs.set(key, prog);
    }
    return prog;
  }

  /* The frame buffer at `addr`, `w` x `h` console pixels: made (black) the first time, or again at another size. */
  target(addr, w, h) {
    const gl = this.gl, s = this.scale;
    let t = this.targets.get(addr);
    if (t && (t.w !== w || t.h !== h)) { gl.deleteFramebuffer(t.fbo); gl.deleteTexture(t.tex); t = null; }
    if (!t) {
      t = { w, h, fbo: gl.createFramebuffer(), tex: gl.createTexture(), depth: null, drawn: false };
      gl.bindTexture(gl.TEXTURE_2D, t.tex);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, w * s, h * s);
      for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
        [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
      gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t.tex, 0);
      gl.disable(gl.SCISSOR_TEST);
      gl.colorMask(true, true, true, true);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      this.targets.set(addr, t);
      this.bound = null;
    }
    return t;
  }

  /* The depth buffer of z image `zimg` for a target of `w` x `h`: cleared (to the farthest) when made. */
  depth(zimg, w, h) {
    const gl = this.gl, key = `${zimg} ${w}x${h}`;
    let d = this.depths.get(key);
    if (!d) {
      d = { zimg, rb: gl.createRenderbuffer(), clear: true };
      gl.bindRenderbuffer(gl.RENDERBUFFER, d.rb);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w * this.scale, h * this.scale);
      this.depths.set(key, d);
    }
    return d;
  }

  bind(d) {
    const gl = this.gl, t = this.target(d.cimg, d.width, d.height), z = this.depth(d.zimg, d.width, d.height);
    if (this.bound !== t) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
      gl.viewport(0, 0, t.w * this.scale, t.h * this.scale);
      this.bound = t;
    }
    if (t.depth !== z) {
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, z.rb);
      t.depth = z;
    }
    if (z.clear) {
      gl.disable(gl.SCISSOR_TEST);
      gl.depthMask(true);
      gl.clearDepth(1);
      gl.clear(gl.DEPTH_BUFFER_BIT);
      z.clear = false;
    }
    t.drawn = true;
    return t;
  }

  /* render_gpu.h's commands, from the host's memory: `ptr` the commands, `draws`, `vertices` and `pixels` where
     the draws, their vertices and the textures' texels are (the commands name them by index). */
  run(buffer, ptr, words, draws, vertices, pixels) {
    const gl = this.gl;
    if (this.buffer !== buffer) {
      this.buffer = buffer;
      this.u32 = new Uint32Array(buffer);
      this.f32 = new Float32Array(buffer);
      this.i32 = new Int32Array(buffer);
      this.u8 = new Uint8Array(buffer);
    }
    const u32 = this.u32, at = ptr / 4;
    let verticesUploaded = false;
    this.bound = null;
    gl.bindVertexArray(this.vao);
    gl.colorMask(true, true, true, false);
    this.seed = (this.seed + 1) & 0xffff;
    for (let i = 0; i < words; i += 5) {
      const op = u32[at + i], a = u32[at + i + 1], b = u32[at + i + 2], c = u32[at + i + 3], e = u32[at + i + 4];
      if (op === COMMAND.TEXTURE) {
        let t = this.textures[a];
        if (!t) t = this.textures[a] = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        const from = pixels + e * 4;
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, b, c, 0, gl.RGBA, gl.UNSIGNED_BYTE, this.u8.subarray(from, from + b * c * 4));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        this.stats.textures++;
      } else if (op === COMMAND.CLEAR_DEPTH) {
        for (const d of this.depths.values()) if (d.zimg === a) d.clear = true;
      } else if (op === COMMAND.DRAW) {
        if (!verticesUploaded) { // every draw's vertices, sent once a run
          let end = 0;
          for (let k = i; k < words; k += 5) {
            if (u32[at + k] !== COMMAND.DRAW) continue;
            const dw = draws / 4 + u32[at + k + 1] * DRAW_WORDS;
            end = Math.max(end, u32[dw + 154] + u32[dw + 155]);
          }
          gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
          gl.bufferData(gl.ARRAY_BUFFER, this.f32.subarray(vertices / 4, vertices / 4 + end * VERTEX), gl.STREAM_DRAW);
          verticesUploaded = true;
        }
        this.draw(draws / 4 + a * DRAW_WORDS);
      }
    }
    gl.bindVertexArray(null);
  }

  draw(w) {
    const gl = this.gl, u32 = this.u32, f32 = this.f32, i32 = this.i32;
    const count = u32[w + 155];
    if (!count) return;
    const d = { kind: u32[w], flags: u32[w + 1], c0: u32[w + 2], c1: u32[w + 3], modeH: u32[w + 4], modeL: u32[w + 5],
      cimg: u32[w + 45], width: u32[w + 46], height: u32[w + 47], zimg: u32[w + 49] };
    if (!d.width || !d.height) return;
    const t = this.bind(d), s = this.scale;
    const prog = this.program(d), un = prog.u;
    gl.useProgram(prog.p);
    gl.uniform2f(un.u_size, d.width, d.height);
    const colour = (name, off) => { if (un[name]) gl.uniform4f(un[name], f32[w + off], f32[w + off + 1], f32[w + off + 2], f32[w + off + 3]); };
    colour("u_prim", 6); colour("u_env", 10); colour("u_fog", 14); colour("u_blend", 18);
    colour("u_keyCenter", 22); colour("u_keyScale", 26); colour("u_fill", 35);
    if (un.u_misc) gl.uniform4f(un.u_misc, f32[w + 30], f32[w + 31], f32[w + 32], f32[w + 33]);
    if (un.u_primZ) gl.uniform1f(un.u_primZ, f32[w + 34]);
    if (un.u_texInfo) gl.uniform4i(un.u_texInfo, i32[w + 39], i32[w + 40], this.seed, s);
    if (d.flags & FLAG.TEXTURED) {
      const tf = new Float32Array(32), ti = new Int32Array(32), tj = new Int32Array(32);
      for (let k = 0; k < 8; k++) {
        const o = w + 50 + k * TILE_WORDS;
        gl.activeTexture(gl.TEXTURE0 + k);
        gl.bindTexture(gl.TEXTURE_2D, (u32[o] && this.textures[u32[o]]) || this.blank);
        tf.set([f32[o + 3], f32[o + 4], f32[o + 5], f32[o + 6]], k * 4);
        ti.set(u32[o] ? [i32[o + 1], i32[o + 2], i32[o + 7], i32[o + 8]] : [1, 1, 1, 1], k * 4);
        tj.set([i32[o + 9], i32[o + 10], i32[o + 11], i32[o + 12]], k * 4);
      }
      gl.activeTexture(gl.TEXTURE0);
      if (un.u_tileF) gl.uniform4fv(un.u_tileF, tf);
      if (un.u_tileI) gl.uniform4iv(un.u_tileI, ti);
      if (un.u_tileJ) gl.uniform4iv(un.u_tileJ, tj);
    }
    // the scissor, in the target's pixels (GL's rows count up from the bottom)
    const x0 = Math.max(0, i32[w + 41]), y0 = Math.max(0, i32[w + 42]);
    const x1 = Math.min(d.width, i32[w + 43]), y1 = Math.min(d.height, i32[w + 44]);
    if (x1 <= x0 || y1 <= y0) return;
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(x0 * s, (t.h - y1) * s, (x1 - x0) * s, (y1 - y0) * s);
    // depth, as render.c's plot(): compared (decals only at the surface's own depth) and written by other mode L
    const zsPrim = (d.modeL & 4) !== 0, zcmp = (d.modeL & 0x10) !== 0, zupd = (d.modeL & 0x20) !== 0;
    const depthOn = d.kind === KIND.COMBINE && ((d.flags & FLAG.DEPTH) || zsPrim) && (zcmp || zupd);
    if (depthOn) {
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(zcmp ? gl.LEQUAL : gl.ALWAYS);
      gl.depthMask(zupd);
      if (((d.modeL >>> 10) & 3) === 3) { gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(-1, -4); }
      else gl.disable(gl.POLYGON_OFFSET_FILL);
    } else {
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.POLYGON_OFFSET_FILL);
    }
    // the blender's last cycle where it reads the frame buffer
    const plan = d.kind === KIND.COMBINE ? blenderPlan(d.modeL, (d.modeH >>> 20) & 3) : { blend: null, write: true };
    if (plan.blend) { gl.enable(gl.BLEND); gl.blendFunc(gl[plan.blend[0]], gl[plan.blend[1]]); } else gl.disable(gl.BLEND);
    gl.colorMask(plan.write, plan.write, plan.write, false);
    gl.drawArrays(gl.TRIANGLES, u32[w + 154], count);
    this.stats.draws++;
    this.stats.triangles += count / 3;
  }

  /* The frame buffer at `addr` shown, `w` x `h` of it: 1, or 0 when the GPU never drew it (the caller shows RDRAM's). */
  present(addr, w, h, flags) {
    const t = this.targets.get(addr);
    if (!t || !t.drawn) return 0;
    this.show(t.tex, w * this.scale, h * this.scale, [0, 1 - Math.min(h, t.h) / t.h, Math.min(w, t.w) / t.w, Math.min(h, t.h) / t.h],
      (flags & 1) !== 0);
    this.stats.gpuFrames++;
    return 1;
  }

  /* A frame as RGB (software's, from RDRAM): shown at the same size as the GPU's. */
  showRGB(rgb, w, h) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.rgbTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB8, w, h, 0, gl.RGB, gl.UNSIGNED_BYTE, rgb);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.show(this.rgbTex, w * this.scale, h * this.scale, [0, 1, 1, -1], false); // its first row at the top
    this.stats.rdramFrames++;
  }

  show(tex, w, h, area, gamma) {
    const gl = this.gl, c = this.canvas;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.bound = null;
    gl.viewport(0, 0, w, h);
    gl.disable(gl.SCISSOR_TEST); gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND); gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.colorMask(true, true, true, true);
    gl.useProgram(this.present_.p);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.uniform1i(this.present_.u.u_image, 0);
    gl.uniform4f(this.present_.u.u_area, area[0], area[1], area[2], area[3]);
    gl.uniform1i(this.present_.u.u_gamma, gamma ? 1 : 0);
    gl.bindVertexArray(this.quadVao);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindVertexArray(null);
    if (this.onShow) this.onShow();
  }
}

export const _testing = { fragmentShader, programKey, blenderPlan, combinerUses, cycleCode };

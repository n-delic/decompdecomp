// The browser build's runtime places data segments itself (web/runtime.js, placeDataOurselves): Safari places a
// segment above 2 GB as if its address were negative and refuses the module, and every segment of a game's browser
// build is above 2 GB. A hand-made module: a segment at 16, one at 0x80000010 (encoded as a negative i32.const, as
// a compiler writes it), and one already passive, which stays as it is.
import assert from "node:assert/strict";
import { placeDataOurselves } from "../src/decompnet/port/wasm/web/runtime.js";

const uleb = (n) => { const out = []; do { let b = n & 0x7f; n >>>= 7; if (n) b |= 0x80; out.push(b); } while (n); return out; };
const sleb = (n) => {
  const out = [];
  for (;;) {
    const b = n & 0x7f; n >>= 7;
    if ((n === 0 && !(b & 0x40)) || (n === -1 && (b & 0x40))) { out.push(b); return out; }
    out.push(b | 0x80);
  }
};
const section = (id, body) => [id, ...uleb(body.length), ...body];
const name = (s) => [...uleb(s.length), ...new TextEncoder().encode(s)];
const imports = section(2, [1, ...name("js"), ...name("memory"), 2, 0, 1]); // one page, no maximum
const active = (offset, bytes) => [0, 0x41, ...sleb(offset), 0x0b, ...uleb(bytes.length), ...bytes];
const data = section(11, [3, ...active(16, [1, 2, 3]), ...active(0x80000010 | 0, [9, 9]), 1, 1, 7]);
const original = Uint8Array.from([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0, ...imports, ...data]);
assert.ok(WebAssembly.validate(original));

const placed = placeDataOurselves(original.buffer);
assert.ok(WebAssembly.validate(placed.module), "the rewritten module is valid");
assert.deepEqual(placed.segments.map((s) => [s.address, [...s.data]]), [[16, [1, 2, 3]], [0x80000010, [9, 9]]],
  "each active segment's address read as unsigned; the passive one isn't moved");

// One page of memory: the original can't be placed there (0x80000010 is outside it), the rewritten one can, since
// the browser places nothing; the page then copies in what fits a small memory.
const memory = new WebAssembly.Memory({ initial: 1 });
await assert.rejects(WebAssembly.instantiate(original, { js: { memory } }));
await WebAssembly.instantiate(placed.module, { js: { memory } });
const low = placeDataOurselves(Uint8Array.from([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0, ...imports,
  ...section(11, [1, ...active(16, [1, 2, 3])])]).buffer);
low.place(memory);
assert.deepEqual([...new Uint8Array(memory.buffer, 15, 5)], [0, 1, 2, 3, 0]);

// A module without data passes through unchanged.
const bare = Uint8Array.from([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0, ...imports]);
assert.deepEqual([...placeDataOurselves(bare.buffer).module], [...bare]);
console.log("web runtime: data segments placed by the page");

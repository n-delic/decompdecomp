// The page plays a build's SIMD modules only where they can run (web/runtime.js, moduleFor): when the build has
// them (an export made "faster where it can"), the browser validates WebAssembly SIMD, and ?nosimd isn't asked.
// Safari before 16.4 can't load a module that uses SIMD at all, so it must get the plain one.
import assert from "node:assert/strict";
import { moduleFor, simdSupported } from "../src/decompnet/port/wasm/web/runtime.js";

assert.equal(simdSupported(), true, "node's V8 has SIMD");
assert.equal(moduleFor("host-web.wasm", { simd: true }, ""), "host-web-simd.wasm");
assert.equal(moduleFor("app-asyncify.wasm", { simd: true }, "?asyncify"), "app-asyncify-simd.wasm");
assert.equal(moduleFor("host-web.wasm", { simd: false }, ""), "host-web.wasm", "a build without them");
assert.equal(moduleFor("host-web.wasm", {}, ""), "host-web.wasm", "a build from before the option");
assert.equal(moduleFor("host-web.wasm", { simd: true }, "?nosimd"), "host-web.wasm", "?nosimd");
const validate = WebAssembly.validate;
WebAssembly.validate = () => false; // a browser without SIMD
try {
  assert.equal(simdSupported(), false);
  assert.equal(moduleFor("host-web.wasm", { simd: true }, ""), "host-web.wasm", "a browser without SIMD");
} finally { WebAssembly.validate = validate; }

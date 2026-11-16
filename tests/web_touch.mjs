/* Real controller logic with a small pointer-event surface: no ROM, browser or game tools needed. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

function element(dataset = {}) {
  return { dataset, style: {}, listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setPointerCapture(id) { this.captured = id; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 120, height: 120 }; },
    fire(type, id, x = 60, y = 60) {
      this.listeners[type]({ pointerId: id, clientX: x, clientY: y, preventDefault() {} });
    } };
}
const a = element({ button: "32768" }), b = element({ button: "16384" }), stick = element(), dot = element();
stick.querySelector = () => dot;
const root = { querySelector: () => stick, querySelectorAll: () => [a, b] }, events = {}, document = element();
const context = vm.createContext({ document, Map, Math, Number,
  addEventListener(type, fn) { events[type] = fn; } });
const source = fs.readFileSync(new URL("../src/decompnet/consoles/n64/port_wasm/web/touch.js", import.meta.url), "utf8");
vm.runInContext(source.replace("export function", "function"), context);
const controller = context.touchController(root);
stick.fire("pointerdown", 1, 100, 20);
a.fire("pointerdown", 2); b.fire("pointerdown", 3);
assert.equal(controller.read().buttons, 0xc000);
assert.equal(controller.read().x, 57); assert.equal(controller.read().y, 57);
assert.equal(stick.captured, 1);
// A second finger on the stick cannot steal it.
stick.fire("pointerdown", 9, 0, 0); stick.fire("pointerup", 9);
assert.equal(controller.read().x, 57);
a.fire("pointerup", 2);
assert.equal(controller.read().buttons, 0x4000); assert.equal(controller.read().x, 57);
b.fire("pointercancel", 3); stick.fire("lostpointercapture", 1);
assert.equal(controller.read().buttons | controller.read().x | controller.read().y, 0);
// Two fingers on the same button: releasing one keeps the other held.
a.fire("pointerdown", 4); a.fire("pointerdown", 5); a.fire("lostpointercapture", 4);
assert.equal(controller.read().buttons, 0x8000);
events.blur(); assert.equal(controller.read().buttons, 0);
stick.fire("pointerdown", 6, 100, 60); a.fire("pointerdown", 7);
document.hidden = true; document.listeners.visibilitychange();
assert.equal(controller.read().buttons | controller.read().x | controller.read().y, 0);
stick.fire("pointerdown", 8, 60, 20); events.resize(); assert.equal(controller.read().y, 0);
console.log("Touch input: simultaneous buttons, analog diagonals, cancellation and background reset passed");

// The browser build's threads give way when the next retrace is due (web/runtime.js, retraceDeadline), so the
// retraces keep their 50 a second: with a fixed 15 ms per retrace, a game whose low-priority thread never waits held
// the page 22 ms of every 20, and a PAL racing game drew 22 frames a second where it draws 25.
import assert from "node:assert/strict";
import { retraceDeadline } from "../src/decompnet/port/wasm/web/runtime.js";

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} is ${b}`);

const now = performance.now();
// one retrace due: its threads run until the next is (`next`)
near(retraceDeadline(now + 20, 1, 0), now + 20);
// three due (the page was late): the first two are late already, and still let the woken thread run 2 ms
const late = retraceDeadline(now + 5, 3, 0);
assert.ok(late >= now + 2 && late < now + 50, `a late retrace's threads get a little time (${late - now} ms)`);
near(retraceDeadline(now + 45, 3, 2), now + 45);
near(retraceDeadline(now + 45, 3, 1), now + 25);

/* The browser build's runtime (port/wasm/web.py): loads the game's module and its host's, which share one 4 GB
   memory, and runs the game's threads as coroutines with JavaScript Promise Integration. The console's own page
   (its kit's web/play.js) gives it the ROM and what to do with frames, sound and the controller.

   Threads (web/co.c): a game thread waiting for the CPU suspends its wasm stack; between calls from here, the host
   says which one runs next (co_next) and this resumes it until it waits again. Each has its own C stack in the
   host's part of memory, swapped into the host's __stack_pointer here (co_sp, co_set_sp). The page's own calls
   (the boot, each retrace) run outside any thread and never wait. */

export function supported() {
  return typeof WebAssembly.Suspending === "function" && typeof WebAssembly.promising === "function";
}

/* Every data segment here lives above 2 GB (the game's RAM from 0x80000000, the host's data from 0xB0000000), and
   Safari places a segment at such an address as if it were negative (its offset sign-extended to 64 bits), so it
   refuses the module: "segment writes outside of memory". So each active segment whose offset is a constant becomes
   passive (the browser places nothing, and nothing else refers to it) and the page copies it into memory itself, at
   its address read as unsigned, once the module is instantiated. Other segments are left as they are. */
function readLeb(b, at, signed = false) {
  let result = 0n, shift = 0n, byte;
  do { byte = b[at++]; result |= BigInt(byte & 0x7f) << shift; shift += 7n; } while (byte & 0x80);
  if (signed && (byte & 0x40)) result -= 1n << shift;
  return [result, at];
}
function leb(n) {
  const out = [];
  do { let byte = n & 0x7f; n >>>= 7; if (n) byte |= 0x80; out.push(byte); } while (n);
  return Uint8Array.from(out);
}
export function placeDataOurselves(code) {
  const b = new Uint8Array(code), parts = [b.subarray(0, 8)], segments = [];
  let at = 8;
  while (at < b.length) {
    const id = b[at];
    const [size, body] = readLeb(b, at + 1);
    const end = body + Number(size);
    if (id !== 11) { parts.push(b.subarray(at, end)); at = end; continue; } /* only the data section changes */
    let [count, p] = readLeb(b, body);
    const out = [leb(Number(count))];
    for (let i = 0; i < Number(count); i++) {
      const start = p;
      let flags;
      [flags, p] = readLeb(b, p);
      let q = p;
      if (flags === 2n) [, q] = readLeb(b, q); /* an explicit memory index */
      if ((flags === 0n || flags === 2n) && b[q] === 0x41) { /* i32.const <offset> end */
        let offset;
        [offset, q] = readLeb(b, q + 1, true);
        if (b[q] === 0x0b) {
          let length;
          [length, q] = readLeb(b, q + 1);
          const data = b.subarray(q, q + Number(length));
          segments.push({ address: Number(BigInt.asUintN(32, offset)), data });
          out.push(Uint8Array.of(1), leb(data.length), data);
          p = q + Number(length);
          continue;
        }
      }
      if (flags === 0n || flags === 2n) { while (b[q] !== 0x0b) q++; q++; } /* another offset expression */
      let length;
      [length, q] = readLeb(b, q);
      p = q + Number(length);
      out.push(b.subarray(start, p));
    }
    parts.push(Uint8Array.of(11), leb(out.reduce((n, x) => n + x.length, 0)), ...out);
    at = end;
  }
  const module = new Uint8Array(parts.reduce((n, x) => n + x.length, 0));
  let o = 0;
  for (const x of parts) { module.set(x, o); o += x.length; }
  return {
    module,
    segments,
    place(memory) {
      const bytes = new Uint8Array(memory.buffer);
      for (const s of segments) bytes.set(s.data, s.address);
    },
  };
}

/* The few WASI calls the host's C library makes: its stdout and stderr to the console, the clock, its environment
   (the PORT_* options, from the page). No files: the ROM and the save come through the page. */
function wasi(memory, environment, log) {
  const view = () => new DataView(memory.buffer);
  const bytes = () => new Uint8Array(memory.buffer);
  const env = Object.entries(environment).map(([k, v]) => new TextEncoder().encode(`${k}=${v}\0`));
  const lines = { 1: "", 2: "" };
  const EBADF = 8, ENOENT = 44, ESPIPE = 70;
  return {
    environ_sizes_get(count, size) {
      view().setUint32(count >>> 0, env.length, true);
      view().setUint32(size >>> 0, env.reduce((n, e) => n + e.length, 0), true);
      return 0;
    },
    environ_get(ptrs, buf) {
      let p = ptrs >>> 0, b = buf >>> 0;
      for (const e of env) { view().setUint32(p, b, true); bytes().set(e, b); p += 4; b += e.length; }
      return 0;
    },
    clock_time_get(id, precision, out) {
      const ns = id === 0 ? BigInt(Date.now()) * 1000000n : BigInt(Math.round(performance.now() * 1e6));
      view().setBigUint64(out >>> 0, ns, true);
      return 0;
    },
    fd_write(fd, iovs, count, written) {
      let n = 0, text = "";
      for (let i = 0; i < count; i++) {
        const at = (iovs >>> 0) + i * 8, ptr = view().getUint32(at, true), len = view().getUint32(at + 4, true);
        text += new TextDecoder().decode(bytes().slice(ptr, ptr + len));
        n += len;
      }
      view().setUint32(written >>> 0, n, true);
      if (fd === 1 || fd === 2) {
        const parts = (lines[fd] + text).split("\n");
        lines[fd] = parts.pop();
        for (const line of parts) log(line, fd);
      }
      return 0;
    },
    fd_fdstat_get(fd, out) {
      if (fd > 2) return EBADF;
      const v = view(), p = out >>> 0;
      v.setUint8(p, 2); v.setUint16(p + 2, 0, true); v.setBigUint64(p + 8, 0xffffffffn, true); v.setBigUint64(p + 16, 0xffffffffn, true);
      return 0;
    },
    fd_fdstat_set_flags: () => 0,
    fd_close: () => 0,
    fd_prestat_get: () => EBADF,
    fd_prestat_dir_name: () => EBADF,
    fd_read: () => EBADF,
    fd_seek: () => ESPIPE,
    path_open: () => ENOENT,
    proc_exit(code) { throw new Error(`the game's host exited (${code})`); },
  };
}

/* Start the game from `base` (the folder of the build's files) and `rom` (the player's ROM, bytes). `io`:
   {frame(rgb, w, h), audio(lr, frames, hz), input() -> buttons<<16 | x<<8 | y, eepromLoad() -> bytes,
   eepromSave(bytes), log(line, stream), error(error)}; `env`: the host's PORT_* options. Resolves once it runs. */
export async function start({ base = "", rom, io, env = {} }) {
  if (!supported()) {
    throw new Error("This browser can't run the game: it needs WebAssembly JavaScript Promise Integration "
      + "(Chrome or Edge 137 or later, or Safari 27).");
  }
  const memory = new WebAssembly.Memory({ initial: 65536, maximum: 65536 });
  const bytes = () => new Uint8Array(memory.buffer);
  const [build, hostCode, gameCode] = await Promise.all([
    fetch(base + "build.json").then((r) => r.json()),
    fetch(base + "host-web.wasm").then((r) => r.arrayBuffer()),
    fetch(base + "game-web.wasm").then((r) => r.arrayBuffer()),
  ]);

  let host = null, current = -1, mainSp = 0, yielded = null;
  const threads = [];
  function leave() {
    host.co_leave();
    current = -1;
    host.co_set_sp(mainSp);
    const done = yielded;
    yielded = null;
    if (done) done();
  }
  const suspend = new WebAssembly.Suspending(() => new Promise((resume) => {
    const t = threads[current];
    t.resume = resume;
    t.sp = host.co_sp();
    leave();
  }));
  const portWeb = {
    suspend,
    memory_base: () => 0,
    trace: () => io.log(new Error("the game's host asked for a backtrace").stack, 2),
    eeprom_load: (ptr, size) => {
      const saved = io.eepromLoad();
      if (saved) bytes().set(saved.subarray(0, size), ptr >>> 0);
    },
    eeprom_save: (ptr, size) => io.eepromSave(bytes().slice(ptr >>> 0, (ptr >>> 0) + size)),
    frame: (ptr, w, h) => io.frame(bytes().subarray(ptr >>> 0, (ptr >>> 0) + w * h * 3), w, h),
    audio: (ptr, frames, hz) => io.audio(new Int16Array(memory.buffer, ptr >>> 0, frames * 2), frames, hz),
    input: () => io.input(),
  };
  const hostCodePlaced = placeDataOurselves(hostCode), gameCodePlaced = placeDataOurselves(gameCode);
  const hostModule = await WebAssembly.instantiate(hostCodePlaced.module, {
    env: { memory }, wasi_snapshot_preview1: wasi(memory, env, io.log), port_web: portWeb,
  });
  host = hostModule.instance.exports;
  hostCodePlaced.place(memory);
  host._initialize();
  const gameImports = { memory };
  for (const [name, value] of Object.entries(host)) if (typeof value === "function") gameImports[name] = value;
  const gameModule = await WebAssembly.instantiate(gameCodePlaced.module, { env: gameImports });
  const game = gameModule.instance.exports;
  gameCodePlaced.place(memory);
  game.__wasm_call_ctors(); // each file registers its functions, for calls through pointers (web/be.c)
  build.slots.forEach((name, i) => {
    if (!game[name]) throw new Error(`the game's module has no ${name}, which the host calls`);
    host.__indirect_function_table.set(host.port_web_slot(i), game[name]);
  });
  const threadMain = WebAssembly.promising(host.co_main);

  /* Run thread `id` until it waits again, or ends. */
  function step(id) {
    return new Promise((done) => {
      yielded = done;
      current = id;
      mainSp = host.co_sp();
      const t = threads[id] || (threads[id] = {});
      host.co_enter(id);
      if (!host.co_started(id)) {
        host.co_set_sp(host.co_stack_top(id));
        threadMain(id).then(() => leave(), (error) => { io.error(error); leave(); });
      } else {
        host.co_set_sp(t.sp);
        const resume = t.resume;
        t.resume = null;
        resume();
      }
    });
  }
  /* Every thread that can run, in the order the host says, until none can: what the CPU does between retraces. */
  async function runThreads() {
    const until = performance.now() + 15; /* then the retrace's turn, if one is due (a thread that gave way) */
    while (performance.now() < until) {
      const id = host.co_next();
      if (id < 0) return;
      await step(id);
    }
  }
  let queue = Promise.resolve();
  const then = (work) => (queue = queue.then(work).catch((error) => io.error(error)));

  const at = host.port_web_alloc(rom.length) >>> 0;
  if (!at) throw new Error("There's no memory left for the ROM.");
  bytes().set(rom, at);
  host.port_web_boot(at, rom.length);
  await then(runThreads);
  /* the VI retrace, PAL (50 a second), as the native host's main loop gives it */
  let next = performance.now(), stopped = false;
  function tick() {
    if (stopped) return;
    const now = performance.now();
    let due = 0;
    while (next <= now && due < 3) { next += 20; due++; }
    if (now - next > 200) next = now + 20; /* a background tab: don't catch up for ever */
    for (let i = 0; i < due; i++) then(() => { host.port_web_retrace(); return runThreads(); });
    setTimeout(tick, Math.max(1, next - performance.now()));
  }
  tick();
  return { stop() { stopped = true; }, memory, host, game };
}

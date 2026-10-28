// The GUI's scripts, run in node against a stand-in for the page, to test what tests/test_gui.py asks: the live
// polls of two projects, and the notification centre that tells their news. Every element is a stub that takes any
// property and any call; fetch answers from the scenario below, and an answer can be held back to play a slow poll.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function stub() {
  const props = {};
  const fn = function () { return stub(); };
  return new Proxy(fn, {
    get(_, key) {
      if (key in props) return props[key];
      if (key === Symbol.toPrimitive) return () => "";
      if (key === Symbol.iterator) return function* () {};
      if (key === "then") return undefined;  // not a promise
      if (["clientWidth", "width", "height", "length", "scrollHeight"].includes(key)) return 0;
      if (key === "hidden") return false;
      return (props[key] = stub());
    },
    set(_, key, value) { props[key] = value; return true; },
    apply() { return stub(); },
  });
}

const held = [];  // fetches held back: [url, release]
const answers = {};  // url prefix -> answer
let hold = null;  // url prefix whose next fetch is held back
function fetch(url) {
  const prefix = Object.keys(answers).sort((a, b) => b.length - a.length).find((p) => url.startsWith(p));
  const body = prefix ? answers[prefix](url) : {};
  const res = { ok: true, json: async () => body };
  if (hold && url.startsWith(hold)) { hold = null; return new Promise((resolve) => held.push([url, () => resolve(res)])); }
  return Promise.resolve(res);
}

const toasts = [], errors = [];
process.on("unhandledRejection", (e) => errors.push(String(e && e.stack || e)));
const ctx = {
  console, fetch, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number, Symbol, Error, RegExp, Uint8Array, URLSearchParams,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: () => 0, performance: { now: () => 0 }, localStorage: stub(), navigator: stub(), URLSearchParams,
  matchMedia: () => stub(), getComputedStyle: () => stub(), location: { hash: "", href: "" }, confirm: () => false,
  addEventListener: () => {}, scrollTo: () => {}, devicePixelRatio: 1, document: stub(),
};
ctx.window = ctx;
const now = Date.now() / 1000;
const overview = (slug) => ({
  slug, title: slug, kind: "rom", platform: "ps2", platform_name: "PlayStation 2", ready: true, rom: { game_code: "X" },
  compiler: { name: "EE-GCC", flags: [], installed: true, supported: true }, functions: 1, matched: 0, bytes: 1,
  matched_bytes: 0, attempts: 0, priority: [], suggestions: [], boundaries: [], compiler_ranges: [],
});
const live = (slug, events) => ({ overview: overview(slug), events, attempts: [], claims: [], sessions: [] });
// the other game's agents matched 60 functions before it was opened: none of them is news
const old = Array.from({ length: 60 }, (_, i) => ({ id: i + 1, kind: "match", detail: `func_${i}`, agent: "claude", created_at: now - 60 }));
let liveB = [];
answers["/api/state"] = () => ({ version: "test", settings: {}, agents: [], toolchains: [] });
answers["/api/projects"] = () => [];
answers["/api/agents/detected"] = () => [];
answers["/api/mapcolumns"] = () => ({ keys: ["vram", "cc", "ccx"], rows: [] });
answers["/api/knowledge"] = () => ({ auto: false, items: [] });
answers["/api/stats"] = () => ({ agents: [], matches_per_hour: new Array(24).fill(0), compiles_last_hour: 0 });
answers["/api/project?slug="] = (url) => overview(url.split("=")[1]);
answers["/api/map?slug="] = () => ({ sig: "s", keys: ["name", "vram"], rows: [] });
answers["/api/live?slug=a"] = () => live("a", []);
answers["/api/live?slug=b"] = (url) => live("b", liveB.filter((e) => e.id > Number(new URL("http://x" + url).searchParams.get("e"))));
// the app-wide feed (/api/events): every project's notable events, with the project's name
let feed = old.map((e) => ({ ...e, slug: "b", project: "Ridge Racer V" }));
answers["/api/events"] = (url) => {
  const after = new URL("http://x" + url).searchParams.get("after");
  const last = Math.max(0, ...feed.map((e) => e.id));
  return after == null ? { last, events: [] } : { last, events: feed.filter((e) => e.id > Number(after)) };
};

vm.createContext(ctx);
// GUI_JS: another copy of the scripts (an older one, to see the test fail there)
const js = process.env.GUI_JS || path.join(__dirname, "..", "src", "decompnet", "app", "static", "js");
for (const name of ["base", "sidebar", "home", "project", "map", "agents", "function", "steering", "pages"]) {
  vm.runInContext(fs.readFileSync(path.join(js, `${name}.js`), "utf8"), ctx, { filename: `${name}.js` });
}
ctx.toast = (html) => toasts.push(html);
const tick = () => new Promise((r) => setImmediate(r));
const SETTLE_TICKS = 50;  // turns of the event loop: enough for a chain of awaits to finish
const settle = async () => { for (let i = 0; i < SETTLE_TICKS; i++) await tick(); };

(async () => {
  await ctx.openProject("a");
  await settle();
  hold = "/api/live?slug=a";  // game A's next poll is slow (its agents keep the app busy)
  ctx.poll();
  await settle();
  liveB = old;
  const opening = ctx.openProject("b");
  await settle();
  await opening;
  for (const [, release] of held) release();  // A's slow poll comes back, after B opened
  await settle();
  liveB = old.concat([{ id: 61, kind: "match", detail: "func_new", agent: "gemini", created_at: Date.now() / 1000 + 1 }]);
  await ctx.poll();  // B's next poll: news is the notification centre's, never a project's own toast
  await settle();
  // the notification centre began where the feed was when the window opened (pages.js): 60 old matches aren't news.
  // Then B's agent matches 13 functions and A's helper pauses: two lines, a burst counted, not 14 toasts
  const t = Date.now() / 1000;
  const news = Array.from({ length: 13 }, (_, i) => ({ id: 61 + i, kind: "match", detail: `func_${100 + i}`, agent: "gemini-3.8-flash-2",
    created_at: t + i, slug: "b", project: "Ridge Racer V" }));
  news.push({ id: 74, kind: "paused", detail: "the provider is out of credit", agent: "claude-1", created_at: t + 13, slug: "a", project: "Demo Quest 64" });
  feed = feed.concat(news);
  await ctx.pollEvents();
  await settle();
  // one more match in the same burst: the same line counts up
  feed.push({ id: 75, kind: "match", detail: "func_200", agent: "claude-2", created_at: t + 14, slug: "b", project: "Ridge Racer V" });
  await ctx.pollEvents();
  await settle();
  const notes = vm.runInContext("N.live.map((x) => x.el.innerHTML.replace(/<[^>]+>/g, '').replace(/\\s+/g, ' ').trim())", ctx);
  const unread = vm.runInContext("N.unread", ctx);
  // the page's small pure helpers, for tests of their own
  const checks = vm.runInContext(`({
    repo: ["", "hello", "git@github.com:zeldaret/oot.git", "https://example.com/owner/repo", "https://github.com/zeldaret/oot",
           "https://gitlab.com/owner/repo.git/"].map(repoProblem),
    invite: ["hello", "https://abc.trycloudflare.com/join/CODE1#room=game", "http://192.168.1.5:6481"].map(inviteProblem),
    join: [{ link: "https://abc.trycloudflare.com/join/CODE1", name: "alex", copy: "", own: true },
           { link: "https://abc.trycloudflare.com/join/CODE1", name: "a b", copy: "/x.iso", own: true },
           { link: "https://abc.trycloudflare.com/join/CODE1", name: "alex", copy: "/x.iso", own: true }].map(joinProblem),
    themes: ["", "light", "dark"].map(nextTheme),
    computers: (() => {
      const before = S.ov, members = [{ name: "alex" }, { name: "kim" }];
      S.ov = { room: { role: "guest", member: "kim", members } };
      const guest = ["@claude-1", "@alex.claude-2", "claude-3", "autopilot", "@deepseek-v4.1-2"].map((a) => computerColour(computerOf(a)));
      S.ov = { room: { role: "host", members } };
      const host = ["claude-1", "alex.claude-2", "kim.claude-3", "opencode-deepseek-v4.1-flash-1"].map((a) => computerColour(computerOf(a)));
      S.ov = { room: null };
      const alone = computerColour(computerOf("claude-1"));
      S.ov = before;
      return { guest, host, alone };
    })(),
    dropped: (() => {
      const head = (at, text) => { const h = new Uint8Array(0x10000); [...text].forEach((c, i) => (h[at + i] = c.charCodeAt(0))); return h; };
      const file = { name: "SLUS_000.01", size: 0x10000 };
      return [identifyHead(head(0, "PS-X EXE"), file).platform, identifyHead(head(0x9319, "CD001"), file).platform,
              platformName("playstation"), notSupported(head(0, "PS-X EXE"), file)];
    })(),
    // Windows programs the PC console can't start: a 64-bit one, one for another processor, a DOS one
    windows: [0x8664, 0xaa64, 0x14c, null].map((machine) => {
      const h = new Uint8Array(0x200), dv = new DataView(h.buffer);
      h.set([0x4d, 0x5a]);
      if (machine !== null) { dv.setUint32(0x3c, 0x80, true); h.set([0x50, 0x45, 0, 0], 0x80); dv.setUint16(0x84, machine, true); }
      if (machine === 0x14c) dv.setUint16(0x98, 0x10b, true);
      const file = { name: "game.exe", size: 0x200 };
      return [identifyHead(h, file)?.platform ?? null, notSupported(h, file)];
    }),
    // the Visual C++ pack a dropped program asks for, from its Rich header or linker version: [head as hex, pack, what
    // the drop says]. tests/test_gui.py asks scratch.describe about the same bytes
    pcPack: [[[0xab, 40219, 503], [0xaa, 40219, 171], [0x30, 9044, 121], [0x9d, 40219, 1]],
             [[0x0a, 8047, 169], [0x0b, 8966, 148], [0x0b, 8447, 38], [0x13, 8034, 23]],
             [[0x5d, 3077, 40], [0x5f, 3077, 300], [0x78, 50727, 1]],  // 2003's code, linked by 2005's linker
             [[0x6d, 50727, 30], [0x0b, 8168, 10]],  // no linker entry: the version that built the most files
             [], [[0x01, 0, 5]]].map((rich, k) => {
      const key = [0x5a, 0x17, 0x3c, 0x91], bytes = [0x4d, 0x5a, ...new Array(0x3e + 0x40).fill(0)];
      if (rich.length) {
        const plain = [..."DanS"].map((c) => c.charCodeAt(0)).concat(new Array(12).fill(0));
        for (const [product, build, count] of rich) {
          const w = [((product << 16) | build) >>> 0, count];
          for (const v of w) plain.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, v >>> 24);
        }
        bytes.push(...plain.map((b, i) => b ^ key[i % 4]), 0x52, 0x69, 0x63, 0x68, ...key);
      }
      while (bytes.length % 8) bytes.push(0);
      const pe = bytes.length;
      bytes.push(0x50, 0x45, 0, 0, 0x4c, 0x01, 3, 0, ...new Array(12).fill(0), 0xe0, 0, 0x02, 0x01);
      bytes.push(0x0b, 0x01, k === 4 ? 9 : 6, 0, ...new Array(0xe0 - 4).fill(0));
      const h = new Uint8Array(0x400);
      h.set(bytes);
      h[0x3c] = pe;
      const info = identifyHead(h, { name: "game.exe", size: 0x400 });
      return [[...h.slice(0, bytes.length)].map((b) => b.toString(16).padStart(2, "0")).join(""), info.pack, packOf(info)];
    }),
    // a PSP's UMD image and programs are told apart from the PS2's; what can't be read yet says what to do instead
    psp: (() => {
      const head = (writes) => {
        const h = new Uint8Array(0x9400), dv = new DataView(h.buffer);
        writes.forEach(([at, v]) => (typeof v === "string" ? [...v].forEach((c, i) => (h[at + i] = c.charCodeAt(0))) : dv.setUint32(at, v, true)));
        return h;
      };
      const iso = { name: "game.iso", size: 0x9400 }, elf = (type, flags) => head([[0, "\x7fELF"], [5, "\x01"], [16, type], [18, "\x08"], [36, flags]]);
      const umd = head([[0x8001, "CD001"], [0x8008, "PSP GAME                        "]]);
      const ps2 = head([[0x8001, "CD001"], [0x8008, "PLAYSTATION                     "]]);
      const cso = head([[0, "CISO"], [4, 0x18], [8, 0x4000000], [16, 0x800]]);
      const zso = head([[0, "ZISO"], [4, 0x18], [8, 0x4000000], [16, 0x800]]);
      const gcCiso = head([[0, "CISO"], [4, 0x200000], [8, 0x00010101]]);
      const pbp = head([[0, "\0PBP"]]);
      const platform = (h, f = iso) => identifyHead(h, f)?.platform ?? null;
      return {
        platforms: [platform(umd), platform(ps2), platform(gcCiso), platform(cso, { name: "game.cso", size: 0x9400 }),
                    platform(elf(0xffa0, 0x10a23001)), platform(elf(2, 0x10a23001)), platform(elf(2, 0x20924001))],
        said: [notSupported(zso, { name: "game.bin", size: 0x9400 }), notSupported(pbp, { name: "EBOOT.PBP", size: 0x9400 })],
        cso: identifyHead(cso, { name: "game.cso", size: 0x9400 })?.order ?? null,
        name: platformName("playstation"),
      };
    })(),
    // the import offers every compiler of the game's console this computer lacks (every PlayStation's for a disc not read yet)
    missing: (() => {
      const before = S.st;
      S.st = { consoles: [{ id: "ps", aliases: [], installable: ["gcc-2.7.2-psx", "gcc-2.6.3-psx"] },
                          { id: "ps2", aliases: [], installable: ["ee-gcc-2.96", "mwcps2-3.0.1"] },
                          { id: "nds", aliases: [], installable: [] }],
               toolchains: [{ id: "gcc-2.7.2-psx", installed: true }, { id: "gcc-2.6.3-psx", installed: false },
                            { id: "ee-gcc-2.96", installed: false }, { id: "mwcps2-3.0.1", installed: false, license: "proprietary" }] };
      const out = [missingCompilers("ps2").map((t) => t.id), missingCompilers("playstation").length, missingCompilers("nds").length];
      S.st = before;
      return out;
    })(),
    // tests/test_ds.py's header, whose checksum (0xB4DE) the server's crc16 computed: the page agrees on what's a DS ROM
    nds: (() => {
      const h = new Uint8Array(0x200), dv = new DataView(h.buffer);
      [..."DEMOQUESTDS\0ADQE"].forEach((c, i) => { h[i] = c.charCodeAt(0); });
      [0x4000, 0x02000800, 0x02000000, 0x1000].forEach((v, i) => dv.setUint32(0x20 + 4 * i, v, true));
      dv.setUint16(0x15e, 0xb4de, true);
      const file = { name: "game.nds", size: 0x4200 };
      const good = identifyHead(h, file);
      h[0] ^= 1;
      return { good: good && [good.platform, good.code, good.title], broken: identifyHead(h, file), said: notSupported(h, file) };
    })(),
    // the launcher keeps the last effort picked: Claude Code's xhigh isn't one opencode offers
    efforts: (() => {
      LZ.effort = "xhigh";
      const claude = { takes_effort: true, efforts: ["low", "medium", "high", "xhigh", "max"] };
      const opencode = { takes_effort: true, efforts: ["low", "medium", "high", "max"] };
      return [withEffort(claude, "opus"), withEffort(opencode, "deepseek/v4"), withEffort({ takes_effort: false }, "m")];
    })(),
    systemThemes: ["", "?system-theme=light", "?system-theme=dark", "?system-theme=invalid"].map((search) => {
      location.search = search;
      return systemTheme();
    }),
    search: (() => {
      const fns = [{ name: "func_80000400", vram: 0x80000400, unit: "main" }, { name: "lerp", vram: 0x80000500, unit: "main" },
                   { name: "main_loop", vram: 0x80000600, unit: "main" }];
      return [searchFunctions(fns, "ma", false), searchFunctions(fns, "main", true), searchFunctions(fns, "mai", false).length,
              searchFunctions(fns, "zzz", false)].map((r) => (Array.isArray(r) ? r.map((f) => f.name) : r));
    })(),
    // Polish is offered once half the game's own code matches: set-aside functions count on neither side
    polish: [{ functions: 274, set_aside: 241, matched: 33 }, { functions: 274, set_aside: 0, matched: 33 },
             { functions: 5, set_aside: 5, matched: 0 }, null].map(polishOffered),
    // #118: a project from another tool names a compiler decompdecomp doesn't have: the banner says so, instead of
    // taking it for a compiler with no build for this computer
    unknownCompiler: (() => {
      const before = S.ov, original = document.getElementById;
      const notice = { hidden: null, className: null, innerHTML: "", _html: null, _working: false };
      document.getElementById = (id) => (id === "compilerNotice" ? notice : original(id));
      S.ov = { slug: "a", kind: "rom", matched: 0, compiler: { toolchain: "msvcxx-x86_64", name: "msvcxx-x86_64",
        flags: ["/O2"], installed: false, supported: false, known: false, missing: [] } };
      renderCompilerNotice(S.ov);
      const said = { hidden: notice.hidden, className: notice.className, text: String(notice.innerHTML).replace(/<[^>]+>/g, "") };
      document.getElementById = original;
      S.ov = before;
      return said;
    })(),
    autopilot: burstLines([
      { id: 1, slug: "a", project: "Demo", kind: "match", agent: "autopilot", detail: "func_1", created_at: 100 },
      { id: 2, slug: "a", project: "Demo", kind: "match", agent: "autopilot", detail: "func_2", created_at: 101 },
      { id: 3, slug: "a", project: "Demo", kind: "autopilot", agent: "", detail: "2 of 95 functions matched from machine drafts", created_at: 102 },
      { id: 4, slug: "a", project: "Demo", kind: "match", agent: "claude-1", detail: "func_3", created_at: 103 },
      { id: 5, slug: "a", project: "Demo", kind: "autopilot", agent: "", detail: "0 of 1 functions matched from machine drafts", created_at: 104 },
    ]).map((g) => noteHtml(g).replace(/<[^>]+>/g, "")),
    // #99: the app's own workers' claims outlive a stopped helper (the permuter's by half an hour) and one helper
    // going between waiting and working mustn't swap the rows
    agents: (() => {
      const was = { sessions: S.sessions, claims: S.claims };
      const claim = (agent) => ({ name: "func_1", agent });
      S.sessions = [];
      S.claims = ["permuter", "@permuter", "decompdecomp", "@autopilot"].map(claim);
      const ownOnly = workingAgents().n;
      S.claims = [claim("alex.claude-1")];
      const outside = workingAgents().n;
      S.claims = [claim(null), claim(undefined)];
      const nameless = workingAgents().n;
      const at = (label, state, task = "decompile") => ({ id: label, label, state, task });
      const flip = (first, second) => [at("alpha", first), at("beta", second)].sort(sessionOrder).map((s) => s.label);
      const flipped = flip("waiting", "working"), again = flip("working", "waiting");
      const rows = [at("stopped-one", "stopped"), at("working-one", "working"), at("polishing", "waiting", "readable"),
                    at("paused-one", "paused", "readable")].sort(sessionOrder).map((s) => s.label);
      S.sessions = was.sessions; S.claims = was.claims;
      return { ownOnly, outside, nameless, flipped, again, rows };
    })(),
  })`, ctx);
  process.stdout.write(JSON.stringify({ toasts, notes, unread, errors, checks }));
})().catch((e) => { console.error(e); process.exit(1); });

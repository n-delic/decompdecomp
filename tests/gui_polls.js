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
  console, fetch, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number, Symbol, Error, RegExp, Uint8Array,
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
    autopilot: burstLines([
      { id: 1, slug: "a", project: "Demo", kind: "match", agent: "autopilot", detail: "func_1", created_at: 100 },
      { id: 2, slug: "a", project: "Demo", kind: "match", agent: "autopilot", detail: "func_2", created_at: 101 },
      { id: 3, slug: "a", project: "Demo", kind: "autopilot", agent: "", detail: "2 of 95 functions matched from machine drafts", created_at: 102 },
      { id: 4, slug: "a", project: "Demo", kind: "match", agent: "claude-1", detail: "func_3", created_at: 103 },
      { id: 5, slug: "a", project: "Demo", kind: "autopilot", agent: "", detail: "0 of 1 functions matched from machine drafts", created_at: 104 },
    ]).map((g) => noteHtml(g).replace(/<[^>]+>/g, "")),
  })`, ctx);
  process.stdout.write(JSON.stringify({ toasts, notes, unread, errors, checks }));
})().catch((e) => { console.error(e); process.exit(1); });

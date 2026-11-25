/* The browser build's settings menu: the same rows as the window build's (window/settings.h: tests/test_n64_port_web_settings.py
   checks that each one agrees), the same keys (Tab or F1 opens it, the arrows move and change, Enter acts, Escape
   closes; a gamepad's Back button, its d-pad, A and B), kept in the browser. The model (rows, choices, navigation) has no
   page in it, so it runs under node; mountMenu draws it over the game. */

/* `keep: false`: not remembered (a browser only goes full screen on a click, so it can't start that way). */
export const ROWS = [
  { section: "Video", key: "fullscreen", label: "Full screen", values: ["OFF", "ON"], fallback: 0, keep: false },
  { section: "Video", key: "smooth", label: "Scaling", values: ["SHARP", "SMOOTH"], fallback: 0 },
  { section: "Video", key: "fps", label: "Show FPS", values: ["OFF", "ON"], fallback: 1 },
  { section: "Audio", key: "volume", label: "Volume", values: ["0", "10", "20", "30", "40", "50", "60", "70", "80", "90", "100"],
    fallback: 10 },
];
export const NAV = { UP: 0, DOWN: 1, LEFT: 2, RIGHT: 3, ACCEPT: 4, BACK: 5 };

/* The menu's state. `saved` is {key: "VALUE"} as kept before; a value that isn't one of a row's choices is ignored, so
   an edited or newer record can't break the page. */
export function menuModel(saved = {}) {
  const value = {};
  for (const r of ROWS) {
    const i = r.values.indexOf(saved[r.key]);
    value[r.key] = i >= 0 && r.keep !== false ? i : r.fallback;
  }
  const items = [...ROWS, { action: "resume", label: "Resume" }];
  const model = {
    open: false, row: 0, value, items,
    get(key) { const r = ROWS.find((x) => x.key === key); return r.values[value[key]]; },
    /* Set a choice from outside (the page's own button, the browser leaving full screen). */
    set(key, text) {
      const r = ROWS.find((x) => x.key === key), i = r.values.indexOf(text);
      if (i < 0 || value[key] === i) return false;
      value[key] = i;
      return true;
    },
    /* The record to keep. */
    kept() { return Object.fromEntries(ROWS.filter((r) => r.keep !== false).map((r) => [r.key, model.get(r.key)])); },
    /* One key or button. Returns {changed: key|null, action: "resume"|null}. */
    input(nav) {
      const item = items[model.row], out = { changed: null, action: null };
      if (nav === NAV.BACK) { model.open = false; out.action = "resume"; }
      else if (nav === NAV.UP) model.row = (model.row + items.length - 1) % items.length;
      else if (nav === NAV.DOWN) model.row = (model.row + 1) % items.length;
      else if (item.key) {
        const n = item.values.length, step = nav === NAV.LEFT ? n - 1 : 1;
        value[item.key] = (value[item.key] + step) % n;
        out.changed = item.key;
      } else if (nav === NAV.ACCEPT) { model.open = false; out.action = "resume"; }
      return out;
    },
  };
  return model;
}

const KEY_NAV = { ArrowUp: NAV.UP, ArrowDown: NAV.DOWN, ArrowLeft: NAV.LEFT, ArrowRight: NAV.RIGHT, Enter: NAV.ACCEPT,
  Space: NAV.ACCEPT, Escape: NAV.BACK };
/* a gamepad in the browser's standard layout: 0 is A, 1 is B, 8 is Back, 12 to 15 the d-pad */
const PAD_NAV = { 0: NAV.ACCEPT, 1: NAV.BACK, 12: NAV.UP, 13: NAV.DOWN, 14: NAV.LEFT, 15: NAV.RIGHT };

/* The menu over `host` (the element that is full screen, so the menu is visible in it too). `apply(key, text)` makes a
   choice take effect; `remember(record)` keeps them; `toggled(open)` tells the page to stop or start the game's input. */
export function mountMenu(host, model, { apply, remember, toggled }) {
  const panel = document.createElement("div");
  panel.id = "menu";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Settings");
  panel.hidden = true;
  host.appendChild(panel);
  const held = new Set();

  function draw() {
    panel.replaceChildren();
    const title = document.createElement("h2");
    title.textContent = "Settings";
    panel.appendChild(title);
    let section = "";
    model.items.forEach((item, i) => {
      if (item.section && item.section !== section) {
        section = item.section;
        const h = document.createElement("h3");
        h.textContent = section;
        panel.appendChild(h);
      }
      const row = document.createElement("button");
      row.type = "button";
      row.className = "menu-row" + (i === model.row ? " current" : "");
      const label = document.createElement("span");
      label.textContent = item.label;
      row.appendChild(label);
      if (item.key) {
        const value = document.createElement("span");
        value.textContent = `‹ ${model.get(item.key)} ›`;
        row.appendChild(value);
        row.setAttribute("aria-label", `${item.label}: ${model.get(item.key)}`);
      }
      row.onclick = () => { model.row = i; step(item.key ? NAV.RIGHT : NAV.ACCEPT); };
      panel.appendChild(row);
    });
  }
  function step(nav) {
    const was = model.open, out = model.input(nav);
    if (out.changed) {
      apply(out.changed, model.get(out.changed));
      remember(model.kept());
    }
    if (model.open !== was) { panel.hidden = !model.open; toggled(model.open); }
    draw();
  }
  function toggle() {
    model.open = !model.open;
    model.row = 0;
    panel.hidden = !model.open;
    toggled(model.open);
    draw();
    if (model.open) panel.querySelector(".current")?.focus({ preventScroll: true });
  }
  /* A choice changed outside the menu: shown, and remembered like any other. */
  function sync(key, text) {
    if (model.set(key, text)) { remember(model.kept()); if (model.open) draw(); }
  }
  addEventListener("keydown", (e) => {
    if (host.hidden || e.repeat && !model.open) return;
    if (e.code === "Tab" || e.code === "F1") { e.preventDefault(); e.stopPropagation(); if (!e.repeat) toggle(); return; }
    if (model.open && e.code in KEY_NAV) { e.preventDefault(); e.stopPropagation(); step(KEY_NAV[e.code]); }
  }, true);
  addEventListener("keyup", (e) => { if (model.open && e.code in KEY_NAV) { e.preventDefault(); e.stopPropagation(); } }, true);
  /* Called on every frame with the pads (the page's own input loop reads them anyway): Back opens it, then the pad
     moves through it, a press at a time. */
  function pads(list) {
    const now = new Set();
    for (const gp of list) {
      if (!gp) continue;
      gp.buttons.forEach((b, i) => { if (b.pressed || b.value > 0.5) now.add(i); });
    }
    for (const i of now) {
      if (held.has(i)) continue;
      if (i === 8) toggle();
      else if (model.open && i in PAD_NAV) step(PAD_NAV[i]);
    }
    held.clear();
    for (const i of now) held.add(i);
  }
  for (const r of ROWS) apply(r.key, model.get(r.key), true);
  draw();
  return { toggle, sync, pads };
}

"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ sidebar */
/* The page's CSP allows no inline style attributes: widths are set through the DOM instead. */
function setWidths(root) { root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; }); }

function markNav() {
  const h = location.hash || "#/";
  const route = h.startsWith("#/p/") ? "project" : (h.replace("#/", "") || "home");
  document.querySelectorAll(".nav [data-route]").forEach((a) => a.classList.toggle("on", a.dataset.route === route));
  document.querySelectorAll(".nav-proj").forEach((a) => a.classList.toggle("on", a.dataset.slug === S.slug && route === "project"));
}
async function loadNav() {
  if (document.hidden && S.projects.length) return;  // the window is minimised: nobody sees the list
  try { S.projects = await api("/api/projects"); } catch (e) {
    if (!S.projects?.length) $("navProjects").innerHTML = `<p class="nav-empty">Couldn't load your projects: ${esc(e.message)}</p>`;
    return;
  }
  renderNav();
}
function renderNav() {
  const box = $("navProjects");
  if (!S.projects.length) { box.innerHTML = `<p class="nav-empty">None yet: drop in a game.</p>`; return; }
  box.innerHTML = S.projects.map((p) => {
    const pct = p.bytes ? (100 * p.matched_bytes) / p.bytes : 0;
    const short = PLAT_SHORT[p.platform] || (p.platform_name || "").replace("Nintendo ", "") || "?";
    const state = p.working ? `<span class="dot on" title="${p.working} agent${p.working === 1 ? "" : "s"} working"></span>`
      : p.autopilot === "running" ? `<span class="dot work" title="autopilot running"></span>` : "";
    if (p.broken) return `<a class="nav-proj broken" href="#/p/${esc(p.slug)}" data-slug="${esc(p.slug)}" title="${esc(p.broken)}">
      <span class="np-top"><span class="np-name">${esc(p.title)}</span></span>
      <span class="np-meta"><span>can't be opened: hover for why</span></span></a>`;
    return `<a class="nav-proj" href="#/p/${esc(p.slug)}" data-slug="${esc(p.slug)}" title="${esc(p.title)}">
      <span class="np-top"><span class="np-name">${esc(p.title)}</span>${state}${p.room ? `<span class="np-room" title="${p.room === "host" ? "you share this project" : "in a team room"}">●●</span>` : ""}</span>
      <span class="np-meta"><span>${esc(short)}</span><span class="grow"></span><span>${p.kind === "fork" && !p.ready ? "set up" : pctNum(pct) + "%"}</span></span>
      <span class="np-bar"><i data-w="${pct.toFixed(2)}"></i></span></a>`;
  }).join("");
  setWidths(box);
  markNav();
}
setInterval(loadNav, 5000);
function openNav() { document.body.classList.add("nav-open"); $("navShade").hidden = false; }
function closeNav() { document.body.classList.remove("nav-open"); $("navShade").hidden = true; }
$("navOpen").onclick = openNav;
$("navClose").onclick = closeNav;
$("navShade").onclick = closeNav;
// New project: every way to start, in one menu (it used to be split between the sidebar and the home screen)
const newMenu = $("newMenu"), navNew = $("navNew");
const closeNew = () => { newMenu.hidden = true; navNew.setAttribute("aria-expanded", "false"); };
navNew.onclick = (e) => {
  e.stopPropagation();
  const open = newMenu.hidden;
  newMenu.hidden = !open;
  navNew.setAttribute("aria-expanded", String(open));
  if (open) newMenu.querySelector("button").focus();
};
newMenu.onclick = (e) => {
  const b = e.target.closest("[data-new]");
  if (!b) return;
  closeNew();
  closeNav();
  const what = b.dataset.new;
  if (what === "file") { if (location.hash && location.hash !== "#/") location.hash = "#/"; $("file").click(); }
  else if (what === "fork") location.hash = "#/catalog";
  else if (what === "join") openJoin();
  else if (what === "demo") $("demoBtn").click();
  else openConnect();
};
newMenu.addEventListener("keydown", (e) => {
  const items = [...newMenu.querySelectorAll("button")];
  if (e.key === "Escape") { closeNew(); navNew.focus(); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  }
});
document.addEventListener("click", (e) => { if (!newMenu.hidden && !e.target.closest(".nav-new-wrap")) closeNew(); });


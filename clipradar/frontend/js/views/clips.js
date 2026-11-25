import { api, on } from "../api.js";
import { ICONS, empty, esc } from "../ui.js";
import { clipCard } from "./dashboard.js";

const STATUS_FILTERS = [
  ["ready", "Listos"], ["detected,rendering", "Procesando"], ["discarded", "Descartados"], ["failed", "Con error"],
];

export default async function clips(root, ctx) {
  const cats = await api("/api/clips/categories");
  const state = { status: "ready", category: "", recommended: false, offset: 0, items: [], total: 0 };

  root.innerHTML = `
    <div class="row between mb"><h1>Clips</h1><span class="small muted" id="count"></span></div>
    <div class="filters" id="f-status">${STATUS_FILTERS.map(([v, l]) => `<button class="chip-toggle" data-status="${v}">${l}</button>`).join("")}
      <button class="chip-toggle" id="f-rec">★ Mejores para publicar</button></div>
    <div class="filters" id="f-cat"><button class="chip-toggle" data-cat="">Todas</button>${cats.map((c) => `<button class="chip-toggle" data-cat="${c.id}">${esc(c.label)}</button>`).join("")}</div>
    <div id="grid"></div>
    <div class="row mt" style="justify-content:center"><button class="btn hidden" id="more">Cargar más</button></div>`;

  const paintFilters = () => {
    root.querySelectorAll("[data-status]").forEach((b) => b.classList.toggle("on", b.dataset.status === state.status));
    root.querySelectorAll("[data-cat]").forEach((b) => b.classList.toggle("on", b.dataset.cat === state.category));
    root.querySelector("#f-rec").classList.toggle("on", state.recommended);
  };

  const load = async (append = false) => {
    const q = new URLSearchParams({ status: state.status, limit: 24, offset: append ? state.offset : 0 });
    if (state.category) q.set("category", state.category);
    if (state.recommended) q.set("recommended", "true");
    const res = await api(`/api/clips?${q}`);
    if (!ctx.isCurrent()) return;
    state.items = append ? state.items.concat(res.items) : res.items;
    state.total = res.total;
    state.offset = state.items.length;
    root.querySelector("#count").textContent = `${res.total} clip${res.total === 1 ? "" : "s"}`;
    root.querySelector("#grid").innerHTML = state.items.length
      ? `<div class="clip-grid">${state.items.map(cardWithError).join("")}</div>`
      : `<div class="card">${empty(ICONS.clips, "No hay clips con estos filtros", state.status === "ready" ? "Los clips aparecen cuando la IA detecta un momento en una transmisión activa." : "Prueba con otros filtros.")}</div>`;
    root.querySelector("#more").classList.toggle("hidden", state.items.length >= state.total);
  };

  root.querySelectorAll("[data-status]").forEach((b) => b.onclick = () => { state.status = b.dataset.status; paintFilters(); load(); });
  root.querySelectorAll("[data-cat]").forEach((b) => b.onclick = () => { state.category = b.dataset.cat; paintFilters(); load(); });
  root.querySelector("#f-rec").onclick = () => { state.recommended = !state.recommended; paintFilters(); load(); };
  root.querySelector("#more").onclick = () => load(true);
  paintFilters();
  await load();

  let t = null;
  const reload = () => { clearTimeout(t); t = setTimeout(() => load(), 700); };
  ctx.onCleanup(on("clip", reload));
  ctx.onCleanup(on("clip_deleted", reload));
  ctx.onCleanup(() => clearTimeout(t));
}

function cardWithError(c) {
  if (c.status === "failed" || c.status === "discarded") {
    return clipCard(c).replace('<div class="clip-body">', `<div class="clip-body"><p class="tiny" style="color:var(--${c.status === "failed" ? "bad" : "faint"})">${esc(c.error)}</p>`);
  }
  return clipCard(c);
}

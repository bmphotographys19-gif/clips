// ClipRadar AI — aplicación principal (router + estructura)
import { api, connectEvents, disconnectEvents, on } from "./api.js";
import { $, ICONS, esc } from "./ui.js";

const ROUTES = [
  { id: "dashboard", label: "Inicio", icon: ICONS.dash, load: () => import("./views/dashboard.js") },
  { id: "monitor", label: "Monitor", icon: ICONS.radar, load: () => import("./views/monitor.js") },
  { id: "clips", label: "Clips", icon: ICONS.clips, load: () => import("./views/clips.js") },
  { id: "publicaciones", label: "Publicar", icon: ICONS.send, load: () => import("./views/publish.js") },
  { id: "ajustes", label: "Ajustes", icon: ICONS.gear, load: () => import("./views/settings.js") },
];

let cleanup = [];
let renderToken = 0;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  return { parts, query: new URLSearchParams(query) };
}

function shell() {
  const links = ROUTES.map((r) => `<a class="side-link" data-route="${r.id}" href="#/${r.id}">${r.icon}<span>${r.label}</span></a>`).join("");
  const tabs = ROUTES.map((r) => `<a class="tab" data-route="${r.id}" href="#/${r.id}">${r.icon}<span>${r.label}</span></a>`).join("");
  $("#app").innerHTML = `
    <div class="shell">
      <nav class="sidebar"><div class="brand"><img src="/icons/icon-192.png" alt="">ClipRadar AI</div>${links}
        <div class="grow"></div><button class="btn ghost sm" id="logout">Cerrar sesión</button></nav>
      <main class="main">
        <div class="topbar"><div class="brand"><img src="/icons/icon-192.png" alt="">ClipRadar AI</div>
          <span class="row gap-s small muted" id="conn-wrap"><i class="conn" id="conn"></i><span id="conn-label">Conectando…</span></span></div>
        <div id="view"></div>
      </main>
      <nav class="tabbar">${tabs}</nav>
    </div>`;
  $("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };
  on("connection", ({ online }) => {
    $("#conn")?.classList.toggle("on", online);
    const l = $("#conn-label"); if (l) l.textContent = online ? "Tiempo real" : "Reconectando…";
  });
}

async function render() {
  const token = ++renderToken;
  cleanup.forEach((fn) => { try { fn(); } catch { /* */ } });
  cleanup = [];
  const { parts, query } = parseHash();
  const route = ROUTES.find((r) => r.id === parts[0]) || ROUTES[0];
  document.querySelectorAll("[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === route.id));
  const view = $("#view");
  view.innerHTML = `<div class="row" style="justify-content:center;padding:60px"><div class="spinner"></div></div>`;
  try {
    let mod = await route.load();
    if (route.id === "clips" && parts[1]) mod = await import("./views/editor.js");
    if (token !== renderToken) return;
    const ctx = { params: parts.slice(1), query, onCleanup: (fn) => cleanup.push(fn), isCurrent: () => token === renderToken };
    await mod.default(view, ctx);
    window.scrollTo(0, 0);
  } catch (err) {
    if (token !== renderToken) return;
    view.innerHTML = `<div class="notice bad">No se pudo cargar la vista: ${esc(err.message)}</div>`;
  }
}

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  let me;
  try { me = await api("/api/auth/me"); } catch { me = { authenticated: false }; }
  if (!me.authenticated) {
    const mod = await import("./views/login.js");
    mod.default($("#app"), me);
    return;
  }
  shell();
  connectEvents();
  window.addEventListener("hashchange", render);
  window.addEventListener("cr:unauthorized", () => { disconnectEvents(); location.reload(); }, { once: true });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") connectEvents(); });
  render();
}

boot();

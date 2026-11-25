import { api, on } from "../api.js";
import { ICONS, STREAM_STATUS, empty, esc, fmtBytes, fmtDuration, pill, scoreBadge, timeAgo } from "../ui.js";

export default async function dashboard(root, ctx) {
  const [stats, streams, clips, system, cats] = await Promise.all([
    api("/api/stats"), api("/api/streams"), api("/api/clips?status=ready&limit=8"),
    api("/api/system/status"), api("/api/clips/categories"),
  ]);
  const catLabel = Object.fromEntries(cats.map((c) => [c.id, c.label]));
  const live = streams.filter((s) => s.desired_state === "running");
  const problems = Object.entries(system.checks).filter(([k, v]) => !v.ok && !["youtube_api"].includes(k));
  const pubs = stats.publications || {};
  const published = (pubs.published || 0) + (pubs.sent_to_inbox || 0);
  const maxCat = Math.max(1, ...Object.values(stats.clips_by_category));

  root.innerHTML = `
    <div class="row between mb"><h1>Dashboard</h1>
      <a class="btn primary sm" href="#/monitor?new=1">${ICONS.plus}Nueva transmisión</a></div>

    ${problems.length ? `<div class="notice warn mb">Configuración pendiente: ${problems.map(([k, v]) => `<b>${esc(k)}</b> (${esc(v.detail)})`).join(" · ")}. <a href="#/ajustes">Ver ajustes</a></div>` : ""}

    <div class="grid grid-4">
      <div class="stat"><div class="stat-label">Transmisiones en vivo</div><div class="stat-value" id="st-live">${stats.streams_live}</div><div class="stat-sub">${live.length} activadas</div></div>
      <div class="stat"><div class="stat-label">Clips listos</div><div class="stat-value" id="st-ready">${stats.clips_ready}</div><div class="stat-sub">${stats.clips_total} detectados en total</div></div>
      <div class="stat"><div class="stat-label">Publicados en TikTok</div><div class="stat-value">${published}</div><div class="stat-sub">${pubs.failed ? `${pubs.failed} con error` : "directos o en bandeja"}</div></div>
      <div class="stat"><div class="stat-label">Horas analizadas</div><div class="stat-value">${stats.hours_analyzed}</div><div class="stat-sub">${stats.avg_score != null ? `puntuación media ${stats.avg_score}` : "sin clips aún"}</div></div>
    </div>

    <div class="section-title">Monitoreo activo</div>
    <div id="live-list">${live.length ? live.map(streamRow).join("") : `<div class="card">${empty(ICONS.radar, "Sin transmisiones activas", "Añade el enlace de un directo autorizado para empezar a detectar momentos.", `<a class="btn primary" href="#/monitor?new=1">${ICONS.plus}Añadir transmisión</a>`)}</div>`}</div>

    <div class="section-title row between"><span>Mejores clips recientes</span><a class="small" href="#/clips">Ver todos</a></div>
    ${clips.items.length ? `<div class="clip-grid">${clips.items.map(clipCard).join("")}</div>` : `<div class="card">${empty(ICONS.clips, "Todavía no hay clips", "Cuando la IA detecte un momento y termine de renderizarlo aparecerá aquí.")}</div>`}

    <div class="grid grid-md-2 mt-l">
      <div class="card"><div class="card-title"><h3>Clips por categoría</h3></div>
        ${Object.keys(stats.clips_by_category).length ? `<div class="bars">${Object.entries(stats.clips_by_category).sort((a, b) => b[1] - a[1]).map(([k, v]) => `
          <div class="bar-row"><span class="ellipsis">${esc(catLabel[k] || k)}</span><div class="meter"><i style="width:${(v / maxCat) * 100}%"></i></div><b>${v}</b></div>`).join("")}</div>`
          : `<p class="muted small">Sin datos todavía.</p>`}
      </div>
      <div class="card"><div class="card-title"><h3>Almacenamiento</h3><span class="chip">${esc(system.storage_backend === "s3" ? "S3" : "Disco local")}</span></div>
        <div class="kv" style="grid-template-columns:repeat(2,minmax(0,1fr))">
          <div><b>${fmtBytes(stats.storage.clips_bytes)}</b><span>Clips renderizados</span></div>
          <div><b>${fmtBytes(stats.storage.buffer_bytes)}</b><span>Buffer de directos</span></div>
          <div><b>${fmtBytes(stats.storage.work_bytes)}</b><span>Cortes fuente</span></div>
          <div><b>${fmtBytes(system.disk.free)}</b><span>Libre en el servidor</span></div>
        </div>
        <div class="meter mt"><i style="width:${(system.disk.used / system.disk.total) * 100}%"></i></div>
        <p class="tiny faint mt">Disco usado: ${fmtBytes(system.disk.used)} de ${fmtBytes(system.disk.total)}</p>
      </div>
    </div>`;

  const refresh = async () => {
    if (!ctx.isCurrent()) return;
    const [s2, st2] = await Promise.all([api("/api/stats"), api("/api/streams")]);
    root.querySelector("#st-live").textContent = s2.streams_live;
    root.querySelector("#st-ready").textContent = s2.clips_ready;
    const l2 = st2.filter((s) => s.desired_state === "running");
    if (l2.length) root.querySelector("#live-list").innerHTML = l2.map(streamRow).join("");
  };
  let pending = null;
  const debounced = () => { clearTimeout(pending); pending = setTimeout(refresh, 800); };
  ctx.onCleanup(on("stream", debounced));
  ctx.onCleanup(on("clip", debounced));
  ctx.onCleanup(() => clearTimeout(pending));
}

function streamRow(s) {
  return `<a class="card stream-card" href="#/monitor/${s.id}">
    <div class="stream-head"><div class="stream-thumb">${ICONS.radar}</div>
      <div class="grow"><div class="row between gap"><b class="ellipsis">${esc(s.title)}</b>${pill(STREAM_STATUS[s.status] || [s.status, "muted"])}</div>
      <p class="small muted ellipsis">${esc(s.channel_name || s.source_url)} · analizado ${fmtDuration(s.seconds_analyzed)} · ${s.clip_counts?.ready || 0} clips</p></div></div></a>`;
}

export function clipCard(c) {
  const state = c.status !== "ready" ? `<span class="state">${c.status === "rendering" ? '<span class="pill pill-warn">Procesando</span>' : ""}</span>` : "";
  return `<a class="clip-card" href="#/clips/${c.id}">
    <div class="clip-thumb" style="${c.thumb_url ? `background-image:url('${esc(c.thumb_url)}')` : ""}">
      ${c.thumb_url ? "" : '<div class="spinner"></div>'}
      ${scoreBadge(c.score)}${c.recommended ? `<span class="rec">${ICONS.star}</span>` : ""}
      ${c.duration ? `<span class="dur">${Math.round(c.duration)}s</span>` : ""}${state}
    </div>
    <div class="clip-body"><div class="clip-title">${esc(c.title)}</div>
      <div class="row between"><span class="chip">${esc(c.category_label)}</span><span class="tiny faint">${timeAgo(c.created_at)}</span></div></div></a>`;
}

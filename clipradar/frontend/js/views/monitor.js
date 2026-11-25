import { api, on } from "../api.js";
import {
  ICONS, STREAM_STATUS, clockTime, confirmDialog, empty, esc, fmtDuration, modal, pill, timeAgo, toast,
} from "../ui.js";
import { clipCard } from "./dashboard.js";

let CATS = [];

export default async function monitor(root, ctx) {
  CATS = await api("/api/clips/categories");
  if (ctx.params[0]) return detail(root, ctx, Number(ctx.params[0]));
  return list(root, ctx);
}

// ------------------------------------------------------------------ lista
async function list(root, ctx) {
  const draw = async () => {
    const streams = await api("/api/streams");
    if (!ctx.isCurrent()) return;
    root.innerHTML = `
      <div class="row between mb"><h1>Monitor en vivo</h1>
        <button class="btn primary sm" id="add">${ICONS.plus}Añadir</button></div>
      ${streams.length ? streams.map(row).join("") :
        `<div class="card">${empty(ICONS.radar, "Ninguna transmisión", "Pega el enlace de un directo de YouTube autorizado o una URL HLS/RTMP. El servidor lo procesa 24/7 aunque cierres la app.", `<button class="btn primary" id="add2">${ICONS.plus}Añadir transmisión</button>`)}</div>`}`;
    root.querySelector("#add").onclick = () => addStream();
    root.querySelector("#add2")?.addEventListener("click", () => addStream());
    root.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      const { toggle, id } = b.dataset;
      b.disabled = true;
      try { await api(`/api/streams/${id}/${toggle}`, { method: "POST" }); } catch (err) { toast(err.message, "error"); }
      draw();
    }));
  };
  await draw();
  if (ctx.query.get("new")) addStream();
  let t = null;
  ctx.onCleanup(on("stream", () => { clearTimeout(t); t = setTimeout(draw, 500); }));
  ctx.onCleanup(on("stream_deleted", draw));
  ctx.onCleanup(() => clearTimeout(t));
}

function row(s) {
  const running = s.desired_state === "running";
  return `<a class="card stream-card" href="#/monitor/${s.id}">
    <div class="stream-head"><div class="stream-thumb">${ICONS.radar}</div>
      <div class="grow">
        <div class="row between gap"><b class="ellipsis">${esc(s.title)}</b>${pill(STREAM_STATUS[s.status] || [s.status, "muted"])}</div>
        <p class="small muted ellipsis">${esc(s.channel_name || s.source_url)}</p>
        <p class="tiny faint">${s.status_message ? esc(s.status_message) + " · " : ""}${fmtDuration(s.seconds_analyzed)} analizados · ${s.clip_counts?.ready || 0} clips listos</p>
      </div>
      <button class="btn sm ${running ? "danger" : "primary"}" data-toggle="${running ? "stop" : "start"}" data-id="${s.id}">
        ${running ? ICONS.stop + "Detener" : ICONS.play + "Iniciar"}</button>
    </div></a>`;
}

function catChips(selected) {
  return CATS.map((c) => `<button type="button" class="chip-toggle ${selected.includes(c.id) ? "on" : ""}" data-cat="${c.id}">${esc(c.label)}</button>`).join("");
}

function bindChips(root) {
  root.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => b.classList.toggle("on")));
  return () => [...root.querySelectorAll("[data-cat].on")].map((b) => b.dataset.cat);
}

function addStream() {
  modal(`
    <h3>Nueva transmisión</h3>
    <p class="muted small mb">El servidor se conecta a la señal y la analiza continuamente. Puedes cerrar la app.</p>
    <form id="f">
      <label class="field"><span>Enlace del directo</span>
        <input type="url" id="url" required placeholder="https://www.youtube.com/watch?v=…  o  https://…/stream.m3u8">
        <p class="hint">YouTube Live, o una señal HLS/RTMP/SRT que el creador te envíe (re-stream).</p></label>
      <label class="field"><span>Autorización del creador</span>
        <textarea id="perm" required placeholder="Ej.: El creador me autorizó por email el 20/11 a publicar clips con crédito."></textarea>
        <p class="hint">Obligatorio: solo procesa directos de creadores que te hayan dado permiso.</p></label>
      <label class="field"><span>Crédito en cada publicación</span><input type="text" id="credit" placeholder="@canal (se rellena con el nombre del canal si lo dejas vacío)"></label>
      <label class="field"><span>Puntuación mínima para crear clips: <b id="sv">60</b></span><input type="range" id="score" min="30" max="95" value="60"></label>
      <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Categorías (ninguna = todas)</span><div class="chips">${catChips([])}</div></div>
      <div class="row end gap mt"><button type="button" class="btn ghost" data-close>Cancelar</button>
        <button class="btn primary" id="go">${ICONS.play}Crear e iniciar</button></div>
    </form>`, {
    onMount(m, close) {
      const getCats = bindChips(m);
      m.querySelector("#score").oninput = (e) => { m.querySelector("#sv").textContent = e.target.value; };
      m.querySelector("#f").onsubmit = async (e) => {
        e.preventDefault();
        const btn = m.querySelector("#go");
        btn.disabled = true; btn.textContent = "Comprobando la fuente…";
        try {
          const s = await api("/api/streams", { method: "POST", body: {
            url: m.querySelector("#url").value.trim(), permission_note: m.querySelector("#perm").value.trim(),
            credit: m.querySelector("#credit").value.trim(), min_score: Number(m.querySelector("#score").value),
            categories: getCats(), start: true,
          } });
          close();
          toast("Transmisión creada. El monitor se conectará en unos segundos.", "ok");
          location.hash = `#/monitor/${s.id}`;
        } catch (err) {
          toast(err.message, "error");
          btn.disabled = false; btn.innerHTML = `${ICONS.play}Crear e iniciar`;
        }
      };
    },
  });
}

// ------------------------------------------------------------------ detalle
async function detail(root, ctx, id) {
  let s;
  try { s = await api(`/api/streams/${id}`); } catch (err) {
    root.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; return;
  }
  const [lines, logs, clips] = await Promise.all([
    api(`/api/streams/${id}/transcript?limit=80`), api(`/api/activity?stream_id=${id}&limit=40`),
    api(`/api/clips?stream_id=${id}&limit=12`),
  ]);

  root.innerHTML = `
    <div class="row gap mb"><a class="btn icon ghost" href="#/monitor">${ICONS.back}</a>
      <div class="grow"><h2 class="ellipsis" id="title">${esc(s.title)}</h2><p class="small muted ellipsis">${esc(s.channel_name || s.source_url)}</p></div></div>
    <div class="card">
      <div class="row between gap wrap"><div class="row gap-s" id="status"></div><div class="row gap-s" id="actions"></div></div>
      <p class="small muted mt" id="msg"></p>
      <div class="kv" id="kv"></div>
    </div>

    <div class="grid grid-md-2 mt">
      <div class="card"><div class="card-title"><h3>Transcripción en vivo</h3><span class="tiny faint">Whisper · señales de audio/vídeo</span></div>
        <div class="feed" id="feed"></div></div>
      <div class="card"><div class="card-title"><h3>Actividad</h3></div><div class="feed" id="log"></div></div>
    </div>

    <div class="section-title">Clips de esta transmisión</div>
    <div id="clips"></div>

    <div class="section-title">Ajustes de la transmisión</div>
    <div class="card" id="cfg">
      <label class="field"><span>Puntuación mínima: <b id="sv">${s.min_score}</b></span><input type="range" id="score" min="30" max="95" value="${s.min_score}"></label>
      <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Categorías (ninguna = todas)</span><div class="chips">${catChips(s.categories)}</div></div>
      <label class="field"><span>Crédito al creador</span><input type="text" id="credit" value="${esc(s.credit)}"></label>
      <label class="field"><span>Autorización registrada</span><textarea id="perm">${esc(s.permission_note)}</textarea></label>
      <div class="switch"><div><b>Publicación automática</b><p class="small muted">Requiere activarla también en Publicar → Automática.</p></div><input type="checkbox" id="auto" ${s.auto_publish ? "checked" : ""}></div>
      <div class="row between mt"><button class="btn danger sm" id="del">${ICONS.trash}Eliminar</button><button class="btn primary" id="save">Guardar</button></div>
    </div>`;

  const paint = (st) => {
    s = st;
    const running = st.desired_state === "running";
    root.querySelector("#title").textContent = st.title;
    root.querySelector("#status").innerHTML = pill(STREAM_STATUS[st.status] || [st.status, "muted"]);
    root.querySelector("#msg").textContent = st.status_message || "";
    root.querySelector("#actions").innerHTML = running
      ? `<button class="btn danger sm" id="stop">${ICONS.stop}Detener</button>`
      : `<button class="btn primary sm" id="start">${ICONS.play}Iniciar</button>`;
    root.querySelector("#kv").innerHTML = `
      <div><b>${fmtDuration(st.seconds_captured)}</b><span>Capturado</span></div>
      <div><b>${fmtDuration(st.seconds_analyzed)}</b><span>Analizado</span></div>
      <div><b>${timeAgo(st.last_segment_at)}</b><span>Última señal</span></div>
      <div><b>${timeAgo(st.last_analysis_at)}</b><span>Último análisis IA</span></div>`;
    root.querySelector("#stop")?.addEventListener("click", () => act("stop"));
    root.querySelector("#start")?.addEventListener("click", () => act("start"));
  };
  const act = async (what) => {
    try { paint(await api(`/api/streams/${id}/${what}`, { method: "POST" })); } catch (err) { toast(err.message, "error"); }
  };
  paint(s);

  const feed = root.querySelector("#feed");
  const addLines = (ls) => {
    if (!ls.length && !feed.children.length) {
      feed.innerHTML = `<p class="muted small" id="feed-empty">La transcripción aparecerá aquí tras el primer análisis (~${60} s después de conectar).</p>`;
      return;
    }
    feed.querySelector("#feed-empty")?.remove();
    const stick = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 30;
    feed.insertAdjacentHTML("beforeend", ls.map((l) => `<div class="feed-line ${l.loud ? "loud" : ""}"><time>${clockTime(l.t)}</time>
      <span>${l.loud ? '<span class="tag-mini tag-loud">VOLUMEN</span>' : ""}${l.scene ? '<span class="tag-mini tag-scene">ESCENA</span>' : ""}${esc(l.text)}</span></div>`).join(""));
    while (feed.children.length > 300) feed.firstChild.remove();
    if (stick) feed.scrollTop = feed.scrollHeight;
  };
  addLines(lines);
  feed.scrollTop = feed.scrollHeight;

  const log = root.querySelector("#log");
  const addLog = (items, prepend) => {
    const html = items.map((a) => `<div class="log-line ${a.level}"><time class="tiny faint">${new Date(a.created_at).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}</time><span>${esc(a.message)}</span></div>`).join("");
    if (prepend) log.insertAdjacentHTML("afterbegin", html); else log.innerHTML = html || `<p class="muted small">Sin actividad todavía.</p>`;
  };
  addLog(logs, false);

  const drawClips = async () => {
    const c = await api(`/api/clips?stream_id=${id}&limit=12`);
    if (!ctx.isCurrent()) return;
    root.querySelector("#clips").innerHTML = c.items.length ? `<div class="clip-grid">${c.items.map(clipCard).join("")}</div>`
      : `<div class="card">${empty(ICONS.clips, "Sin clips todavía", "La IA analiza la conversación cada minuto. Los momentos detectados aparecerán aquí.")}</div>`;
  };
  root.querySelector("#clips").innerHTML = clips.items.length ? `<div class="clip-grid">${clips.items.map(clipCard).join("")}</div>`
    : `<div class="card">${empty(ICONS.clips, "Sin clips todavía", "La IA analiza la conversación cada minuto. Los momentos detectados aparecerán aquí.")}</div>`;

  // Ajustes
  const getCats = bindChips(root.querySelector("#cfg"));
  root.querySelector("#score").oninput = (e) => { root.querySelector("#sv").textContent = e.target.value; };
  root.querySelector("#save").onclick = async () => {
    try {
      await api(`/api/streams/${id}`, { method: "PATCH", body: {
        min_score: Number(root.querySelector("#score").value), categories: getCats(),
        credit: root.querySelector("#credit").value, permission_note: root.querySelector("#perm").value,
        auto_publish: root.querySelector("#auto").checked,
      } });
      toast("Ajustes guardados", "ok");
    } catch (err) { toast(err.message, "error"); }
  };
  root.querySelector("#del").onclick = async () => {
    if (!(await confirmDialog("Eliminar transmisión", "Se borrarán también todos sus clips. No se puede deshacer.", "Eliminar", true))) return;
    try { await api(`/api/streams/${id}`, { method: "DELETE" }); location.hash = "#/monitor"; } catch (err) { toast(err.message, "error"); }
  };

  // Tiempo real
  ctx.onCleanup(on("stream", (d) => { if (d.id === id) paint({ ...s, ...d }); }));
  ctx.onCleanup(on("transcript", (d) => { if (d.stream_id === id) addLines(d.lines); }));
  ctx.onCleanup(on("activity", (a) => { if (a.stream_id === id) addLog([a], true); }));
  let t = null;
  ctx.onCleanup(on("clip", (c) => { if (c.stream_id === id) { clearTimeout(t); t = setTimeout(drawClips, 600); } }));
  const tick = setInterval(() => paint(s), 10000);
  ctx.onCleanup(() => { clearInterval(tick); clearTimeout(t); });
}

import { api } from "../api.js";
import { esc, toast } from "../ui.js";

const CHECK_LABELS = {
  database: "Base de datos", redis: "Redis (cola y tiempo real)", monitor: "Servicio de monitoreo", workers: "Trabajadores (render/publicación)",
  ffmpeg: "FFmpeg", yt_dlp: "yt-dlp", ai: "IA (Claude)", transcription: "Transcripción", tiktok: "App de TikTok", youtube_api: "YouTube Data API",
};

export default async function settingsView(root, ctx) {
  const [settings, system, cats] = await Promise.all([api("/api/settings"), api("/api/system/status"), api("/api/clips/categories")]);
  const det = settings.detection, ed = settings.editor;

  root.innerHTML = `
    <h1 class="mb">Ajustes</h1>

    <div class="card"><div class="card-title"><h3>Estado del sistema</h3><button class="btn sm ghost" id="refresh">Actualizar</button></div>
      <div id="checks"></div>
      <p class="tiny faint mt">Servidor: ${esc(system.public_url)} · Almacenamiento: ${esc(system.storage_backend)}</p></div>

    <div class="section-title">Detección con IA</div>
    <div class="card">
      <label class="field"><span>Puntuación mínima por defecto: <b id="sv">${det.min_score}</b></span><input type="range" id="d-score" min="30" max="95" value="${det.min_score}"></label>
      <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Categorías por defecto</span>
        <div class="chips">${cats.map((c) => `<button type="button" class="chip-toggle ${det.categories.includes(c.id) ? "on" : ""}" data-cat="${c.id}">${esc(c.label)}</button>`).join("")}</div></div>
      <div class="grid grid-2">
        <label class="field"><span>Máx. clips por hora</span><input type="number" id="d-max" min="1" max="60" value="${det.max_clips_per_hour}"></label>
        <label class="field"><span>Umbral de duplicados</span><input type="number" id="d-dup" min="0.3" max="0.95" step="0.05" value="${det.dedupe_similarity}"></label>
      </div>
      <p class="hint">El umbral de duplicados compara el texto de los clips cercanos: más bajo = descarta más repeticiones.</p>
      <div class="row end mt"><button class="btn primary" id="d-save">Guardar</button></div>
    </div>

    <div class="section-title">Editor por defecto</div>
    <div class="card">
      <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Encuadre</span>
        <div class="seg" id="e-layout">${[["auto", "Automático"], ["track", "Seguir al que habla"], ["blur", "Completo + fondo"]].map(([v, l]) => `<button data-v="${v}" class="${ed.layout === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Estilo de subtítulos</span>
        <div class="seg" id="e-style">${[["highlight", "Palabra resaltada"], ["simple", "Simple"], ["box", "Con caja"]].map(([v, l]) => `<button data-v="${v}" class="${ed.subtitle_style === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <p class="hint">Salida: MP4 H.264, 1080×1920, AAC 48 kHz, sonoridad normalizada a −14 LUFS.</p>
      <div class="row end mt"><button class="btn primary" id="e-save">Guardar</button></div>
    </div>

    <div class="section-title">Instalar en el móvil</div>
    <div class="card small muted">
      <p><b style="color:var(--text)">iPhone:</b> abre esta página en Safari → botón Compartir → "Añadir a pantalla de inicio".</p>
      <p class="mt"><b style="color:var(--text)">Android:</b> en Chrome → menú ⋮ → "Instalar aplicación".</p>
    </div>

    <div class="row end mt-l"><button class="btn danger" id="logout">Cerrar sesión</button></div>`;

  const drawChecks = (sys) => {
    root.querySelector("#checks").innerHTML = Object.entries(sys.checks).map(([k, v]) => `
      <div class="list-item"><i class="check ${v.ok ? "ok" : "bad"}"></i><div class="grow"><b class="small">${esc(CHECK_LABELS[k] || k)}</b>
      <p class="tiny faint">${esc(v.detail)}</p></div></div>`).join("");
  };
  drawChecks(system);
  root.querySelector("#refresh").onclick = async () => drawChecks(await api("/api/system/status"));

  root.querySelectorAll("[data-cat]").forEach((b) => b.onclick = () => b.classList.toggle("on"));
  root.querySelector("#d-score").oninput = (e) => { root.querySelector("#sv").textContent = e.target.value; };
  root.querySelector("#d-save").onclick = async () => {
    try {
      await api("/api/settings/detection", { method: "PUT", body: {
        min_score: Number(root.querySelector("#d-score").value),
        categories: [...root.querySelectorAll("[data-cat].on")].map((b) => b.dataset.cat),
        max_clips_per_hour: Number(root.querySelector("#d-max").value),
        dedupe_similarity: Number(root.querySelector("#d-dup").value),
      } });
      toast("Ajustes de detección guardados", "ok");
    } catch (err) { toast(err.message, "error"); }
  };

  const segVal = (sel, init) => {
    let val = init;
    root.querySelectorAll(`${sel} button`).forEach((b) => b.onclick = () => {
      val = b.dataset.v; root.querySelectorAll(`${sel} button`).forEach((x) => x.classList.toggle("on", x === b));
    });
    return () => val;
  };
  const layout = segVal("#e-layout", ed.layout), style = segVal("#e-style", ed.subtitle_style);
  root.querySelector("#e-save").onclick = async () => {
    try {
      await api("/api/settings/editor", { method: "PUT", body: { layout: layout(), subtitle_style: style() } });
      toast("Ajustes del editor guardados", "ok");
    } catch (err) { toast(err.message, "error"); }
  };
  root.querySelector("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };
}

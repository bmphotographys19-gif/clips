import { api, on } from "../api.js";
import { ICONS, PUB_STATUS, confirmDialog, empty, esc, pill, timeAgo, toast } from "../ui.js";

export default async function publish(root, ctx) {
  const flag = ctx.query.get("tiktok");
  if (flag) {
    const msg = { ok: ["Cuenta de TikTok conectada", "ok"], denied: ["Autorización cancelada en TikTok", "error"],
      error: ["No se pudo completar la conexión con TikTok", "error"], state_error: ["Enlace de autorización caducado; vuelve a intentarlo", "error"] }[flag];
    if (msg) toast(msg[0], msg[1]);
    history.replaceState(null, "", "#/publicaciones");
  }

  const [status, settings, pubs] = await Promise.all([api("/api/tiktok/status"), api("/api/settings"), api("/api/publications")]);
  const cfg = settings.publishing;
  const acc = status.account;

  root.innerHTML = `
    <h1 class="mb">Publicaciones</h1>

    <div class="card"><div class="card-title"><h3>Cuenta de TikTok</h3>${acc ? pill(["Conectada", "ok"]) : pill(["Sin conectar", "muted"])}</div>
      ${!status.configured ? `<div class="notice warn">Faltan las credenciales de la app de TikTok en el servidor (TIKTOK_CLIENT_KEY y TIKTOK_CLIENT_SECRET).
        Créalas en developers.tiktok.com con Login Kit y Content Posting API, y registra esta URL de redirección:<br><code class="mono">${esc(status.redirect_uri)}</code></div>`
      : acc ? `<div class="row gap">${acc.avatar_url ? `<img src="${esc(acc.avatar_url)}" style="width:44px;height:44px;border-radius:50%">` : ""}
          <div class="grow"><b>${esc(acc.display_name || acc.open_id)}</b><p class="tiny faint">Permisos: ${esc(acc.scopes)}</p></div>
          <button class="btn danger sm" id="disc">Desconectar</button></div>`
      : `<p class="muted small mb">Autoriza a ClipRadar a subir vídeos a tu cuenta con la API oficial (OAuth). Puedes revocarlo cuando quieras.</p>
         <button class="btn primary" id="conn">Conectar con TikTok</button>`}
    </div>

    <div class="section-title">Publicación automática</div>
    <div class="card" id="auto">
      <div class="notice mb">Se publicarán automáticamente solo los clips <b>recomendados</b> por la IA que superen la puntuación mínima,
        de transmisiones con la auto-publicación activada, respetando el límite diario. Desactivada por defecto.</div>
      <div class="switch"><div><b>Activar publicación automática</b><p class="small muted">${acc ? "" : "Conecta TikTok primero."}</p></div>
        <input type="checkbox" id="a-on" ${cfg.auto_publish ? "checked" : ""} ${acc ? "" : "disabled"}></div>
      <div class="field mt"><span class="small muted" style="display:block;margin-bottom:8px">Modo</span>
        <div class="seg" id="a-mode"><button data-v="inbox" class="${cfg.mode === "inbox" ? "on" : ""}">Bandeja (revisas en TikTok)</button><button data-v="direct" class="${cfg.mode === "direct" ? "on" : ""}">Directa</button></div></div>
      <label class="field"><span>Privacidad en modo directo</span><select id="a-priv">
        ${[["SELF_ONLY", "Solo yo"], ["MUTUAL_FOLLOW_FRIENDS", "Amigos"], ["FOLLOWER_OF_CREATOR", "Seguidores"], ["PUBLIC_TO_EVERYONE", "Público"]].map(([v, l]) => `<option value="${v}" ${cfg.privacy_level === v ? "selected" : ""}>${l}</option>`).join("")}
        </select><p class="hint">Si tu cuenta no permite la opción elegida, la publicación fallará sin publicar nada.</p></label>
      <div class="grid grid-2">
        <label class="field"><span>Puntuación mínima</span><input type="number" id="a-score" min="50" max="100" value="${cfg.min_score}"></label>
        <label class="field"><span>Máx. por día</span><input type="number" id="a-max" min="1" max="15" value="${cfg.max_per_day}"></label>
      </div>
      <label class="field"><span>Minutos mínimos entre publicaciones</span><input type="number" id="a-gap" min="10" max="1440" value="${cfg.min_minutes_between}"></label>
      <label class="field"><span>Plantilla de crédito</span><input type="text" id="a-credit" value="${esc(cfg.credit_template)}"><p class="hint">{credit} se sustituye por el crédito de cada transmisión.</p></label>
      <div class="switch"><span>Desactivar comentarios</span><input type="checkbox" id="a-cmt" ${cfg.disable_comment ? "checked" : ""}></div>
      <div class="switch"><span>Desactivar dúos</span><input type="checkbox" id="a-duet" ${cfg.disable_duet ? "checked" : ""}></div>
      <div class="switch"><span>Desactivar stitch</span><input type="checkbox" id="a-stitch" ${cfg.disable_stitch ? "checked" : ""}></div>
      <label class="row gap-s small mt" style="align-items:flex-start"><input type="checkbox" id="a-consent" ${cfg.consent_confirmed ? "checked" : ""} style="margin-top:3px">
        <span>Autorizo a ClipRadar a publicar en mi cuenta de TikTok, sin revisión previa, los clips que cumplan estas condiciones. Confirmo que tengo permiso de los creadores y acepto la
        <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener">confirmación de uso de música</a> de TikTok.</span></label>
      <div class="row end mt"><button class="btn primary" id="a-save">Guardar</button></div>
    </div>

    <div class="section-title">Historial</div>
    <div class="card" id="list"></div>`;

  const drawList = (items) => {
    root.querySelector("#list").innerHTML = items.length ? items.map((p) => `
      <div class="list-item">
        <a href="#/clips/${p.clip_id}" style="width:46px;aspect-ratio:9/16;border-radius:8px;background:var(--surface-2) center/cover;background-image:url('${esc(p.thumb_url)}');flex:none"></a>
        <div class="grow"><b class="small ellipsis" style="display:block">${esc(p.clip_title)}</b>
          <p class="tiny faint">${p.mode === "direct" ? "Directa" : "Bandeja"} · ${p.trigger === "auto" ? "automática" : "manual"} · ${timeAgo(p.created_at)}</p>
          ${p.error ? `<p class="tiny" style="color:var(--bad)">${esc(p.error)}</p>` : ""}</div>
        <div class="col gap-s" style="align-items:flex-end">${pill(PUB_STATUS[p.status] || [p.status, "muted"])}
          ${p.status === "failed" ? `<button class="btn sm" data-retry="${p.id}">Reintentar</button>` : ""}</div>
      </div>`).join("") : empty(ICONS.send, "Sin publicaciones", "Abre un clip listo y pulsa Publicar.");
    root.querySelectorAll("[data-retry]").forEach((b) => b.onclick = async () => {
      try { await api(`/api/publications/${b.dataset.retry}/retry`, { method: "POST" }); drawList(await api("/api/publications")); }
      catch (err) { toast(err.message, "error"); }
    });
  };
  drawList(pubs);

  root.querySelector("#conn")?.addEventListener("click", async () => {
    try { const { url } = await api("/api/tiktok/connect"); location.href = url; } catch (err) { toast(err.message, "error"); }
  });
  root.querySelector("#disc")?.addEventListener("click", async () => {
    if (!(await confirmDialog("Desconectar TikTok", "Se revocará el acceso y se detendrá la publicación automática.", "Desconectar", true))) return;
    await api("/api/tiktok/disconnect", { method: "POST" });
    await api("/api/settings/publishing", { method: "PUT", body: { auto_publish: false } });
    location.reload();
  });

  let mode = cfg.mode;
  root.querySelectorAll("#a-mode button").forEach((b) => b.onclick = () => {
    mode = b.dataset.v; root.querySelectorAll("#a-mode button").forEach((x) => x.classList.toggle("on", x === b));
  });
  root.querySelector("#a-save").onclick = async () => {
    const v = (id) => root.querySelector(id);
    try {
      await api("/api/settings/publishing", { method: "PUT", body: {
        auto_publish: v("#a-on").checked, mode, privacy_level: v("#a-priv").value,
        min_score: Number(v("#a-score").value), max_per_day: Number(v("#a-max").value),
        min_minutes_between: Number(v("#a-gap").value), credit_template: v("#a-credit").value,
        disable_comment: v("#a-cmt").checked, disable_duet: v("#a-duet").checked, disable_stitch: v("#a-stitch").checked,
        consent_confirmed: v("#a-consent").checked,
      } });
      toast("Publicación automática guardada", "ok");
    } catch (err) { toast(err.message, "error"); }
  };

  ctx.onCleanup(on("publication", async () => { if (ctx.isCurrent()) drawList(await api("/api/publications")); }));
}

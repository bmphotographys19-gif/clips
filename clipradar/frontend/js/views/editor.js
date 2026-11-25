import { api, on } from "../api.js";
import {
  CLIP_STATUS, ICONS, PUB_STATUS, confirmDialog, esc, fmtClock, modal, pill, scoreBadge, timeAgo, toast,
} from "../ui.js";

export default async function editor(root, ctx) {
  const id = Number(ctx.params[0]);
  let clip;
  try { clip = await api(`/api/clips/${id}`); } catch (err) {
    root.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; return;
  }
  let cues = structuredClone(clip.subtitles);
  let dirty = false;

  const draw = () => {
    const busy = clip.status === "rendering" || clip.status === "detected";
    root.innerHTML = `
      <div class="row gap mb"><a class="btn icon ghost" href="#/clips">${ICONS.back}</a>
        <div class="grow"><h2 class="ellipsis">Editor</h2><p class="small muted ellipsis">${esc(clip.stream_title)} · ${timeAgo(clip.created_at)}</p></div>
        ${pill(CLIP_STATUS[clip.status] || [clip.status, "muted"])}</div>

      <div class="editor">
        <div class="player-col">
          ${clip.video_url ? `<video class="player" id="player" src="${esc(clip.video_url)}" controls playsinline preload="metadata" poster="${esc(clip.thumb_url)}"></video>`
            : `<div class="player row" style="justify-content:center">${busy ? '<div class="spinner"></div>' : `<p class="muted small">Sin vídeo</p>`}</div>`}
          ${busy ? `<div class="notice mt">Procesando: revisión visual con IA y render 1080×1920…</div>` : ""}
          ${clip.error ? `<div class="notice ${clip.status === "failed" ? "bad" : "warn"} mt">${esc(clip.error)}</div>` : ""}
          <div class="grid grid-2 mt">
            <button class="btn primary" id="publish" ${clip.status !== "ready" ? "disabled" : ""}>${ICONS.send}Publicar</button>
            ${clip.video_url ? `<a class="btn" href="${esc(clip.video_url)}" download="clip_${clip.id}.mp4">Descargar MP4</a>` : `<button class="btn" disabled>Descargar MP4</button>`}
          </div>
          ${clip.publications.length ? `<div class="card mt"><h3 class="mb">Publicaciones</h3>${clip.publications.map((p) => `
            <div class="list-item"><div class="grow"><b class="small">${p.mode === "direct" ? "Directa" : "Bandeja de TikTok"}</b>
            <p class="tiny faint">${timeAgo(p.created_at)}${p.error ? " · " + esc(p.error) : ""}</p></div>${pill(PUB_STATUS[p.status] || [p.status, "muted"])}</div>`).join("")}</div>` : ""}
        </div>

        <div>
          <div class="card">
            <div class="row between mb"><div class="row gap-s">${scoreBadge(clip.score)}<span class="chip">${esc(clip.category_label)}</span>${clip.recommended ? `<span class="chip" style="color:#fbbf24">★ Recomendado</span>` : ""}</div>
              <span class="tiny faint">${clip.duration ? Math.round(clip.duration) + " s" : ""}</span></div>
            <p class="small muted">${esc(clip.reason)}</p>
          </div>

          <div class="card"><h3 class="mb">Texto para TikTok</h3>
            <label class="field"><span>Título</span><input type="text" id="title" maxlength="150" value="${esc(clip.title)}"></label>
            <label class="field"><span>Descripción</span><textarea id="desc">${esc(clip.description)}</textarea></label>
            <label class="field"><span>Hashtags (separados por espacios)</span><input type="text" id="tags" value="${esc(clip.hashtags.map((h) => "#" + h).join(" "))}"></label>
            <div class="row end"><button class="btn sm" id="save-text">Guardar texto</button></div>
          </div>

          <div class="card"><div class="card-title"><h3>Vídeo</h3></div>
            <div class="field"><span class="small muted" style="display:block;margin-bottom:8px">Encuadre</span>
              <div class="seg" id="layout">${[["auto", "Automático"], ["track", "Seguir al que habla"], ["blur", "Completo + fondo"]].map(([v, l]) => `<button data-v="${v}" class="${clip.layout === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
            <div class="grid grid-2">
              <label class="field"><span>Recortar inicio (s)</span><input type="number" id="ts" min="0" step="0.1" value="${clip.trim_start}"></label>
              <label class="field"><span>Recortar final (s)</span><input type="number" id="te" min="0" step="0.1" value="${clip.trim_end}"></label>
            </div>
            <p class="hint">Duración del corte original: ${clip.source_duration} s (incluye contexto antes y después).</p>
          </div>

          <div class="card"><div class="card-title"><h3>Subtítulos</h3>
            <label class="row gap-s small"><input type="checkbox" id="sub-on" ${clip.subtitles_enabled ? "checked" : ""}> Mostrar</label></div>
            <div class="seg mb" id="style">${[["highlight", "Palabra resaltada"], ["simple", "Simple"], ["box", "Con caja"]].map(([v, l]) => `<button data-v="${v}" class="${clip.subtitle_style === v ? "on" : ""}">${l}</button>`).join("")}</div>
            <p class="hint mb">Tiempos en segundos desde el inicio del corte original. Edita el texto con total libertad.</p>
            <div id="cues"></div>
            <button class="btn sm mt" id="add-cue">${ICONS.plus}Añadir línea</button>
          </div>

          <div class="card"><div class="row between gap wrap">
            <button class="btn danger sm" id="delete">${ICONS.trash}Eliminar</button>
            <div class="row gap">${clip.status !== "discarded" ? `<button class="btn sm ghost" id="discard">Descartar</button>` : ""}
              <button class="btn primary" id="render" ${!clip.source_available || busy ? "disabled" : ""}>Guardar y volver a renderizar</button></div></div>
            ${!clip.source_available ? `<p class="hint">El corte original ya no está disponible: solo puedes editar el texto.</p>` : ""}
          </div>

          <details class="card"><summary class="small muted">Transcripción completa</summary><p class="small mt">${esc(clip.transcript)}</p></details>
        </div>
      </div>`;
    drawCues();
    bind();
  };

  const drawCues = () => {
    const box = root.querySelector("#cues");
    box.innerHTML = cues.length ? cues.map((c, i) => `<div class="cue" data-i="${i}">
      <input class="t" type="number" step="0.1" data-f="start" value="${c.start.toFixed(2)}" aria-label="Inicio">
      <input class="t" type="number" step="0.1" data-f="end" value="${c.end.toFixed(2)}" aria-label="Fin">
      <input class="txt" type="text" data-f="text" value="${esc(c.text)}" aria-label="Texto">
      <button class="btn ghost" data-del="${i}" aria-label="Quitar">${ICONS.trash}</button></div>`).join("")
      : `<p class="muted small">Sin subtítulos (no se detectó voz).</p>`;
    box.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", () => {
      const i = Number(inp.closest(".cue").dataset.i), f = inp.dataset.f;
      cues[i][f] = f === "text" ? inp.value : Number(inp.value);
      dirty = true;
    }));
    box.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { cues.splice(Number(b.dataset.del), 1); dirty = true; drawCues(); });
  };

  const textBody = () => ({
    title: root.querySelector("#title").value.trim(),
    description: root.querySelector("#desc").value.trim(),
    hashtags: root.querySelector("#tags").value.split(/[\s,]+/).filter(Boolean),
  });

  const bind = () => {
    let layout = clip.layout, style = clip.subtitle_style;
    root.querySelectorAll("#layout button").forEach((b) => b.onclick = () => {
      layout = b.dataset.v; root.querySelectorAll("#layout button").forEach((x) => x.classList.toggle("on", x === b));
    });
    root.querySelectorAll("#style button").forEach((b) => b.onclick = () => {
      style = b.dataset.v; root.querySelectorAll("#style button").forEach((x) => x.classList.toggle("on", x === b));
    });
    root.querySelector("#save-text").onclick = async () => {
      try { clip = await api(`/api/clips/${id}`, { method: "PATCH", body: textBody() }); toast("Texto guardado", "ok"); }
      catch (err) { toast(err.message, "error"); }
    };
    root.querySelector("#add-cue").onclick = () => {
      const last = cues[cues.length - 1];
      const start = last ? last.end : 0;
      cues.push({ start, end: start + 1.5, text: "", words: [] }); dirty = true; drawCues();
      root.querySelector(`.cue[data-i="${cues.length - 1}"] .txt`)?.focus();
    };
    root.querySelector("#render").onclick = async () => {
      const btn = root.querySelector("#render"); btn.disabled = true;
      try {
        await api(`/api/clips/${id}`, { method: "PATCH", body: {
          ...textBody(), layout, subtitle_style: style, subtitles_enabled: root.querySelector("#sub-on").checked,
          trim_start: Number(root.querySelector("#ts").value) || 0, trim_end: Number(root.querySelector("#te").value) || 0,
          subtitles: cues.map((c) => ({ start: c.start, end: c.end, text: c.text, words: c.words || [] })),
        } });
        await api(`/api/clips/${id}/render`, { method: "POST" });
        dirty = false;
        toast("Renderizando de nuevo…", "ok");
        clip = await api(`/api/clips/${id}`); cues = structuredClone(clip.subtitles); draw();
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    root.querySelector("#discard")?.addEventListener("click", async () => {
      await api(`/api/clips/${id}/discard`, { method: "POST" }); clip = await api(`/api/clips/${id}`); draw();
    });
    root.querySelector("#delete").onclick = async () => {
      if (!(await confirmDialog("Eliminar clip", "Se borrará el vídeo y su historial.", "Eliminar", true))) return;
      await api(`/api/clips/${id}`, { method: "DELETE" }); location.hash = "#/clips";
    };
    root.querySelector("#publish").onclick = () => openPublish(clip, async () => { clip = await api(`/api/clips/${id}`); draw(); });

    const player = root.querySelector("#player");
    if (player) player.ontimeupdate = () => {
      const t = player.currentTime + (clip.trim_start || 0);
      root.querySelectorAll(".cue").forEach((el) => {
        const c = cues[Number(el.dataset.i)];
        el.classList.toggle("active", c && t >= c.start && t <= c.end);
      });
    };
  };

  draw();
  ctx.onCleanup(on("clip", async (c) => {
    if (c.id !== id || dirty) return;
    clip = await api(`/api/clips/${id}`); cues = structuredClone(clip.subtitles);
    if (ctx.isCurrent()) draw();
  }));
  ctx.onCleanup(on("publication", async (p) => {
    if (p.clip_id !== id || dirty) return;
    clip = await api(`/api/clips/${id}`); if (ctx.isCurrent()) draw();
  }));
}

// ---------------------------------------------------------------- publicar
// Sigue las pautas de UX de TikTok para la Content Posting API: se muestra la cuenta
// de destino, la privacidad no tiene valor por defecto, se respetan los ajustes de
// interacción del creador y se pide consentimiento explícito.
export async function openPublish(clip, done) {
  let status, captionRes;
  try {
    [status, captionRes] = await Promise.all([api("/api/tiktok/status"), api(`/api/clips/${clip.id}/caption`)]);
  } catch (err) { toast(err.message, "error"); return; }
  if (!status.account) {
    modal(`<h3>Conecta TikTok</h3><p class="muted mb">Para publicar necesitas autorizar tu cuenta de TikTok.</p>
      <div class="row end gap"><button class="btn ghost" data-close>Cerrar</button><a class="btn primary" href="#/publicaciones" data-close>Ir a Publicar</a></div>`);
    return;
  }
  let creator = null, creatorErr = "";
  try { creator = await api("/api/tiktok/creator-info"); } catch (err) { creatorErr = err.message; }
  const privacyLabels = { PUBLIC_TO_EVERYONE: "Público", MUTUAL_FOLLOW_FRIENDS: "Amigos", FOLLOWER_OF_CREATOR: "Seguidores", SELF_ONLY: "Solo yo" };
  const opts = creator?.privacy_level_options || [];
  const tooLong = creator?.max_video_post_duration_sec && clip.duration > creator.max_video_post_duration_sec;

  modal(`
    <h3>Publicar en TikTok</h3>
    <div class="row gap mb">${status.account.avatar_url ? `<img src="${esc(status.account.avatar_url)}" style="width:36px;height:36px;border-radius:50%">` : ""}
      <div><b>${esc(creator?.creator_nickname || status.account.display_name || "Tu cuenta")}</b>
      <p class="tiny faint">${creator?.creator_username ? "@" + esc(creator.creator_username) : ""}</p></div></div>
    ${creatorErr ? `<div class="notice warn mb">No se pudo consultar la cuenta: ${esc(creatorErr)}. La publicación directa no está disponible; puedes enviarlo a tu bandeja.</div>` : ""}
    ${tooLong ? `<div class="notice bad mb">El clip dura ${Math.round(clip.duration)} s y tu cuenta permite ${creator.max_video_post_duration_sec} s.</div>` : ""}
    <div class="seg mb" id="mode"><button data-v="inbox" class="on">Enviar a mi bandeja (borrador)</button><button data-v="direct" ${!creator ? "disabled" : ""}>Publicar directamente</button></div>
    <label class="field"><span>Texto de la publicación</span><textarea id="cap" maxlength="2200" style="min-height:120px">${esc(captionRes.caption)}</textarea></label>
    <div id="direct-opts" class="hidden">
      <label class="field"><span>¿Quién puede verlo?</span><select id="priv"><option value="" selected disabled>Elige una opción</option>
        ${opts.map((o) => `<option value="${o}">${privacyLabels[o] || o}</option>`).join("")}</select>
        <p class="hint">Las apps aún no auditadas por TikTok solo pueden publicar en "Solo yo".</p></label>
      <div class="switch"><span>Permitir comentarios</span><input type="checkbox" id="cmt" ${creator?.comment_disabled ? "disabled" : "checked"}></div>
      <div class="switch"><span>Permitir dúos</span><input type="checkbox" id="duet" ${creator?.duet_disabled ? "disabled" : "checked"}></div>
      <div class="switch"><span>Permitir stitch</span><input type="checkbox" id="stitch" ${creator?.stitch_disabled ? "disabled" : "checked"}></div>
      <div class="switch"><div><span>Contenido comercial</span><p class="tiny faint">Actívalo si promocionas una marca, producto o servicio.</p></div><input type="checkbox" id="brand"></div>
      <div id="brand-opts" class="hidden">
        <div class="switch"><span>Tu propia marca</span><input type="checkbox" id="brand-own"></div>
        <div class="switch"><span>Contenido de marca (tercero)</span><input type="checkbox" id="brand-paid"></div>
      </div>
    </div>
    <label class="row gap-s small mt" style="align-items:flex-start"><input type="checkbox" id="consent" style="margin-top:3px">
      <span>Confirmo que quiero publicar este clip, que tengo permiso del creador original y acepto la
      <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener">confirmación de uso de música</a> de TikTok.</span></label>
    <p class="tiny faint mt">Tras publicar, TikTok puede tardar unos minutos en procesar el vídeo.</p>
    <div class="row end gap mt"><button class="btn ghost" data-close>Cancelar</button><button class="btn primary" id="go" disabled>${ICONS.send}Publicar</button></div>`, {
    onMount(m, close) {
      let mode = "inbox";
      const go = m.querySelector("#go");
      const validate = () => {
        const ok = m.querySelector("#consent").checked && !tooLong && (mode === "inbox" || m.querySelector("#priv").value);
        const brand = m.querySelector("#brand").checked;
        const brandOk = !brand || m.querySelector("#brand-own").checked || m.querySelector("#brand-paid").checked;
        go.disabled = !(ok && brandOk);
      };
      m.querySelectorAll("#mode button").forEach((b) => b.onclick = () => {
        mode = b.dataset.v;
        m.querySelectorAll("#mode button").forEach((x) => x.classList.toggle("on", x === b));
        m.querySelector("#direct-opts").classList.toggle("hidden", mode !== "direct");
        validate();
      });
      m.querySelector("#brand").onchange = (e) => { m.querySelector("#brand-opts").classList.toggle("hidden", !e.target.checked); validate(); };
      m.querySelectorAll("input,select").forEach((el) => el.addEventListener("change", validate));
      go.onclick = async () => {
        go.disabled = true;
        const brand = m.querySelector("#brand").checked;
        try {
          await api(`/api/clips/${clip.id}/publish`, { method: "POST", body: {
            mode, caption: m.querySelector("#cap").value, privacy_level: mode === "direct" ? m.querySelector("#priv").value : null,
            disable_comment: !m.querySelector("#cmt").checked, disable_duet: !m.querySelector("#duet").checked,
            disable_stitch: !m.querySelector("#stitch").checked,
            brand_organic_toggle: brand && m.querySelector("#brand-own").checked,
            brand_content_toggle: brand && m.querySelector("#brand-paid").checked, consent: true,
          } });
          close();
          toast(mode === "inbox" ? "Enviando a tu bandeja de TikTok…" : "Publicando en TikTok…", "ok");
          done?.();
        } catch (err) { toast(err.message, "error"); go.disabled = false; }
      };
    },
  });
}

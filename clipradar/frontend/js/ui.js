// Utilidades de interfaz
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function toast(message, kind = "info") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3800);
  setTimeout(() => el.remove(), 4300);
}

export function modal(html, { onMount } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add("open"));
  const close = () => { wrap.classList.remove("open"); setTimeout(() => wrap.remove(), 200); };
  wrap.addEventListener("click", (e) => { if (e.target === wrap || e.target.closest("[data-close]")) close(); });
  onMount?.(wrap.querySelector(".modal"), close);
  return close;
}

export function confirmDialog(title, text, okLabel = "Confirmar", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    modal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>
      <div class="row end gap"><button class="btn ghost" data-close>Cancelar</button>
      <button class="btn ${danger ? "danger" : "primary"}" id="cd-ok">${esc(okLabel)}</button></div>`, {
      onMount(m, close) {
        m.querySelector("#cd-ok").onclick = () => { done = true; close(); resolve(true); };
        m.parentElement.addEventListener("click", () => setTimeout(() => { if (!done) resolve(false); }, 0));
      },
    });
  });
}

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m}:${String(s).padStart(2, "0")} min`;
  return `${s} s`;
}

export function fmtClock(sec) {
  sec = Math.max(0, sec || 0);
  const m = Math.floor(sec / 60), s = (sec % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

export function fmtBytes(b) {
  if (!b) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i > 1 ? 1 : 0)} ${u[i]}`;
}

export function timeAgo(iso) {
  if (!iso) return "—";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 10) return "ahora";
  if (d < 60) return `hace ${Math.floor(d)} s`;
  if (d < 3600) return `hace ${Math.floor(d / 60)} min`;
  if (d < 86400) return `hace ${Math.floor(d / 3600)} h`;
  return new Date(iso).toLocaleDateString("es", { day: "numeric", month: "short" });
}

export function clockTime(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const STREAM_STATUS = {
  idle: ["Inactiva", "muted"], starting: ["Conectando", "warn"], live: ["En vivo", "live"],
  reconnecting: ["Reconectando", "warn"], ended: ["Terminada", "muted"], error: ["Error", "bad"],
  stopped: ["Detenida", "muted"],
};
export const CLIP_STATUS = {
  detected: ["Detectado", "warn"], rendering: ["Procesando", "warn"], ready: ["Listo", "ok"],
  discarded: ["Descartado", "muted"], failed: ["Falló", "bad"],
};
export const PUB_STATUS = {
  queued: ["En cola", "warn"], uploading: ["Subiendo", "warn"], processing: ["TikTok procesando", "warn"],
  published: ["Publicado", "ok"], sent_to_inbox: ["En tu bandeja de TikTok", "ok"], failed: ["Falló", "bad"],
};

export function pill([label, kind]) { return `<span class="pill pill-${kind}">${esc(label)}</span>`; }

export function scoreBadge(score) {
  const k = score >= 85 ? "hot" : score >= 70 ? "good" : score >= 50 ? "mid" : "low";
  return `<span class="score score-${k}" title="Potencial viral">${score}</span>`;
}

export function empty(icon, title, text, action = "") {
  return `<div class="empty"><div class="empty-icon">${icon}</div><h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>${action}</div>`;
}

export const ICONS = {
  dash: '<svg viewBox="0 0 24 24"><path d="M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z"/></svg>',
  radar: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 10 10h-2a8 8 0 1 1-8-8zm0 4a6 6 0 1 0 6 6h-2a4 4 0 1 1-4-4zm0 4a2 2 0 1 0 2 2l7.07-7.07-1.41-1.41L12.6 10.1A2 2 0 0 0 12 10z"/></svg>',
  clips: '<svg viewBox="0 0 24 24"><path d="M4 6H2v14a2 2 0 0 0 2 2h14v-2H4zm16-4H8a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zm-8 12.5v-9l6 4.5z"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M2 21 23 12 2 3v7l15 2-15 2z"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><path d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.4 7.4 0 0 0-1.7-1L15 3h-4l-.4 2.7a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7.4 7.4 0 0 0 1.7 1L11 21h4l.4-2.7a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.5zM13 15.5A3.5 3.5 0 1 1 16.5 12 3.5 3.5 0 0 1 13 15.5z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="m12 17.3 6.2 3.7-1.6-7 5.4-4.7-7.2-.6L12 2 9.2 8.7 2 9.3l5.4 4.7-1.6 7z"/></svg>',
};

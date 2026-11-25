// Cliente de la API + eventos en tiempo real (SSE)
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const res = await fetch(path, {
    method,
    signal,
    credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && (data.detail?.[0]?.msg || data.detail)) || `Error ${res.status}`;
    if (res.status === 401 && path !== "/api/auth/login") window.dispatchEvent(new Event("cr:unauthorized"));
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

// Bus de eventos: las vistas se suscriben con on(type, fn) y se limpian al cambiar de vista.
const listeners = new Map();
export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type)?.delete(fn);
}
function emit(type, data) {
  listeners.get(type)?.forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });
  listeners.get("*")?.forEach((fn) => { try { fn({ type, data }); } catch (e) { console.error(e); } });
}

let es = null;
export function connectEvents() {
  if (es) es.close();
  es = new EventSource("/api/events");
  es.onopen = () => emit("connection", { online: true });
  es.onerror = () => emit("connection", { online: false });
  es.onmessage = (ev) => {
    try { const msg = JSON.parse(ev.data); emit(msg.type, msg.data); } catch { /* ignorar */ }
  };
}
export function disconnectEvents() { es?.close(); es = null; }

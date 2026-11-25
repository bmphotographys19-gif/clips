import { api } from "../api.js";
import { esc } from "../ui.js";

export default function login(root, me) {
  root.innerHTML = `
    <div class="login"><form class="card" id="f">
      <img class="login-logo" src="/icons/icon-192.png" alt="">
      <h1 style="text-align:center">ClipRadar AI</h1>
      <p class="muted" style="text-align:center;margin:6px 0 22px">Panel de control</p>
      ${me.password_configured === false ? `<div class="notice warn mb">El servidor no tiene ADMIN_PASSWORD configurada. Defínela en el archivo .env y reinicia.</div>` : ""}
      <label class="field"><span>Contraseña</span><input type="password" id="pw" autocomplete="current-password" required autofocus></label>
      <p class="small" id="err" style="color:var(--bad);min-height:20px"></p>
      <button class="btn primary block" id="go">Entrar</button>
    </form></div>`;
  root.querySelector("#f").onsubmit = async (e) => {
    e.preventDefault();
    const btn = root.querySelector("#go");
    btn.disabled = true;
    try {
      await api("/api/auth/login", { method: "POST", body: { password: root.querySelector("#pw").value } });
      location.reload();
    } catch (err) {
      root.querySelector("#err").innerHTML = esc(err.message);
      btn.disabled = false;
    }
  };
}

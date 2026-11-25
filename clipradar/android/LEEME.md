# ClipRadar AI en tu celular Android (sin servidor)

Con esta opción, todo funciona **dentro de tu celular**: no pagas servidor ni dominio. Solo pagas la IA de
Claude por las horas de directo que analices.

**Condiciones para que funcione 24/7:**
- El celular debe estar **encendido, enchufado al cargador y con internet** (mejor Wi-Fi).
- Recomendado: Android 10 o superior, 6 GB de RAM o más y 3 GB libres.
- El celular se calentará algo mientras analiza: no lo tapes ni lo dejes al sol.

---

## Paso 1 — Instalar Termux (5 min)
1. Instala **F-Droid** desde <https://f-droid.org> (descarga el APK y permite "instalar apps desconocidas").
2. En F-Droid, busca e instala **Termux**.
   ⚠️ **No uses la versión de Google Play**: está desactualizada y falla.

## Paso 2 — Que Android no lo cierre (2 min)
1. Ajustes → Aplicaciones → **Termux** → Batería → **Sin restricciones** (o "No optimizar").
2. **Android 14 o superior:** Ajustes → Opciones de desarrollador → activa
   **"Desactivar restricciones de procesos secundarios"** (para activar las Opciones de desarrollador:
   Ajustes → Acerca del teléfono → toca 7 veces "Número de compilación").
3. **Android 12 y 13:** si notas que ClipRadar se detiene solo, avísame y te explico cómo quitar el límite
   (hace falta conectarlo una vez a un ordenador).

## Paso 3 — Instalar ClipRadar (30-60 min, solo la primera vez)
Abre Termux, copia esta línea, pégala (mantén pulsado → Pegar) y pulsa Enter:

```
pkg install -y git && { [ -d ~/clips ] || git clone https://github.com/bmphotographys19-gif/clips ~/clips; } && bash ~/clips/clipradar/android/instalar.sh
```

El instalador te preguntará:
- **Modelo de transcripción:** elige `1` (base) salvo que tu celular sea muy potente.
- **Contraseña del panel:** invéntala y apúntala.
- **Clave de API de Claude:** créala en <https://console.anthropic.com> → API Keys (carga un saldo pequeño
  de prepago; cuando se acaba, se detiene sin cobrar más). Puedes dejarla vacía y ponerla después.
- **Modelo de IA:** Haiku (más barato), Sonnet o Opus (mejor detección, más caro).
- **TikTok:** tu *Client key* y *Client secret* (o déjalo vacío para más tarde).

Deja la pantalla encendida durante la instalación. Si se corta, vuelve a pegar la misma línea: continúa
donde se quedó.

## Paso 4 — Arrancar
En Termux:
```
bash ~/clips/clipradar/android/iniciar.sh
```
Se abrirá Chrome con el panel (`http://localhost:8000`). Entra con tu contraseña y, en el menú ⋮ de Chrome,
pulsa **"Instalar aplicación"** para tenerla como app.

Verás una notificación de Termux con **"wake lock held"**: significa que está trabajando. Puedes salir de
Termux y usar el celular con normalidad; **no cierres esa notificación**.

Para detenerlo:
```
bash ~/clips/clipradar/android/detener.sh
```

## Paso 5 — Usarlo
1. **Monitor → Añadir** → pega el enlace del directo y escribe la autorización del creador.
2. La transcripción aparece en el Monitor a los ~2 minutos; los clips, en **Clips**.
3. **Publicar → Conectar con TikTok** para enlazar tu cuenta.

## TikTok
El instalador usa como dirección de redirección la página
`https://bmphotographys19-gif.github.io/clips/callback.html`. Debe estar registrada en tu app de
developers.tiktok.com (Login Kit → Redirect URI). Esa página devuelve la autorización a ClipRadar en tu
propio celular.

## Actualizar
```
bash ~/clips/clipradar/android/detener.sh
cd ~/clips && git pull
bash ~/clips/clipradar/android/iniciar.sh
```

## Si algo falla
- Mira el estado en **Ajustes → Estado del sistema** dentro del panel.
- Los registros están en `~/clips/clipradar/backend/data/logs/` (`api.log`, `monitor.log`, `worker.log`).
  Cópiame las últimas líneas y lo resolvemos.

## Diferencias con la versión de servidor
- Transcripción con whisper.cpp (modelo `base`): algo menos precisa que en un servidor.
- Vídeo capturado a 720p (el clip final sigue siendo 1080×1920) y render más rápido y ligero.
- El panel solo se abre desde el propio celular (más seguro: nadie más puede entrar).
- Si el celular se apaga o se queda sin internet, el monitoreo se pausa y continúa solo al volver.

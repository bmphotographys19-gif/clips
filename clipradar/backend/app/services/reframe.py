"""Encuadre inteligente 16:9 → 9:16.

Detecta caras con OpenCV, estima quién habla (movimiento en la zona de la boca),
y genera una trayectoria de cámara suave: la cámara solo se mueve cuando el
sujeto sale de una zona muerta, con transiciones suavizadas. El resultado es
una expresión de FFmpeg para el filtro ``crop`` (interpolación lineal por tramos).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

try:  # OpenCV es opcional (p. ej. en Android puede no estar instalado)
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - depende del equipo
    cv2 = None
    np = None

log = logging.getLogger("clipradar.reframe")

SAMPLE_FPS = 3.0
DETECT_WIDTH = 640


@dataclass
class Plan:
    mode: str  # track | blur | vertical
    src_w: int
    src_h: int
    crop_w: int
    keyframes: list[tuple[float, float]] = field(default_factory=list)  # (t, x_px)
    face_ratio: float = 0.0

    def x_expr(self) -> str:
        """Expresión de FFmpeg para la x del recorte en función de t."""
        kf = self.keyframes
        if not kf:
            return str(int((self.src_w - self.crop_w) / 2))
        if len(kf) == 1:
            return str(int(kf[0][1]))
        expr = f"{kf[-1][1]:.1f}"
        for (t0, x0), (t1, x1) in reversed(list(zip(kf[:-1], kf[1:]))):
            if abs(x1 - x0) < 0.5:
                seg = f"{x0:.1f}"
            else:
                seg = f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
            expr = f"if(lt(t\\,{t1:.3f})\\,{seg}\\,{expr})"
        return f"if(lt(t\\,{kf[0][0]:.3f})\\,{kf[0][1]:.1f}\\,{expr})"


def _cascades():
    base = Path(cv2.data.haarcascades)
    front = cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml"))
    profile = cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml"))
    return front, profile


def _faces(gray: np.ndarray, front, profile) -> list[tuple[int, int, int, int]]:
    h = gray.shape[0]
    min_size = (max(24, h // 14), max(24, h // 14))
    found = list(front.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=min_size))
    if not found:
        found = list(profile.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=min_size))
        if not found:  # perfil mirando al otro lado
            flipped = cv2.flip(gray, 1)
            w = gray.shape[1]
            found = [(w - x - fw, y, fw, fh) for (x, y, fw, fh) in
                     profile.detectMultiScale(flipped, scaleFactor=1.15, minNeighbors=6, minSize=min_size)]
    return [tuple(int(v) for v in f) for f in found]


def analyze(video: Path, forced_mode: str = "auto") -> Plan:
    if cv2 is None:
        return _plan_without_opencv(video)
    cap = cv2.VideoCapture(str(video))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_w <= 0 or src_h <= 0:
        cap.release()
        raise RuntimeError("No se pudo leer el vídeo para el encuadre")
    crop_w = int(round(src_h * 9 / 16 / 2) * 2)
    if src_w / src_h <= 9 / 16 + 0.01:
        cap.release()
        return Plan("vertical", src_w, src_h, src_w)
    if forced_mode == "blur":
        cap.release()
        return Plan("blur", src_w, src_h, crop_w)

    front, profile = _cascades()
    scale = DETECT_WIDTH / src_w
    step = max(1, int(round(fps / SAMPLE_FPS)))
    samples: list[tuple[float, float | None]] = []  # (t, centro x en px de origen)
    prev_gray = None
    current: float | None = None
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = cv2.resize(frame, (DETECT_WIDTH, int(src_h * scale)))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = _faces(gray, front, profile)
            t = idx / fps
            target = None
            if faces:
                best, best_score = None, -1.0
                for (x, y, w, h) in faces:
                    area = (w * h) / (gray.shape[0] * gray.shape[1])
                    motion = 0.0
                    if prev_gray is not None:
                        y0, y1 = y + int(h * 0.55), min(gray.shape[0], y + h)
                        a = gray[y0:y1, x:x + w].astype(np.float32)
                        b = prev_gray[y0:y1, x:x + w].astype(np.float32)
                        if a.size and a.shape == b.shape:
                            motion = float(np.mean(np.abs(a - b))) / 255.0
                    cx = (x + w / 2) / scale
                    stickiness = 0.15 if current is not None and abs(cx - current) < crop_w * 0.25 else 0.0
                    score = area * 4 + motion * 3 + stickiness
                    if score > best_score:
                        best, best_score = cx, score
                target = best
                current = target
            samples.append((t, target))
            prev_gray = gray
        idx += 1
    cap.release()

    if not samples:
        return Plan("blur", src_w, src_h, crop_w)
    with_face = [s for s in samples if s[1] is not None]
    ratio = len(with_face) / len(samples)
    if forced_mode != "track" and ratio < 0.4:
        return Plan("blur", src_w, src_h, crop_w, face_ratio=ratio)
    if not with_face:
        return Plan("track", src_w, src_h, crop_w, [(0.0, (src_w - crop_w) / 2)], ratio)

    # Rellenar huecos sin cara con la última posición conocida (o la siguiente).
    filled: list[tuple[float, float]] = []
    last = with_face[0][1]
    for t, cx in samples:
        last = cx if cx is not None else last
        filled.append((t, last))
    # Mediana móvil para eliminar saltos falsos.
    xs = np.array([c for _, c in filled])
    k = 5
    med = np.array([np.median(xs[max(0, i - k // 2): i + k // 2 + 1]) for i in range(len(xs))])

    # Cámara con zona muerta: solo se mueve si el sujeto se aleja >12 % del ancho.
    dead = src_w * 0.12
    max_x = src_w - crop_w
    def clamp(cx: float) -> float:
        return float(min(max(cx - crop_w / 2, 0), max_x))

    keyframes: list[tuple[float, float]] = [(0.0, clamp(med[0]))]
    cam_center = med[0]
    move_time = 0.6  # segundos que dura un desplazamiento
    for (t, _), cx in zip(filled, med):
        if abs(cx - cam_center) > dead:
            last_t, last_x = keyframes[-1]
            hold_t = max(t, last_t + 0.05)
            if hold_t > last_t + 0.05:
                keyframes.append((hold_t, last_x))
            keyframes.append((hold_t + move_time, clamp(cx)))
            cam_center = cx
    # Limitar el número de tramos (expresión manejable).
    if len(keyframes) > 80:
        stride = len(keyframes) / 80
        keyframes = [keyframes[int(i * stride)] for i in range(80)]
    return Plan("track", src_w, src_h, crop_w, keyframes, ratio)


def _plan_without_opencv(video: Path) -> Plan:
    """Sin OpenCV no hay detección de caras: vídeo completo sobre fondo desenfocado."""
    from .media import video_info

    info = video_info(video)
    w, h = info["width"], info["height"]
    if w / max(h, 1) <= 9 / 16 + 0.01:
        return Plan("vertical", w, h, w)
    log.warning("OpenCV no está instalado: se usa el encuadre completo con fondo desenfocado")
    return Plan("blur", w, h, int(round(h * 9 / 16 / 2) * 2))

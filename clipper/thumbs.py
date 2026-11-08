"""Miniatures : extraction d'images, choix de la plus nette avec un visage, texte posé avec Pillow."""
from __future__ import annotations

import io
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .log import log

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def extract_frames(src: Path, start: float, end: float, n: int, out_dir: Path, w: int, h: int) -> list[Path]:
    """Extrait `n` images réparties sur le clip, recadrées en 16:9 à w×h."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        t = start + (end - start) * (i + 0.5) / n
        p = out_dir / f"frame_{i}.jpg"
        if not p.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                 "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                 "-q:v", "2", str(p)],
                check=True,
            )
        if p.exists():
            paths.append(p)
    return paths


@lru_cache(maxsize=1)
def _face_detector():
    import cv2
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def score_frame(path: Path) -> tuple[bool, float]:
    """(visage détecté, netteté). La netteté (variance du Laplacien) est mesurée sur le visage s'il y en a un."""
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return False, 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = _face_detector().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                              minSize=(gray.shape[0] // 10, gray.shape[0] // 10))
    if len(faces):
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        region = gray[y:y + h, x:x + w]
        return True, float(cv2.Laplacian(region, cv2.CV_64F).var())
    return False, float(cv2.Laplacian(gray, cv2.CV_64F).var())


def best_frame(paths: list[Path]) -> int:
    """Indice de la plus nette parmi les images avec visage (ou parmi toutes s'il n'y en a aucune)."""
    scores = [score_frame(p) for p in paths]
    with_face = [i for i, (face, _) in enumerate(scores) if face]
    pool = with_face or list(range(len(paths)))
    return max(pool, key=lambda i: scores[i][1])


def _font(cfg_t: dict, size: int):
    path = cfg_t.get("font_path")
    for candidate in ([path] if path else []) + FONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    log.warning("Aucune police trouvée : renseigne thumbnail.font_path dans config.yaml.")
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, stroke: int, max_w: int) -> list[str]:
    """Une ligne si possible, sinon deux lignes équilibrées."""
    words = text.split()
    if draw.textlength(text, font=font) + 2 * stroke <= max_w or len(words) < 2:
        return [text]
    best = min(range(1, len(words)), key=lambda k: abs(
        draw.textlength(" ".join(words[:k]), font=font) - draw.textlength(" ".join(words[k:]), font=font)))
    return [" ".join(words[:best]), " ".join(words[best:])]


def compose(frame: Path, text: str, cfg_t: dict, out: Path) -> Path:
    """Pose le texte sur l'image et enregistre un JPEG 1280×720 de moins de 2 Mo."""
    W, H, margin = cfg_t["width"], cfg_t["height"], cfg_t["margin"]
    img = Image.open(frame).convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    text = (text or "").strip()
    if cfg_t.get("uppercase"):
        text = text.upper()

    if text:
        draw = ImageDraw.Draw(img)
        stroke = cfg_t["stroke_width"]
        size = cfg_t["font_size"]
        # Réduit la police jusqu'à ce que le texte tienne sur 2 lignes dans la largeur.
        while True:
            font = _font(cfg_t, size)
            lines = _wrap(draw, text, font, stroke, W - 2 * margin)
            if all(draw.textlength(l, font=font) + 2 * stroke <= W - 2 * margin for l in lines) or size <= 30:
                break
            size -= 6
        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        block_h = line_h * len(lines) + int(size * 0.15) * (len(lines) - 1)
        pos = cfg_t["position"]
        y0 = margin if pos == "top" else (H - block_h) // 2 if pos == "center" else H - margin - block_h

        if cfg_t.get("band_color"):
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            pad = int(size * 0.3)
            ImageDraw.Draw(overlay).rectangle([0, y0 - pad, W, y0 + block_h + pad],
                                              fill=ImageColor.getrgb(cfg_t["band_color"]))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)

        y = y0
        for line in lines:
            w = draw.textlength(line, font=font)
            draw.text(((W - w) / 2, y), line, font=font, fill=cfg_t["text_color"],
                      stroke_width=stroke, stroke_fill=cfg_t["stroke_color"])
            y += line_h + int(size * 0.15)

    # Qualité dégressive jusqu'à passer sous la limite de poids.
    out.parent.mkdir(parents=True, exist_ok=True)
    for quality in (92, 85, 78, 70, 60, 50):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= cfg_t["max_bytes"]:
            break
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(out)
    return out


def make_thumbnail(src: Path, clip: dict, clip_dir: Path, cfg, frame: int | None = None) -> tuple[Path, int]:
    """Extrait les images (si besoin), choisit la meilleure (sauf `frame` imposé), compose la miniature."""
    t = cfg["thumbnail"]
    frames = extract_frames(src, clip["start"], clip["end"], t["frames"], clip_dir / "frames", t["width"], t["height"])
    if not frames:
        raise RuntimeError("Aucune image extraite pour la miniature.")
    idx = best_frame(frames) if frame is None else max(0, min(frame, len(frames) - 1))
    out = compose(frames[idx], clip.get("thumb_text") or "", t, clip_dir / "thumb.jpg")
    return out, idx

"""Rendu ffmpeg réel (format, durée, loudness) et miniatures (taille, poids, choix de l'image)."""
import json
import re
import subprocess

import pytest
from PIL import Image, ImageFilter

from clipper.config import DEFAULTS
from clipper.render import render_clip
from clipper.thumbs import best_frame, compose


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def integrated_loudness(path):
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


@pytest.fixture(scope="module")
def quiet_source(tmp_path_factory):
    """Source 640×360 (4:3 recadré ailleurs) avec un son très faible (~ −35 LUFS)."""
    p = tmp_path_factory.mktemp("r") / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=44100,volume=0.03", "-t", "12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(p)], check=True)
    return p


def test_render_format_duration_loudness(quiet_source, tmp_path):
    r = dict(DEFAULTS["render"], preset="ultrafast")
    out = render_clip(quiet_source, 2.0, 10.0, tmp_path / "clip.mp4", r)
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (1920, 1080) and v["pix_fmt"] == "yuv420p"
    assert a["sample_rate"] == "48000"
    assert float(info["format"]["duration"]) == pytest.approx(8.0, abs=0.1)
    assert integrated_loudness(out) == pytest.approx(-14, abs=1.0)
    assert not (tmp_path / "clip.tmp.mp4").exists()


def _noise(path, blur=0):
    img = Image.effect_noise((1280, 720), 80).convert("RGB")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    img.save(path, quality=95)
    return path


def test_best_frame_prefers_sharpest_without_face(tmp_path):
    frames = [_noise(tmp_path / "a.jpg", 4), _noise(tmp_path / "b.jpg", 0), _noise(tmp_path / "c.jpg", 2)]
    assert best_frame(frames) == 1


def test_compose_size_weight_and_text(tmp_path):
    frame = _noise(tmp_path / "f.jpg")
    t = dict(DEFAULTS["thumbnail"])
    out = compose(frame, "Une phrase un peu longue pour tester le retour à la ligne", t, tmp_path / "thumb.jpg")
    img = Image.open(out)
    assert img.size == (1280, 720) and out.stat().st_size <= t["max_bytes"]
    # le texte (blanc) est bien posé en bas sur le bandeau
    bottom = img.crop((0, 500, 1280, 720)).convert("L")
    assert bottom.getextrema()[1] >= 240


def test_compose_respects_tiny_weight_limit(tmp_path):
    frame = _noise(tmp_path / "f.jpg")
    t = dict(DEFAULTS["thumbnail"], max_bytes=400_000)
    out = compose(frame, "Test", t, tmp_path / "thumb.jpg")
    assert out.stat().st_size <= 400_000

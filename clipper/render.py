"""Découpe finale : ffmpeg précis à la frame, 1080p 16:9, fondu audio, loudness −14 LUFS (2 passes)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import db
from .log import log


def _loudnorm_filter(r: dict, extra: str = "") -> str:
    return f"loudnorm=I={r['loudness_lufs']}:TP={r['true_peak_db']}:LRA=11{extra}"


def measure_loudness(src: Path, start: float, duration: float, r: dict) -> dict:
    """1re passe : mesure la loudness du passage (sortie JSON de loudnorm sur stderr)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
         "-vn", "-af", _loudnorm_filter(r, ":print_format=json"), "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        raise RuntimeError("Mesure de loudness impossible (sortie loudnorm introuvable).")
    return json.loads(match.group(0))


def render_clip(src: Path, start: float, end: float, out: Path, r: dict) -> Path:
    """Rend le clip [start, end] de `src` vers `out` (MP4 H.264/AAC)."""
    duration = end - start
    m = measure_loudness(src, start, duration, r)
    # Passe linéaire : gain constant calculé sur la mesure → pas de pompage.
    norm = _loudnorm_filter(r, (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                                f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                                f":offset={m['target_offset']}:linear=true"))
    fade = r["fade_s"]
    afilter = (f"{norm},aresample=48000,afade=t=in:st=0:d={fade},"
               f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade}")
    W, H = r["width"], r["height"]
    vfilter = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    # -ss avant -i + ré-encodage : recherche rapide ET précise à la frame près.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
         "-map", "0:v:0", "-map", "0:a:0", "-vf", vfilter, "-af", afilter,
         "-c:v", "libx264", "-preset", r["preset"], "-crf", str(r["crf"]), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", r["audio_bitrate"], "-ar", "48000", "-movflags", "+faststart", str(tmp)],
        check=True,
    )
    tmp.replace(out)
    return out


def is_fresh(clip) -> bool:
    """Le rendu existe et correspond aux bornes actuelles."""
    return bool(clip["render_path"] and Path(clip["render_path"]).exists()
                and (clip["rendered_start"], clip["rendered_end"]) == (clip["start"], clip["end"]))


def render_one(conn, cfg, clip_id: int) -> bool:
    """Rend un clip validé si son rendu est absent ou périmé. Renvoie True si un rendu a été fait."""
    from .pipeline import source_path

    clip = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not clip or clip["status"] not in ("approved", "rendered"):
        return False
    if is_fresh(clip):
        if clip["status"] == "approved":
            db.update_clip(conn, clip_id, status="rendered")
        return False
    video = db.get_video(conn, clip["video_id"])
    out = cfg.clip_dir(clip_id) / "clip.mp4"
    log.info("Rendu du clip #%d (%.0f s)…", clip_id, clip["end"] - clip["start"])
    start, end = clip["start"], clip["end"]
    render_clip(source_path(video, cfg), start, end, out, cfg["render"])
    # Ne passe en « rendu » que si les bornes n'ont pas bougé pendant le rendu.
    cur = conn.execute("SELECT start, end, status FROM clips WHERE id = ?", (clip_id,)).fetchone()
    status = "rendered" if (cur["status"] in ("approved", "rendered") and (cur["start"], cur["end"]) == (start, end)) \
        else cur["status"]
    db.update_clip(conn, clip_id, render_path=str(out), rendered_start=start, rendered_end=end, status=status)
    log.info("✓ Clip #%d rendu : %s", clip_id, out)
    return True


def render_pending(conn, cfg) -> int:
    """Rend tous les clips validés en attente (reprise après crash)."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM clips WHERE status IN ('approved', 'rendered') ORDER BY approved_at, id")]
    done = 0
    for cid in ids:
        try:
            done += render_one(conn, cfg, cid)
        except Exception as exc:
            log.error("✗ Rendu du clip #%d impossible : %s", cid, exc)
    return done

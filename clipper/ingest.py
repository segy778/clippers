"""Ingestion : dépliage des URL (vidéo, playlist, chaîne, fichier local) et téléchargement."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .log import log

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Chaîne sans onglet précisé → on vise l'onglet « Vidéos » (pas les shorts ni les lives).
CHANNEL_ROOT = re.compile(r"^https?://(www\.|m\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?$")
SOURCE_SUFFIXES = (".mkv", ".mp4", ".webm", ".mov")


def _ydl_opts(cfg, **extra) -> dict:
    ing = cfg["ingest"]
    opts = {"quiet": True, "no_warnings": True, "noprogress": True}
    if ing.get("js_runtimes"):
        opts["js_runtimes"] = {name: {} for name in ing["js_runtimes"]}
        opts["remote_components"] = {"ejs:github"}  # scripts de déchiffrement si absents en local
    if ing.get("cookies_file"):
        opts["cookiefile"] = ing["cookies_file"]
    if ing.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (ing["cookies_from_browser"],)
    opts.update(extra)
    return opts


def local_source_id(path: Path) -> str:
    st = path.stat()
    digest = hashlib.sha1(f"{path.resolve()}|{st.st_size}".encode()).hexdigest()[:12]
    return f"local-{digest}"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def expand(url: str, cfg) -> list[dict]:
    """Renvoie la liste des vidéos désignées par `url` : [{source_id, url, title, duration, local_path}]."""
    path = Path(url).expanduser()
    if path.exists() and path.is_file():
        return [{
            "source_id": local_source_id(path), "url": str(path.resolve()), "title": path.stem,
            "duration": probe_duration(path), "local_path": str(path.resolve()),
        }]

    import yt_dlp  # import tardif : lent à charger

    if CHANNEL_ROOT.match(url):
        url = url.rstrip("/") + "/videos"
    with yt_dlp.YoutubeDL(_ydl_opts(cfg, extract_flat="in_playlist")) as ydl:
        info = ydl.extract_info(url, download=False)

    videos: list[dict] = []

    def walk(entry):
        if not entry:
            return
        if entry.get("_type") == "playlist" or entry.get("entries") is not None:
            for sub in entry.get("entries") or []:
                walk(sub)
            return
        vid = entry.get("id")
        if not vid or not YOUTUBE_ID.match(vid):
            return
        videos.append({
            "source_id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
            "title": entry.get("title"), "duration": entry.get("duration"), "local_path": None,
        })

    walk(info)
    is_list = info.get("_type") == "playlist"
    min_d = cfg["ingest"]["min_duration_s"]
    if is_list and min_d:
        kept = [v for v in videos if not v["duration"] or v["duration"] >= min_d]
        if len(kept) < len(videos):
            log.info("%d vidéo(s) de moins de %d s ignorée(s).", len(videos) - len(kept), min_d)
        videos = kept
    # Dédoublonnage (une vidéo peut apparaître dans plusieurs onglets).
    seen, unique = set(), []
    for v in videos:
        if v["source_id"] not in seen:
            seen.add(v["source_id"])
            unique.append(v)
    return unique


def find_source_file(video_dir: Path) -> Path | None:
    for p in sorted(video_dir.glob("source.*")):
        if p.suffix in SOURCE_SUFFIXES:
            return p
    return None


def download(video, cfg) -> tuple[Path, dict]:
    """Télécharge la vidéo (ou référence le fichier local). Sans effet si déjà fait.

    Renvoie (chemin du fichier source, métadonnées).
    """
    vdir = cfg.video_dir(video["source_id"])
    vdir.mkdir(parents=True, exist_ok=True)
    info_path = vdir / "info.json"

    if video["local_path"]:
        src = Path(video["local_path"])
        if not src.exists():
            raise FileNotFoundError(f"Fichier source introuvable : {src}")
        meta = {"title": video["title"], "duration": probe_duration(src), "channel": None,
                "upload_date": None, "description": ""}
        return src, meta

    existing = find_source_file(vdir)
    if existing and info_path.exists():
        log.info("Déjà téléchargée : %s", existing.name)
        return existing, _meta_from_info(json.loads(info_path.read_text(encoding="utf-8")))

    import yt_dlp

    h = cfg["ingest"]["max_height"]
    opts = _ydl_opts(
        cfg,
        format=f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
        merge_output_format="mkv",
        outtmpl=str(vdir / "source.%(ext)s"),
        writeinfojson=False,
        retries=5,
        fragment_retries=5,
        continuedl=True,  # reprise d'un téléchargement interrompu
    )
    log.info("Téléchargement de %s…", video["url"])
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video["url"], download=True)
        info = ydl.sanitize_info(info)
    src = find_source_file(vdir)
    if not src:
        raise RuntimeError("yt-dlp n'a produit aucun fichier vidéo.")
    keep = {k: info.get(k) for k in ("id", "title", "channel", "channel_id", "duration", "upload_date",
                                       "description", "tags", "webpage_url", "width", "height", "fps")}
    tmp = info_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(info_path)
    return src, _meta_from_info(keep)


def _meta_from_info(info: dict) -> dict:
    return {
        "title": info.get("title"), "channel": info.get("channel"), "duration": info.get("duration"),
        "upload_date": info.get("upload_date"), "description": (info.get("description") or "")[:5000],
        "tags": info.get("tags") or [], "width": info.get("width"), "height": info.get("height"),
    }


def make_proxy(src: Path, proxy: Path) -> Path:
    """Copie légère 720p pour la page de revue (lecture et déplacement instantanés)."""
    if proxy.exists():
        return proxy
    tmp = proxy.with_name(proxy.stem + ".tmp.mp4")
    log.info("Création du proxy de revue 720p…")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "28", "-g", "48", "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart", str(tmp)],
        check=True,
    )
    tmp.replace(proxy)
    return proxy


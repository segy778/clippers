"""Orchestration : fait avancer chaque vidéo d'étape en étape, isole les échecs.

Étapes (colonne videos.stage = dernière étape réussie) :
    new → downloaded → transcribed → selected
Chaque étape vérifie d'abord si son résultat existe déjà : relancer est sans risque.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

from . import costs, db
from .ingest import download, find_source_file
from .log import log
from .selection import select
from .transcribe import transcribe_video
from .transcript import sentences, words_from_json

MAX_ATTEMPTS = 3
FINAL_STAGE = "selected"


def pending_videos(conn, retry: bool = False, only: list[int] | None = None):
    rows = conn.execute("SELECT * FROM videos WHERE stage != ? ORDER BY id", (FINAL_STAGE,)).fetchall()
    out = []
    for r in rows:
        if only and r["id"] not in only:
            continue
        if r["attempts"] >= MAX_ATTEMPTS and not retry and not only:
            continue
        out.append(r)
    return out


def step_download(conn, video, cfg):
    src, meta = download(video, cfg)
    db.update_video(conn, video["id"], stage="downloaded", title=meta.get("title") or video["title"],
                    channel=meta.get("channel"), duration=meta.get("duration") or video["duration"],
                    upload_date=meta.get("upload_date"), metadata=meta)


def source_path(video, cfg):
    if video["local_path"]:
        return Path(video["local_path"])
    src = find_source_file(cfg.video_dir(video["source_id"]))
    if not src:
        raise FileNotFoundError("Fichier source absent : relance le téléchargement (stage → new).")
    return src


def step_transcribe(conn, video, cfg):
    vdir = cfg.video_dir(video["source_id"])
    data, called = transcribe_video(source_path(video, cfg), vdir, video["duration"], cfg)
    if called:
        costs.record(conn, video["id"], "transcription", costs.deepgram_cost(cfg, video["duration"]),
                     f"{data['provider']} {data['model']}")
    log.info("Transcription : %d mots, %d locuteur(s).", len(data["words"]),
             len({w["sp"] for w in data["words"]}))
    db.update_video(conn, video["id"], stage="transcribed")


def load_sentences(video, cfg):
    path = cfg.video_dir(video["source_id"]) / "transcript.json"
    return sentences(words_from_json(json.loads(path.read_text(encoding="utf-8"))))


def step_select(conn, video, cfg, claude):
    sents = load_sentences(video, cfg)
    cands = select(video, sents, claude, cfg)
    ts = db.now_iso()
    # Écriture atomique : soit tous les candidats et le changement d'étape, soit rien.
    with db.transaction(conn):
        conn.execute("DELETE FROM clips WHERE video_id = ? AND status = 'candidate'", (video["id"],))
        for c in cands:
            conn.execute(
                """INSERT INTO clips (video_id, raw_start, raw_end, start, end, topic, score, reason,
                                      created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video["id"], c.start, c.end, c.start, c.end, c.topic, c.score, c.reason, ts, ts),
            )
        db.update_video(conn, video["id"], stage="selected")
    log.info("%d clip(s) candidat(s) enregistré(s).", len(cands))


def process_video(conn, video, cfg, claude_factory):
    vid = video["id"]
    label = f"[{video['source_id']}] {video['title'] or video['url']}"
    log.info("— %s (étape actuelle : %s)", label, video["stage"])
    if video["stage"] == "new":
        step_download(conn, video, cfg)
        video = db.get_video(conn, vid)
    if video["stage"] == "downloaded":
        step_transcribe(conn, video, cfg)
        video = db.get_video(conn, vid)
    if video["stage"] == "transcribed":
        step_select(conn, video, cfg, claude_factory())
        video = db.get_video(conn, vid)
    db.update_video(conn, vid, error=None, attempts=0)
    log.info("✓ %s : %s, coût cumulé %.3f $", label, video["stage"], costs.video_cost(conn, vid))


def run(conn, cfg, retry: bool = False, only: list[int] | None = None) -> tuple[int, int]:
    """Traite toutes les vidéos en attente. Renvoie (succès, échecs)."""
    videos = pending_videos(conn, retry, only)
    if not videos:
        log.info("Rien à traiter : toutes les vidéos sont à jour.")
        return 0, 0
    claude = None

    def claude_factory():
        nonlocal claude
        if claude is None:
            from .llm import Claude
            claude = Claude(cfg, conn)
        return claude

    ok = ko = 0
    for video in videos:
        try:
            process_video(conn, video, cfg, claude_factory)
            ok += 1
        except KeyboardInterrupt:
            log.warning("Interrompu. Relance `clipper run` pour reprendre là où ça s'est arrêté.")
            raise
        except Exception as exc:  # une vidéo en échec ne bloque pas les autres
            ko += 1
            attempts = (db.get_video(conn, video["id"])["attempts"] or 0) + 1
            db.update_video(conn, video["id"], error=f"{type(exc).__name__}: {exc}", attempts=attempts)
            log.error("✗ [%s] échec (tentative %d/%d) : %s", video["source_id"], attempts, MAX_ATTEMPTS, exc)
            log.debug(traceback.format_exc())
    log.info("Terminé : %d vidéo(s) traitée(s), %d en échec.", ok, ko)
    return ok, ko

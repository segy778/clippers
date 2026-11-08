"""Serveur de revue local : API JSON + pages statiques + fichiers médias.

Une connexion SQLite par requête (le serveur traite les requêtes dans plusieurs threads).
Les rendus des clips validés passent par une file traitée par un seul thread de fond.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from .. import db
from ..log import log
from ..pipeline import source_path
from ..render import is_fresh, render_one
from ..thumbs import make_thumbnail
from ..titles import MAX_TITLE, clean_tags, clean_thumb_text, full_description

STATIC = Path(__file__).parent / "static"
EDITABLE = {"title", "titles", "description", "tags", "thumb_text"}
STATUSES = {"approved", "rejected", "candidate"}


class RenderWorker:
    """File de rendu : un clip à la fois, pour ne pas saturer la machine."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.q: queue.Queue[int] = queue.Queue()
        self.pending: set[int] = set()
        self.current: int | None = None
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="rendu").start()

    def submit(self, clip_id: int) -> None:
        with self.lock:
            if clip_id in self.pending:
                return
            self.pending.add(clip_id)
        self.q.put(clip_id)

    def state(self) -> dict:
        with self.lock:
            return {"current": self.current, "pending": sorted(self.pending - {self.current})}

    def _loop(self):
        while True:
            cid = self.q.get()
            with self.lock:
                self.current = cid
            conn = db.connect(self.cfg.db_path)
            try:
                render_one(conn, self.cfg, cid)
            except Exception as exc:
                log.error("✗ Rendu du clip #%d impossible : %s", cid, exc)
            finally:
                conn.close()
                with self.lock:
                    self.pending.discard(cid)
                    self.current = None


def create_app(cfg, start_worker: bool = True) -> FastAPI:
    app = FastAPI(title="Clipper — revue")
    worker = RenderWorker(cfg) if start_worker else None

    def conn():
        return db.connect(cfg.db_path)

    def clip_payload(c, video) -> dict:
        d = db.clip_dict(c)
        d["duration"] = d["end"] - d["start"]
        d["full_description"] = full_description(d["description"], video, d["start"])
        d["thumb_url"] = f"/media/clips/{d['id']}/thumb.jpg?v={d['updated_at']}" if d["thumb_path"] else None
        frames_dir = cfg.clip_dir(d["id"]) / "frames"
        n = len(list(frames_dir.glob("frame_*.jpg"))) if frames_dir.exists() else 0
        d["frames"] = [f"/media/clips/{d['id']}/frames/{i}.jpg" for i in range(n)]
        d["render_fresh"] = is_fresh(c)
        d["render_url"] = f"/media/clips/{d['id']}/clip.mp4?v={d['updated_at']}" if d["render_fresh"] else None
        return d

    def get_clip_or_404(c, clip_id):
        row = c.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Clip inconnu")
        return row

    # Reprise : les clips validés non rendus repartent dans la file au démarrage.
    if worker:
        c0 = conn()
        for r in c0.execute("SELECT * FROM clips WHERE status IN ('approved', 'rendered')"):
            if not is_fresh(r):
                worker.submit(r["id"])
        c0.close()

    # --- Pages ---------------------------------------------------------------
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/video/{video_id}")
    def video_page(video_id: int):
        return FileResponse(STATIC / "video.html")

    @app.get("/static/{name}")
    def static(name: str):
        path = (STATIC / name).resolve()
        if path.parent != STATIC.resolve() or not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    # --- API -----------------------------------------------------------------
    @app.get("/api/videos")
    def list_videos():
        c = conn()
        try:
            rows = c.execute(
                """SELECT v.id, v.source_id, v.title, v.channel, v.duration, v.stage, v.error,
                          COUNT(cl.id) AS clips,
                          SUM(cl.status = 'candidate') AS todo,
                          SUM(cl.status IN ('approved', 'rendered', 'uploaded')) AS approved,
                          SUM(cl.status = 'rejected') AS rejected
                   FROM videos v LEFT JOIN clips cl ON cl.video_id = v.id
                   GROUP BY v.id ORDER BY v.id DESC""").fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: int):
        c = conn()
        try:
            v = db.get_video(c, video_id)
            if not v:
                raise HTTPException(404, "Vidéo inconnue")
            clips = c.execute(
                """SELECT * FROM clips WHERE video_id = ? AND NOT (status = 'auto_rejected'
                   AND reject_reason LIKE 'Chevauche%') ORDER BY start""", (video_id,)).fetchall()
            proxy = cfg.video_dir(v["source_id"]) / "proxy.mp4"
            return {
                "video": {k: v[k] for k in ("id", "source_id", "title", "channel", "duration", "stage", "url")},
                "proxy_url": f"/media/videos/{video_id}/proxy.mp4" if proxy.exists() else None,
                "clips": [clip_payload(cl, v) for cl in clips],
                "limits": {"min": cfg["bounds"]["min_duration_s"], "max": cfg["bounds"]["max_duration_s"],
                           "title": MAX_TITLE},
            }
        finally:
            c.close()

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: int, body: dict = Body(...)):
        c = conn()
        try:
            row = get_clip_or_404(c, clip_id)
            v = db.get_video(c, row["video_id"])
            if row["status"] == "uploaded":
                raise HTTPException(409, "Clip déjà publié : modification impossible.")
            fields = {}
            for key in EDITABLE & body.keys():
                fields[key] = body[key]
            if "tags" in fields:
                fields["tags"] = clean_tags(fields["tags"])
            if "thumb_text" in fields:
                fields["thumb_text"] = clean_thumb_text(fields["thumb_text"])
            if "title" in fields:
                fields["title"] = str(fields["title"]).strip()[:MAX_TITLE]
            if "titles" in fields:
                fields["titles"] = [str(t).strip()[:MAX_TITLE] for t in fields["titles"]]

            if "start" in body or "end" in body:
                start = round(float(body.get("start", row["start"])), 3)
                end = round(float(body.get("end", row["end"])), 3)
                start, end = max(0.0, start), min(v["duration"] or end, end)
                if end - start < 10:
                    raise HTTPException(400, "Bornes invalides.")
                fields.update(start=start, end=end)

            status = body.get("status")
            if status is not None:
                if status not in STATUSES:
                    raise HTTPException(400, f"Statut inconnu : {status}")
                if status == "approved" and row["status"] not in ("approved", "rendered"):
                    fields.update(status="approved", approved_at=db.now_iso(), reject_reason=None)
                elif status != "approved":
                    fields.update(status=status)
            # Bornes modifiées sur un clip déjà rendu → il faudra le rendre à nouveau.
            if ("start" in fields or "end" in fields) and fields.get("status", row["status"]) == "rendered":
                fields["status"] = "approved"
            if fields:
                db.update_clip(c, clip_id, **fields)
            row = get_clip_or_404(c, clip_id)
            if worker and row["status"] in ("approved", "rendered") and not is_fresh(row):
                worker.submit(clip_id)
            return clip_payload(row, v)
        finally:
            c.close()

    @app.post("/api/clips/{clip_id}/thumbnail")
    def regenerate_thumbnail(clip_id: int, body: dict = Body(default={})):
        """Recompose la miniature avec l'image `frame` (indice) et/ou un nouveau texte."""
        c = conn()
        try:
            row = get_clip_or_404(c, clip_id)
            v = db.get_video(c, row["video_id"])
            clip = dict(row)
            if "text" in body:
                clip["thumb_text"] = clean_thumb_text(body["text"])
            frame = body.get("frame", row["thumb_frame"])
            path, idx = make_thumbnail(source_path(v, cfg), clip, cfg.clip_dir(clip_id), cfg, frame=frame)
            db.update_clip(c, clip_id, thumb_path=str(path), thumb_frame=idx, thumb_text=clip["thumb_text"])
            return clip_payload(get_clip_or_404(c, clip_id), v)
        finally:
            c.close()

    @app.get("/api/render-queue")
    def render_queue():
        return worker.state() if worker else {"current": None, "pending": []}

    # --- Médias (Range pris en charge par FileResponse : lecture et déplacement dans la vidéo) ---
    @app.get("/media/videos/{video_id}/proxy.mp4")
    def proxy_file(video_id: int):
        c = conn()
        try:
            v = db.get_video(c, video_id)
        finally:
            c.close()
        path = cfg.video_dir(v["source_id"]) / "proxy.mp4" if v else None
        if not path or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="video/mp4")

    @app.get("/media/clips/{clip_id}/{name}")
    def clip_file(clip_id: int, name: str):
        if name not in ("thumb.jpg", "clip.mp4"):
            raise HTTPException(404)
        path = cfg.clip_dir(clip_id) / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.get("/media/clips/{clip_id}/frames/{idx}.jpg")
    def frame_file(clip_id: int, idx: int):
        path = cfg.clip_dir(clip_id) / "frames" / f"frame_{idx}.jpg"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/favicon.ico")
    def favicon():
        return RedirectResponse("/static/favicon.svg")

    return app

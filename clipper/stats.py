"""Suivi : vues et durée moyenne de visionnage de chaque clip à J+2 et J+7 après publication.

Chaque relevé couvre la période [date de publication, date de publication + N jours] (dates
du Pacifique, comme YouTube Analytics). Les données Analytics arrivent avec 1 à 3 jours de
retard : un relevé « J+2 » est donc souvent fait vers J+4 ou J+5, mais il mesure bien les
2 premiers jours. Passé un délai de grâce sans données, on enregistre le compteur de vues
actuel (API Data) sans durée moyenne.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, quota
from .log import log
from .publish import parse_iso

GRACE_DAYS = 5  # attente max des données Analytics après la date du relevé


def due_checkpoints(conn, cfg, now: datetime) -> list[tuple[dict, int]]:
    """(clip, jours) pour chaque relevé arrivé à échéance et pas encore enregistré."""
    out = []
    rows = conn.execute(
        "SELECT * FROM clips WHERE status = 'uploaded' AND yt_video_id IS NOT NULL AND publish_at IS NOT NULL"
    ).fetchall()
    for clip in rows:
        published = parse_iso(clip["publish_at"])
        done = {r["day_offset"] for r in conn.execute("SELECT day_offset FROM stats WHERE clip_id = ?", (clip["id"],))}
        for d in cfg["stats"]["checkpoints_days"]:
            if d not in done and now >= published + timedelta(days=d):
                out.append((clip, d))
    return out


def collect(conn, cfg, yt, now: datetime | None = None) -> int:
    """Relève les statistiques dues. Renvoie le nombre de relevés enregistrés."""
    now = now or datetime.now(timezone.utc)
    due = due_checkpoints(conn, cfg, now)
    if not due:
        log.info("Aucun relevé à faire pour l'instant.")
        return 0
    ids = sorted({c["yt_video_id"] for c, _ in due})
    batches = -(-len(ids) // 50)
    if quota.remaining(conn, cfg, now) < batches * cfg["quota"]["list_cost"]:
        log.warning("Quota YouTube épuisé : relevés reportés.")
        return 0
    quota.charge(conn, batches * cfg["quota"]["list_cost"], now)
    current = yt.view_counts(ids)

    saved = 0
    for clip, d in due:
        vid = clip["yt_video_id"]
        info = current.get(vid)
        if info is None:
            log.warning("Clip #%d : vidéo %s introuvable sur YouTube (supprimée ?).", clip["id"], vid)
            continue
        if info["privacy"] != "public":
            log.warning("Clip #%d : la vidéo est encore « %s » (audit API en attente ?).", clip["id"], info["privacy"])
        start = quota.quota_day(parse_iso(clip["publish_at"]))
        end_dt = datetime.fromisoformat(start) + timedelta(days=d)
        report = yt.video_report(vid, start, end_dt.date().isoformat())
        if report is None:
            if now < parse_iso(clip["publish_at"]) + timedelta(days=d + GRACE_DAYS):
                log.info("Clip #%d J+%d : données Analytics pas encore disponibles, nouvel essai plus tard.",
                         clip["id"], d)
                continue
            report = {"views": info["views"], "avg_view_duration": None}
            log.warning("Clip #%d J+%d : pas de données Analytics, vues actuelles enregistrées.", clip["id"], d)
        conn.execute(
            """INSERT OR IGNORE INTO stats (clip_id, day_offset, views, avg_view_duration, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (clip["id"], d, report["views"], report["avg_view_duration"], db.now_iso()),
        )
        saved += 1
        log.info("Clip #%d J+%d : %d vues, durée moyenne %s", clip["id"], d, report["views"],
                 f"{report['avg_view_duration']:.0f} s" if report["avg_view_duration"] is not None else "inconnue")
    return saved


def table(conn, cfg) -> list[dict]:
    """Une ligne par clip publié, avec titre, note et relevés."""
    rows = conn.execute(
        """SELECT c.*, v.source_id, v.title AS video_title FROM clips c JOIN videos v ON v.id = c.video_id
           WHERE c.yt_video_id IS NOT NULL ORDER BY c.publish_at""").fetchall()
    out = []
    for c in rows:
        duration = c["end"] - c["start"]
        line = {
            "clip_id": c["id"], "video_source": c["source_id"], "titre": c["title"], "note": c["score"],
            "sujet": c["topic"], "duree_clip_s": round(duration), "publie_le": c["publish_at"],
            "url": f"https://youtu.be/{c['yt_video_id']}",
        }
        for d in cfg["stats"]["checkpoints_days"]:
            s = conn.execute("SELECT * FROM stats WHERE clip_id = ? AND day_offset = ?", (c["id"], d)).fetchone()
            avg = s["avg_view_duration"] if s else None
            line[f"vues_j{d}"] = s["views"] if s else None
            line[f"duree_moy_j{d}_s"] = round(avg) if avg is not None else None
            # Rétention moyenne : part du clip regardée en moyenne.
            line[f"retention_j{d}_pct"] = round(100 * avg / duration, 1) if avg is not None and duration else None
        out.append(line)
    return out


def export_csv(rows: list[dict], path: Path) -> Path:
    """CSV séparé par « ; » avec BOM UTF-8 : s'ouvre directement dans Excel en français."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        if not rows:
            fh.write("")
            return path
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if v is None else str(v).replace(".", ",") if isinstance(v, float) else v)
                             for k, v in r.items()})
    return path

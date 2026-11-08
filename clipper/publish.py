"""Publication : file des clips validés, créneaux programmés, quota, envoi en privé.

Rien n'est jamais rendu public directement : chaque vidéo part en `private` avec une date
`publishAt` ; c'est YouTube qui la publie au créneau prévu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, quota
from .log import log
from .render import is_fresh, render_one
from .titles import full_description
from .youtube import QuotaExceeded, ThumbnailForbidden

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class LocalQuotaStop(QuotaExceeded):
    """Arrêt décidé par notre propre compteur (pas par YouTube)."""


# --- Créneaux -----------------------------------------------------------------

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_slots(cfg_pub: dict, taken: set[datetime], now: datetime, n: int) -> list[datetime]:
    """Les `n` prochains créneaux libres (en UTC), selon jours et heures de config.yaml."""
    tz = ZoneInfo(cfg_pub["timezone"])
    days = {DAYS.index(d) for d in cfg_pub["days"]}
    hours = sorted(time.fromisoformat(h) for h in cfg_pub["slots"])
    if not days or not hours:
        raise RuntimeError("publish.days et publish.slots ne peuvent pas être vides.")
    earliest = now + timedelta(hours=cfg_pub["min_lead_hours"])
    taken_utc = {t.astimezone(timezone.utc).replace(second=0, microsecond=0) for t in taken}
    out: list[datetime] = []
    day = now.astimezone(tz).date()
    for _ in range(366 * 2):
        if day.weekday() in days:
            for h in hours:
                slot = datetime.combine(day, h, tzinfo=tz).astimezone(timezone.utc)  # gère l'heure d'été
                if slot >= earliest and slot not in taken_utc:
                    out.append(slot)
                    if len(out) == n:
                        return out
        day += timedelta(days=1)
    return out


def taken_slots(conn, now: datetime) -> set[datetime]:
    rows = conn.execute("SELECT publish_at FROM clips WHERE publish_at IS NOT NULL").fetchall()
    return {parse_iso(r["publish_at"]) for r in rows if parse_iso(r["publish_at"]) >= now}


# --- File ---------------------------------------------------------------------

def queue(conn):
    """Clips validés pas encore envoyés, dans l'ordre de validation."""
    return conn.execute(
        """SELECT * FROM clips WHERE status IN ('approved', 'rendered') AND yt_video_id IS NULL
           ORDER BY approved_at, id""").fetchall()


def unfinished(conn):
    """Clips envoyés dont la miniature ou l'ajout à la playlist reste à faire."""
    return conn.execute(
        "SELECT * FROM clips WHERE status = 'uploaded' AND (thumb_set = 0 OR playlist_added = 0) ORDER BY id"
    ).fetchall()


def _yt_text(s: str, limit: int) -> str:
    """YouTube refuse « < » et « > » dans titres et descriptions."""
    return re.sub(r"[<>]", "", s or "").strip()[:limit]


def video_body(clip, video, cfg, publish_at: datetime) -> dict:
    pub = cfg["publish"]
    c = db.clip_dict(clip)
    return {
        "snippet": {
            "title": _yt_text(c["title"] or c["topic"], 100),
            "description": _yt_text(full_description(c["description"], video, c["start"]), 4900),
            "tags": c["tags"],
            "categoryId": str(pub["category_id"]),
            "defaultLanguage": pub["default_language"],
            "defaultAudioLanguage": pub["default_language"],
        },
        "status": {
            "privacyStatus": "private",          # jamais public directement
            "publishAt": to_iso_z(publish_at),   # YouTube publiera à cette date
            "selfDeclaredMadeForKids": False,
        },
    }


@dataclass
class Report:
    uploaded: list[tuple[int, str, str]] = field(default_factory=list)  # (clip, yt_id, publish_at)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    stopped_reason: str | None = None


JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def fmt_local(dt: datetime, cfg) -> str:
    """« lun. 09/11 18:00 » dans le fuseau de publication (indépendant de la locale système)."""
    local = dt.astimezone(ZoneInfo(cfg["publish"]["timezone"]))
    return f"{JOURS[local.weekday()]} {local:%d/%m %H:%M}"


def plan(conn, cfg, now: datetime | None = None, limit: int | None = None) -> list[tuple[dict, datetime]]:
    """Ce que `publish` ferait maintenant (clips et créneaux), dans la limite du quota."""
    now = now or datetime.now(timezone.utc)
    n = min(len(queue(conn)), quota.clips_left_today(conn, cfg, now), limit or 10**6)
    q = queue(conn)[:n]
    slots = next_slots(cfg["publish"], taken_slots(conn, now), now, len(q))
    return list(zip(q, slots))


def _finish_extras(conn, cfg, yt, clip, report: Report, now: datetime) -> None:
    """Miniature puis playlist, chacune décomptée et mémorisée séparément."""
    qc, pl = cfg["quota"], cfg["publish"].get("playlist_id")
    if not clip["thumb_set"]:
        thumb = clip["thumb_path"]
        if not thumb or not Path(thumb).exists():
            log.warning("Clip #%d : miniature absente, non envoyée.", clip["id"])
        elif quota.remaining(conn, cfg, now) < qc["thumbnail_cost"]:
            raise LocalQuotaStop("quota insuffisant pour la miniature")
        else:
            quota.charge(conn, qc["thumbnail_cost"], now)
            try:
                yt.set_thumbnail(clip["yt_video_id"], Path(thumb))
                db.update_clip(conn, clip["id"], thumb_set=1)
            except ThumbnailForbidden:
                log.warning("Miniature refusée pour #%d : la chaîne doit être vérifiée "
                            "(https://www.youtube.com/verify). Elle sera retentée au prochain publish.", clip["id"])
                report.skipped.append((clip["id"], "miniature refusée (chaîne non vérifiée)"))
    if pl and not clip["playlist_added"]:
        if quota.remaining(conn, cfg, now) < qc["playlist_cost"]:
            raise LocalQuotaStop("quota insuffisant pour l'ajout à la playlist")
        quota.charge(conn, qc["playlist_cost"], now)
        yt.add_to_playlist(pl, clip["yt_video_id"])
        db.update_clip(conn, clip["id"], playlist_added=1)
    elif not pl and not clip["playlist_added"]:
        db.update_clip(conn, clip["id"], playlist_added=1)  # rien à faire


def _recover_interrupted(conn, cfg, yt, clip, channel: dict, now: datetime) -> str | None:
    """Envoi interrompu (crash après acceptation par YouTube ?) : cherche la vidéo sur la chaîne."""
    quota.charge(conn, cfg["quota"]["list_cost"], now)
    title = video_body(clip, db.get_video(conn, clip["video_id"]), cfg, now)["snippet"]["title"]
    taken = {r["yt_video_id"] for r in conn.execute("SELECT yt_video_id FROM clips WHERE yt_video_id IS NOT NULL")}
    for item in yt.recent_uploads(channel["uploads"]):
        if item["title"] == title and item["id"] not in taken:
            return item["id"]
    return None


def publish(conn, cfg, yt, now: datetime | None = None, limit: int | None = None) -> Report:
    """Envoie les clips de la file tant que le quota le permet. La file n'est jamais perdue."""
    now = now or datetime.now(timezone.utc)
    report = Report()
    qc = cfg["quota"]
    per_clip = quota.cost_per_clip(cfg)
    channel = None

    def get_channel():
        nonlocal channel
        if channel is None:
            quota.charge(conn, qc["list_cost"], now)
            channel = yt.channel()
        return channel

    def reload(cid):
        return conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()

    try:
        # 1. Miniatures / playlists restées en suspens lors d'un passage précédent.
        for clip in unfinished(conn):
            _finish_extras(conn, cfg, yt, clip, report, now)

        # 2. Nouveaux envois.
        for clip in queue(conn):
            if limit is not None and len(report.uploaded) >= limit:
                break
            if quota.remaining(conn, cfg, now) < per_clip:
                raise LocalQuotaStop(f"quota insuffisant : il faut {per_clip} unités par clip")
            render_one(conn, cfg, clip["id"])  # rendu si absent ou périmé
            clip = reload(clip["id"])
            if not is_fresh(clip):
                report.skipped.append((clip["id"], "rendu indisponible"))
                continue
            yt_id = None
            if clip["upload_started_at"]:  # un envoi précédent a été interrompu
                yt_id = _recover_interrupted(conn, cfg, yt, clip, get_channel(), now)
                if yt_id:
                    log.info("Clip #%d : envoi interrompu retrouvé sur la chaîne (%s), pas de renvoi.",
                             clip["id"], yt_id)
            # Créneau mémorisé AVANT l'envoi : un envoi retrouvé garde sa date.
            if clip["publish_at"] and parse_iso(clip["publish_at"]) > now:
                slot = parse_iso(clip["publish_at"])
            else:
                slot = next_slots(cfg["publish"], taken_slots(conn, now), now, 1)[0]
            if not yt_id:
                db.update_clip(conn, clip["id"], upload_started_at=db.now_iso(), publish_at=to_iso_z(slot))
                quota.charge(conn, qc["upload_cost"], now)
                log.info("Envoi du clip #%d « %s » → publication %s", clip["id"], clip["title"], fmt_local(slot, cfg))
                yt_id = yt.upload(Path(clip["render_path"]),
                                  video_body(clip, db.get_video(conn, clip["video_id"]), cfg, slot))
            db.update_clip(conn, clip["id"], yt_video_id=yt_id, status="uploaded", publish_at=to_iso_z(slot),
                           uploaded_at=db.now_iso())
            report.uploaded.append((clip["id"], yt_id, to_iso_z(slot)))
            _finish_extras(conn, cfg, yt, reload(clip["id"]), report, now)
    except QuotaExceeded as exc:
        if not isinstance(exc, LocalQuotaStop):
            quota.exhaust(conn, cfg, now)  # YouTube dit stop : on s'aligne
        report.stopped_reason = str(exc)
    return report

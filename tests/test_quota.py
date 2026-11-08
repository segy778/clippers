"""Quota YouTube et publication : blocage sans perte de file, créneaux, reprise, doublons."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from clipper import db, publish, quota
from clipper.youtube import QuotaExceeded, ThumbnailForbidden

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)  # lundi 9 h à Paris


# --- Quota ----------------------------------------------------------------------

def test_quota_day_follows_pacific_time():
    # 7 h UTC le 9 = 23 h le 8 à Los Angeles
    assert quota.quota_day(datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)) == "2026-11-08"
    assert quota.quota_day(datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)) == "2026-11-09"


def test_next_reset_is_pacific_midnight():
    assert quota.next_reset(NOW) == datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)  # PST = UTC−8


def test_charge_remaining_and_clips_left(conn, cfg):
    assert quota.remaining(conn, cfg, NOW) == 10000
    assert quota.cost_per_clip(cfg) == 1650  # pas de playlist configurée
    cfg["publish"]["playlist_id"] = "PL1"
    assert quota.cost_per_clip(cfg) == 1700
    assert quota.clips_left_today(conn, cfg, NOW) == 5  # 10 000 // 1 700 : 5, pas 6
    quota.charge(conn, 1700, NOW)
    quota.charge(conn, 1700, NOW)
    assert quota.remaining(conn, cfg, NOW) == 6600 and quota.clips_left_today(conn, cfg, NOW) == 3


def test_exhaust_never_lowers_counter(conn, cfg):
    quota.exhaust(conn, cfg, NOW)
    assert quota.remaining(conn, cfg, NOW) == 0
    quota.charge(conn, 50, NOW)
    quota.exhaust(conn, cfg, NOW)
    assert quota.used(conn, NOW) == 10050


# --- Créneaux -------------------------------------------------------------------

def P(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=PARIS).astimezone(timezone.utc)


def test_slots_daily_with_lead_time(cfg):
    pub = dict(cfg["publish"], slots=["08:00", "18:00"])
    slots = publish.next_slots(pub, set(), NOW, 3)
    assert slots == [P(9, 18), P(10, 8), P(10, 18)]  # 8 h le 9 est passé (et < 2 h d'avance)


def test_slots_skip_taken_and_days(cfg):
    pub = dict(cfg["publish"], slots=["18:00"], days=["tue", "thu"])
    assert publish.next_slots(pub, {P(10, 18)}, NOW, 2) == [P(12, 18), P(17, 18)]


def test_slots_handle_dst():
    pub = {"timezone": "Europe/Paris", "slots": ["18:00"], "days": ["sun"], "min_lead_hours": 0}
    now = datetime(2027, 3, 20, 12, tzinfo=timezone.utc)
    s1, s2 = publish.next_slots(pub, set(), now, 2)
    assert s1.hour == 17 and s2.hour == 16  # 18 h Paris = 17 h UTC en hiver, 16 h UTC en été


# --- Publication ------------------------------------------------------------------

class FakeYT:
    def __init__(self, fail_upload_at=None, thumb_forbidden=False, uploads=None):
        self.uploads, self.thumbs, self.playlist = [], [], []
        self.fail_upload_at = fail_upload_at
        self.thumb_forbidden = thumb_forbidden
        self.channel_uploads = uploads or []

    def channel(self):
        return {"id": "UC1", "title": "Clips", "uploads": "UU1"}

    def recent_uploads(self, playlist):
        return self.channel_uploads

    def upload(self, path, body):
        if self.fail_upload_at is not None and len(self.uploads) == self.fail_upload_at:
            raise QuotaExceeded("YouTube refuse : quotaExceeded")
        assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"].endswith("Z")
        self.uploads.append(body)
        return f"YT{len(self.uploads)}"

    def set_thumbnail(self, vid, path):
        if self.thumb_forbidden:
            raise ThumbnailForbidden("non vérifiée")
        self.thumbs.append(vid)

    def add_to_playlist(self, pl, vid):
        self.playlist.append((pl, vid))


@pytest.fixture
def clips(conn, cfg, tmp_path):
    """8 clips validés et rendus."""
    db.add_video(conn, "abcdefghijk", "u", "Épisode", 3600)
    render = tmp_path / "clip.mp4"
    render.write_bytes(b"mp4")
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"jpg")
    ts = db.now_iso()
    for i in range(8):
        conn.execute(
            """INSERT INTO clips (video_id, raw_start, raw_end, start, end, topic, score, status, title, titles,
               description, tags, render_path, rendered_start, rendered_end, thumb_path, approved_at,
               created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, 't', 50, 'rendered', ?, '[]', 'd <b>', '["x"]', ?, ?, ?, ?, ?, ?, ?)""",
            (i * 400, i * 400 + 200, i * 400, i * 400 + 200, f"Titre {i}", str(render), i * 400, i * 400 + 200,
             str(thumb), f"2026-11-0{i + 1}T00:00:00", ts, ts))
    cfg["publish"]["playlist_id"] = "PL1"
    return conn


def test_publish_stops_at_quota_without_losing_queue(clips, cfg):
    yt = FakeYT()
    rep = publish.publish(clips, cfg, yt, now=NOW)
    assert len(rep.uploaded) == 5 and rep.stopped_reason  # 5 × 1 700 = 8 500 ; il reste 1 500 < 1 700
    assert len(yt.thumbs) == 5 and len(yt.playlist) == 5
    assert quota.used(clips, NOW) == 8500
    assert len(publish.queue(clips)) == 3  # rien de perdu
    statuses = [r["status"] for r in clips.execute("SELECT status FROM clips ORDER BY id")]
    assert statuses == ["uploaded"] * 5 + ["rendered"] * 3
    # ordre de validation respecté, un créneau par clip, descriptions nettoyées
    publish_at = [r["publish_at"] for r in clips.execute("SELECT publish_at FROM clips WHERE yt_video_id IS NOT NULL ORDER BY id")]
    assert publish_at == sorted(publish_at) and len(set(publish_at)) == 5
    assert "<" not in yt.uploads[0]["snippet"]["description"] and "youtu.be/abcdefghijk?t=0" in yt.uploads[0]["snippet"]["description"]

    # Le lendemain (heure du Pacifique), la file reprend où elle s'était arrêtée.
    tomorrow = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    rep2 = publish.publish(clips, cfg, yt, now=tomorrow)
    assert [c for c, _, _ in rep2.uploaded] == [6, 7, 8] and rep2.stopped_reason is None
    assert len(yt.uploads) == 8 and len({b["status"]["publishAt"] for b in yt.uploads}) == 8


def test_no_call_when_quota_already_spent(clips, cfg):
    quota.charge(clips, 9000, NOW)
    yt = FakeYT()
    rep = publish.publish(clips, cfg, yt, now=NOW)
    assert yt.uploads == [] and rep.stopped_reason and len(publish.queue(clips)) == 8


def test_youtube_quota_error_blocks_rest_of_day(clips, cfg):
    yt = FakeYT(fail_upload_at=2)
    rep = publish.publish(clips, cfg, yt, now=NOW)
    assert len(rep.uploaded) == 2 and "quotaExceeded" in rep.stopped_reason
    assert quota.remaining(clips, cfg, NOW) == 0
    # le clip en échec est resté dans la file, avec son créneau réservé
    third = clips.execute("SELECT * FROM clips WHERE id = 3").fetchone()
    assert third["status"] == "rendered" and third["yt_video_id"] is None and third["upload_started_at"]


def test_interrupted_upload_is_recovered_not_duplicated(clips, cfg):
    # Crash simulé : envoi commencé, YouTube a reçu la vidéo, mais l'identifiant n'a pas été enregistré.
    slot = "2026-11-12T17:00:00Z"
    db.update_clip(clips, 1, upload_started_at=db.now_iso(), publish_at=slot)
    yt = FakeYT(uploads=[{"id": "YTCRASH", "title": "Titre 0"}])
    rep = publish.publish(clips, cfg, yt, now=NOW, limit=1)
    assert rep.uploaded == [(1, "YTCRASH", slot)] and yt.uploads == []
    assert len(yt.thumbs) == 1  # la miniature est quand même posée


def test_interrupted_upload_not_found_is_resent(clips, cfg):
    db.update_clip(clips, 1, upload_started_at=db.now_iso())
    yt = FakeYT(uploads=[{"id": "OTHER", "title": "Autre"}])
    rep = publish.publish(clips, cfg, yt, now=NOW, limit=1)
    assert rep.uploaded[0][1] == "YT1" and len(yt.uploads) == 1


def test_thumbnail_forbidden_is_retried_later(clips, cfg):
    rep = publish.publish(clips, cfg, FakeYT(thumb_forbidden=True), now=NOW, limit=1)
    assert len(rep.uploaded) == 1 and rep.skipped
    assert len(publish.unfinished(clips)) == 1
    yt = FakeYT()
    publish.publish(clips, cfg, yt, now=NOW, limit=0)  # aucun nouvel envoi, mais la miniature est rattrapée
    assert yt.thumbs == ["YT1"] and yt.uploads == [] and publish.unfinished(clips) == []


def test_never_uploads_same_clip_twice(clips, cfg):
    yt = FakeYT()
    publish.publish(clips, cfg, yt, now=NOW, limit=2)
    publish.publish(clips, cfg, yt, now=NOW, limit=0)
    ids = [r["yt_video_id"] for r in clips.execute("SELECT yt_video_id FROM clips WHERE yt_video_id IS NOT NULL")]
    assert ids == ["YT1", "YT2"] and len(yt.uploads) == 2


def test_dry_run_plan_matches_quota(clips, cfg):
    planned = publish.plan(clips, cfg, now=NOW)
    assert [c["id"] for c, _ in planned] == [1, 2, 3, 4, 5]
    assert planned[0][1] == P(9, 18)
    assert quota.used(clips, NOW) == 0  # le plan ne consomme rien

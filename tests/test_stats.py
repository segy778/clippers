"""Suivi J+2 / J+7 : échéances, délai des données Analytics, repli, export CSV."""
import csv
from datetime import datetime, timedelta, timezone

import pytest

from clipper import db, stats

PUB = "2026-11-02T17:00:00Z"  # lundi 18 h à Paris = 9 h à Los Angeles (2 nov.)


class FakeYT:
    def __init__(self, report=None, privacy="public"):
        self.report = report
        self.privacy = privacy
        self.queries = []

    def view_counts(self, ids):
        return {i: {"views": 999, "privacy": self.privacy} for i in ids}

    def video_report(self, vid, start, end):
        self.queries.append((vid, start, end))
        return self.report


@pytest.fixture
def published(conn):
    db.add_video(conn, "abcdefghijk", "u", "Épisode", 3600)
    ts = db.now_iso()
    conn.execute("""INSERT INTO clips (video_id, raw_start, raw_end, start, end, topic, score, status, title,
                    yt_video_id, publish_at, created_at, updated_at)
                    VALUES (1, 0, 200, 0, 200, 'Sujet', 87, 'uploaded', 'Un titre', 'YT1', ?, ?, ?)""", (PUB, ts, ts))
    return conn


def at(days, hours=0):
    return datetime(2026, 11, 2, 17, tzinfo=timezone.utc) + timedelta(days=days, hours=hours)


def test_nothing_due_before_two_days(published, cfg):
    assert stats.due_checkpoints(published, cfg, at(1, 23)) == []
    assert [d for _, d in stats.due_checkpoints(published, cfg, at(2))] == [2]
    assert [d for _, d in stats.due_checkpoints(published, cfg, at(8))] == [2, 7]


def test_collect_uses_publication_window(published, cfg):
    yt = FakeYT({"views": 1234, "avg_view_duration": 95.4})
    assert stats.collect(published, cfg, yt, now=at(4)) == 1
    assert yt.queries == [("YT1", "2026-11-02", "2026-11-04")]
    row = published.execute("SELECT * FROM stats").fetchone()
    assert (row["day_offset"], row["views"], row["avg_view_duration"]) == (2, 1234, 95.4)
    assert stats.collect(published, cfg, yt, now=at(5)) == 0  # pas de doublon


def test_waits_for_analytics_then_falls_back(published, cfg):
    yt = FakeYT(report=None)
    assert stats.collect(published, cfg, yt, now=at(3)) == 0  # on attend
    assert stats.collect(published, cfg, yt, now=at(2 + stats.GRACE_DAYS)) == 1  # repli : vues actuelles
    row = published.execute("SELECT * FROM stats").fetchone()
    assert row["views"] == 999 and row["avg_view_duration"] is None


def test_table_and_csv(published, cfg, tmp_path):
    stats.collect(published, cfg, FakeYT({"views": 1234, "avg_view_duration": 95.4}), now=at(9))
    rows = stats.table(published, cfg)
    r = rows[0]
    assert (r["titre"], r["note"], r["vues_j2"], r["vues_j7"], r["retention_j7_pct"]) == ("Un titre", 87, 1234, 1234, 47.7)
    path = stats.export_csv(rows, tmp_path / "stats.csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM pour Excel
    lines = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    assert lines[0]["titre"] == "Un titre" and lines[0]["retention_j2_pct"] == "47,7"
    assert lines[0]["url"] == "https://youtu.be/YT1"


def test_quota_spent_postpones(published, cfg):
    from clipper import quota
    quota.exhaust(published, cfg, at(4))
    assert stats.collect(published, cfg, FakeYT({"views": 1, "avg_view_duration": 1}), now=at(4)) == 0

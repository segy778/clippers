"""Doublons : une vidéo n'est jamais ajoutée deux fois, un clip jamais uploadé deux fois."""
import sqlite3

import pytest

from clipper import db


def test_add_video_twice(conn):
    assert db.add_video(conn, "abcdefghijk", "https://youtu.be/abcdefghijk") is True
    assert db.add_video(conn, "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk") is False
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1


def test_find_video_by_id_or_source(conn):
    db.add_video(conn, "abcdefghijk", "u")
    assert db.find_video(conn, "1")["source_id"] == "abcdefghijk"
    assert db.find_video(conn, "abcdefghijk")["id"] == 1
    assert db.find_video(conn, "zzz") is None


def test_yt_video_id_unique(conn):
    db.add_video(conn, "abcdefghijk", "u")
    ts = db.now_iso()
    for _ in range(2):
        conn.execute("INSERT INTO clips (video_id, raw_start, raw_end, start, end, topic, score, created_at, updated_at)"
                     " VALUES (1, 0, 150, 0, 150, 't', 50, ?, ?)", (ts, ts))
    db.update_clip(conn, 1, yt_video_id="YT1")
    with pytest.raises(sqlite3.IntegrityError):
        db.update_clip(conn, 2, yt_video_id="YT1")


def test_transaction_rollback(conn):
    db.add_video(conn, "abcdefghijk", "u")
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            db.update_video(conn, 1, stage="selected")
            raise RuntimeError("boum")
    assert db.get_video(conn, 1)["stage"] == "new"

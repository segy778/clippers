"""API de revue : décisions, ajustement des bornes, édition, lecture partielle du proxy."""
import pytest
from fastapi.testclient import TestClient

from clipper import db
from clipper.web.app import create_app


@pytest.fixture
def client(cfg):
    conn = db.connect(cfg.db_path)
    db.add_video(conn, "abcdefghijk", "https://youtu.be/abcdefghijk", "Épisode", 3600)
    db.update_video(conn, 1, stage="ready")
    ts = db.now_iso()
    conn.execute("""INSERT INTO clips (video_id, raw_start, raw_end, start, end, topic, score, titles, title,
                    description, tags, created_at, updated_at)
                    VALUES (1, 100, 250, 100, 250, 'Sujet', 80, '["T1","T2","T3"]', 'T1', 'Desc', '["a"]', ?, ?)""",
                 (ts, ts))
    proxy = cfg.video_dir("abcdefghijk") / "proxy.mp4"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(bytes(range(256)) * 40)
    conn.close()
    return TestClient(create_app(cfg, start_worker=False))


def test_video_payload(client):
    data = client.get("/api/videos/1").json()
    clip = data["clips"][0]
    assert clip["title"] == "T1" and clip["duration"] == 150
    assert "https://youtu.be/abcdefghijk?t=100" in clip["full_description"]
    assert data["proxy_url"] == "/media/videos/1/proxy.mp4"


def test_approve_then_adjust_then_reject(client, cfg):
    c = client.patch("/api/clips/1", json={"status": "approved"}).json()
    assert c["status"] == "approved" and c["approved_at"]
    c = client.patch("/api/clips/1", json={"start": 99, "end": 251}).json()
    assert (c["start"], c["end"], c["full_description"].count("?t=99")) == (99, 251, 1)
    c = client.patch("/api/clips/1", json={"status": "rejected"}).json()
    assert c["status"] == "rejected"
    c = client.patch("/api/clips/1", json={"status": "candidate"}).json()
    assert c["status"] == "candidate"


def test_rendered_clip_back_to_approved_when_bounds_change(client, cfg):
    conn = db.connect(cfg.db_path)
    db.update_clip(conn, 1, status="rendered", render_path="/x.mp4", rendered_start=100, rendered_end=250)
    conn.close()
    assert client.patch("/api/clips/1", json={"end": 252}).json()["status"] == "approved"


def test_edit_fields(client):
    c = client.patch("/api/clips/1", json={"titles": ["Nouveau", "T2", "T3"], "title": "Nouveau" + "x" * 80,
                                           "tags": ["#x", "x", "y"], "description": "Autre"}).json()
    assert c["titles"][0] == "Nouveau" and len(c["title"]) == 60 and c["tags"] == ["x", "y"]


def test_invalid_status_and_unknown_clip(client):
    assert client.patch("/api/clips/1", json={"status": "publie"}).status_code == 400
    assert client.patch("/api/clips/42", json={"status": "approved"}).status_code == 404


def test_uploaded_clip_is_locked(client, cfg):
    conn = db.connect(cfg.db_path)
    db.update_clip(conn, 1, status="uploaded")
    conn.close()
    assert client.patch("/api/clips/1", json={"start": 90}).status_code == 409


def test_proxy_supports_range(client):
    r = client.get("/media/videos/1/proxy.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))


def test_pages_served(client):
    assert "Clipper" in client.get("/").text
    assert "review.js" in client.get("/video/1").text
    assert client.get("/static/review.js").status_code == 200
    assert client.get("/static/../app.py").status_code == 404

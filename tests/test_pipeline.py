"""Pipeline de bout en bout sans réseau : fichier local réel (ffmpeg), Deepgram et Claude simulés."""
import subprocess

import pytest

from clipper import db, pipeline, transcribe
from clipper.ingest import expand


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-shortest", str(path)],
        check=True,
    )
    return path


class FakeBackend:
    calls = 0

    def transcribe(self, audio, duration):
        FakeBackend.calls += 1
        # 400 phrases de 3 mots, 1 s par mot → 1200 s de « parole » (la durée réelle importe peu ici).
        words = []
        for i in range(1200):
            text = f"mot{i}." if i % 3 == 2 else f"mot{i}"
            words.append({"w": text, "s": float(i), "e": i + 0.8, "sp": (i // 300) % 2})
        return {"provider": "fake", "model": "fake", "language": "fr", "duration": 1200, "words": words}


class FakeClaude:
    calls = 0

    def json_call(self, **kwargs):
        FakeClaude.calls += 1
        assert "[0.0-2.8] S0: mot0 mot1 mot2." in kwargs["user"]
        return {"candidates": [
            {"start": 0, "end": 200.8, "topic": "Premier sujet", "score": 70, "reason": "r"},
            {"start": 30, "end": 230.8, "topic": "Doublon", "score": 60, "reason": "r"},
            {"start": 600, "end": 800.8, "topic": "Second sujet", "score": 90, "reason": "r"},
        ]}


@pytest.fixture(autouse=True)
def fakes(monkeypatch, cfg):
    FakeBackend.calls = FakeClaude.calls = 0
    monkeypatch.setattr(transcribe, "get_backend", lambda cfg: FakeBackend())
    import clipper.llm
    monkeypatch.setattr(clipper.llm, "Claude", lambda cfg, conn: FakeClaude())
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    (cfg.prompts_dir / "selection.md").write_text(
        "{{video_title}} {{channel}} {{duration}} {{min_clips}} {{max_clips}} {{min_duration}} "
        "{{max_duration}} {{max_overlap}} {{block_info}}", encoding="utf-8")


def _add(conn, cfg, path):
    v = expand(str(path), cfg)[0]
    db.add_video(conn, v["source_id"], v["url"], v["title"], v["duration"], v["local_path"])
    return db.find_video(conn, v["source_id"])


def test_full_run_then_idempotent(conn, cfg, video_file):
    video = _add(conn, cfg, video_file)
    assert pipeline.run(conn, cfg) == (1, 0)
    video = db.get_video(conn, video["id"])
    assert video["stage"] == "selected" and video["error"] is None
    clips = conn.execute("SELECT * FROM clips ORDER BY start").fetchall()
    assert [c["topic"] for c in clips] == ["Premier sujet", "Second sujet"]  # doublon retiré
    assert (cfg.video_dir(video["source_id"]) / "transcript.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM costs WHERE kind='transcription'").fetchone()[0] == 1

    # Deuxième passage : aucun appel externe, rien ne change.
    assert pipeline.run(conn, cfg) == (0, 0)
    assert FakeBackend.calls == 1 and FakeClaude.calls == 1
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 2


def test_resume_after_crash_uses_cache(conn, cfg, video_file, monkeypatch):
    video = _add(conn, cfg, video_file)
    import clipper.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("panne réseau")

    monkeypatch.setattr(p, "select", boom)
    assert pipeline.run(conn, cfg) == (0, 1)
    v = db.get_video(conn, video["id"])
    assert v["stage"] == "transcribed" and "panne réseau" in v["error"] and v["attempts"] == 1

    monkeypatch.undo()
    monkeypatch.setattr(transcribe, "get_backend", lambda cfg: FakeBackend())
    import clipper.llm
    monkeypatch.setattr(clipper.llm, "Claude", lambda cfg, conn: FakeClaude())
    assert pipeline.run(conn, cfg) == (1, 0)
    assert FakeBackend.calls == 1  # transcription non refaite
    v = db.get_video(conn, video["id"])
    assert v["stage"] == "selected" and v["error"] is None and v["attempts"] == 0


def test_failing_video_does_not_block_others(conn, cfg, video_file):
    db.add_video(conn, "local-absent", "/nexiste/pas.mp4", "absente", 100, "/nexiste/pas.mp4")
    good = _add(conn, cfg, video_file)
    assert pipeline.run(conn, cfg) == (1, 1)
    assert db.get_video(conn, good["id"])["stage"] == "selected"
    bad = db.find_video(conn, "local-absent")
    assert bad["stage"] == "new" and "introuvable" in bad["error"]


def test_repeated_failures_are_skipped_unless_retry(conn, cfg):
    db.add_video(conn, "local-absent", "/nexiste/pas.mp4", "absente", 100, "/nexiste/pas.mp4")
    for _ in range(pipeline.MAX_ATTEMPTS):
        pipeline.run(conn, cfg)
    assert pipeline.run(conn, cfg) == (0, 0)  # ignorée
    assert pipeline.run(conn, cfg, retry=True) == (0, 1)

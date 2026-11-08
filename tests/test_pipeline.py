"""Pipeline de bout en bout sans réseau : fichier local réel (ffmpeg), Deepgram et Claude simulés."""
import shutil
import subprocess
from pathlib import Path

import pytest

from clipper import db, pipeline, transcribe
from clipper.ingest import expand
from clipper.selection import select as real_select


REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def video_file(tmp_path_factory):
    """Vidéo de 15 min, très basse définition (rapide à générer)."""
    path = tmp_path_factory.mktemp("src") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "900", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-b:a", "32k", str(path)],
        check=True,
    )
    return path


class FakeBackend:
    calls = 0

    def transcribe(self, audio, duration):
        FakeBackend.calls += 1
        # 400 phrases de 3 mots, 0,75 s par mot → 900 s de « parole ».
        words = []
        for i in range(1200):
            text = f"mot{i}." if i % 3 == 2 else f"mot{i}"
            words.append({"w": text, "s": i * 0.75, "e": i * 0.75 + 0.6, "sp": (i // 300) % 2})
        return {"provider": "fake", "model": "fake", "language": "fr", "duration": 1200, "words": words}


class FakeClaude:
    calls = 0

    def json_call(self, **kwargs):
        FakeClaude.calls += 1
        if kwargs["kind"] == "titles":
            import re
            ids = [int(x) for x in re.findall(r"id=(\d+)", kwargs["user"])]
            return {"clips": [{"id": i, "titles": [f"Titre {i} " + "x" * 70, "B", "C"], "description": "Desc.",
                               "tags": ["a", "#b", "a"], "thumbnail_text": "un deux trois quatre cinq"}
                              for i in ids]}
        assert "[0.0-2.1] S0: mot0 mot1 mot2." in kwargs["user"]
        return {"candidates": [
            {"start": 0, "end": 200.1, "topic": "Premier sujet", "score": 70, "reason": "r"},
            {"start": 30, "end": 230, "topic": "Doublon", "score": 60, "reason": "r"},
            {"start": 600, "end": 800, "topic": "Second sujet", "score": 90, "reason": "r"},
            {"start": 820, "end": 880, "topic": "Trop court", "score": 95, "reason": "r"},
        ]}


@pytest.fixture(autouse=True)
def fakes(monkeypatch, cfg):
    FakeBackend.calls = FakeClaude.calls = 0
    monkeypatch.setattr(transcribe, "get_backend", lambda cfg: FakeBackend())
    import clipper.llm
    monkeypatch.setattr(clipper.llm, "Claude", lambda cfg, conn: FakeClaude())
    shutil.copytree(REPO / "prompts", cfg.prompts_dir)
    # Étapes ffmpeg lourdes simulées ici (testées à part) pour garder la suite rapide.
    monkeypatch.setattr(transcribe, "extract_audio", lambda src, out: out.write_bytes(b"") or out)
    monkeypatch.setattr(pipeline, "make_proxy", lambda src, proxy: shutil.copy(src, proxy))


def _add(conn, cfg, path):
    v = expand(str(path), cfg)[0]
    db.add_video(conn, v["source_id"], v["url"], v["title"], v["duration"], v["local_path"])
    return db.find_video(conn, v["source_id"])


def test_full_run_then_idempotent(conn, cfg, video_file):
    video = _add(conn, cfg, video_file)
    assert pipeline.run(conn, cfg) == (1, 0)
    video = db.get_video(conn, video["id"])
    assert video["stage"] == "ready" and video["error"] is None
    clips = {c["topic"]: db.clip_dict(c) for c in conn.execute("SELECT * FROM clips")}
    assert clips.keys() == {"Premier sujet", "Second sujet", "Trop court"}  # doublon retiré dès la sélection
    assert clips["Trop court"]["status"] == "auto_rejected" and "Trop court" in clips["Trop court"]["reject_reason"]
    first = clips["Premier sujet"]
    assert first["status"] == "candidate"
    assert first["start"] == 0.0 and first["end"] == pytest.approx(200.25)  # fin 200.1 + 0,3 s, bornée au mot suivant (200.25)
    assert len(first["titles"][0]) <= 60 and first["title"] == first["titles"][0]
    assert first["tags"] == ["a", "b"] and first["thumb_text"] == "un deux trois quatre"
    assert Path(first["thumb_path"]).exists()
    assert (cfg.video_dir(video["source_id"]) / "proxy.mp4").exists()
    assert (cfg.video_dir(video["source_id"]) / "transcript.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM costs WHERE kind='transcription'").fetchone()[0] == 1

    # Deuxième passage : aucun appel externe, rien ne change.
    assert pipeline.run(conn, cfg) == (0, 0)
    assert FakeBackend.calls == 1 and FakeClaude.calls == 2  # sélection + titres
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 3


def test_resume_after_crash_uses_cache(conn, cfg, video_file, monkeypatch):
    video = _add(conn, cfg, video_file)
    import clipper.pipeline as p

    def boom(*a, **k):
        raise RuntimeError("panne réseau")

    monkeypatch.setattr(p, "select", boom)
    assert pipeline.run(conn, cfg) == (0, 1)
    v = db.get_video(conn, video["id"])
    assert v["stage"] == "transcribed" and "panne réseau" in v["error"] and v["attempts"] == 1

    monkeypatch.setattr(p, "select", real_select)
    assert pipeline.run(conn, cfg) == (1, 0)
    assert FakeBackend.calls == 1  # transcription non refaite
    v = db.get_video(conn, video["id"])
    assert v["stage"] == "ready" and v["error"] is None and v["attempts"] == 0


def test_failing_video_does_not_block_others(conn, cfg, video_file):
    db.add_video(conn, "local-absent", "/nexiste/pas.mp4", "absente", 100, "/nexiste/pas.mp4")
    good = _add(conn, cfg, video_file)
    assert pipeline.run(conn, cfg) == (1, 1)
    assert db.get_video(conn, good["id"])["stage"] == "ready"
    bad = db.find_video(conn, "local-absent")
    assert bad["stage"] == "new" and "introuvable" in bad["error"]


def test_repeated_failures_are_skipped_unless_retry(conn, cfg):
    db.add_video(conn, "local-absent", "/nexiste/pas.mp4", "absente", 100, "/nexiste/pas.mp4")
    for _ in range(pipeline.MAX_ATTEMPTS):
        pipeline.run(conn, cfg)
    assert pipeline.run(conn, cfg) == (0, 0)  # ignorée
    assert pipeline.run(conn, cfg, retry=True) == (0, 1)

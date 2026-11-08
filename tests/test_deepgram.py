"""Normalisation de la réponse Deepgram (format documenté de /v1/listen)."""
from clipper.transcribe import DeepgramBackend


def test_normalize(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    raw = {
        "metadata": {"duration": 12.5},
        "results": {"channels": [{"alternatives": [{"words": [
            {"word": "bonjour", "punctuated_word": "Bonjour", "start": 0.12, "end": 0.5, "speaker": 0},
            {"word": "salut", "punctuated_word": "Salut.", "start": 0.9, "end": 1.2, "speaker": 1},
            {"word": "ok", "start": 1.3, "end": 1.4},
        ]}]}]},
    }
    out = DeepgramBackend("nova-3", "fr").normalize(raw)
    assert out["duration"] == 12.5 and out["language"] == "fr"
    assert out["words"] == [
        {"w": "Bonjour", "s": 0.12, "e": 0.5, "sp": 0},
        {"w": "Salut.", "s": 0.9, "e": 1.2, "sp": 1},
        {"w": "ok", "s": 1.3, "e": 1.4, "sp": 0},
    ]

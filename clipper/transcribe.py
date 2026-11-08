"""Transcription horodatée au mot avec locuteurs. Résultat mis en cache dans transcript.json."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol

import httpx

from .config import require_env
from .log import log

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class Backend(Protocol):
    """Interface commune : un backend renvoie la transcription au format normalisé.

    Format : {"provider", "model", "language", "duration", "words": [{"w", "s", "e", "sp"}]}
    (w = mot ponctué, s/e = début/fin en secondes, sp = numéro de locuteur).
    Un backend WhisperX local pourra s'ajouter ici sans toucher au reste.
    """

    def transcribe(self, audio: Path, duration: float) -> dict: ...


def extract_audio(src: Path, out: Path) -> Path:
    """Piste audio mono 16 kHz en Opus : ~15 Mo par heure, suffisant pour la reconnaissance."""
    if out.exists():
        return out
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libopus", "-b:a", "32k", str(tmp)],
        check=True,
    )
    tmp.replace(out)
    return out


class DeepgramBackend:
    def __init__(self, model: str, language: str):
        self.model = model
        self.language = language
        self.key = require_env("DEEPGRAM_API_KEY")

    def transcribe(self, audio: Path, duration: float) -> dict:
        params = {
            "model": self.model, "diarize": "true", "smart_format": "true", "punctuate": "true",
        }
        if self.language == "multi":
            params["detect_language"] = "true"
        else:
            params["language"] = self.language
        # Délai généreux : l'envoi + le traitement d'une heure d'audio prend en général < 1 min.
        timeout = httpx.Timeout(connect=30, read=max(300, duration / 3), write=600, pool=30)
        with audio.open("rb") as fh:
            resp = httpx.post(
                DEEPGRAM_URL, params=params, content=fh,
                headers={"Authorization": f"Token {self.key}", "Content-Type": "audio/ogg"},
                timeout=timeout,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram a répondu {resp.status_code} : {resp.text[:300]}")
        return self.normalize(resp.json())

    def normalize(self, raw: dict) -> dict:
        alt = raw["results"]["channels"][0]["alternatives"][0]
        words = [
            {"w": w.get("punctuated_word") or w["word"], "s": round(w["start"], 3),
             "e": round(w["end"], 3), "sp": int(w.get("speaker", 0))}
            for w in alt.get("words", [])
        ]
        meta = raw.get("metadata", {})
        lang = raw["results"]["channels"][0].get("detected_language") or self.language
        return {"provider": "deepgram", "model": self.model, "language": lang,
                "duration": meta.get("duration"), "words": words}


def get_backend(cfg) -> Backend:
    t = cfg["transcription"]
    if t["provider"] == "deepgram":
        return DeepgramBackend(t["model"], t["language"])
    raise ValueError(f"Fournisseur de transcription inconnu : {t['provider']}")


def transcribe_video(src: Path, vdir: Path, duration: float, cfg) -> tuple[dict, bool]:
    """Renvoie (transcription, appel_effectué). Jamais deux appels pour la même vidéo."""
    out = vdir / "transcript.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8")), False
    backend = get_backend(cfg)  # vérifie la clé avant tout travail
    audio = extract_audio(src, vdir / "audio.ogg")
    log.info("Transcription (%s, %.1f min d'audio)…", cfg["transcription"]["provider"], duration / 60)
    data = backend.transcribe(audio, duration)
    if not data["words"]:
        raise RuntimeError("Transcription vide : aucun mot reconnu.")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    audio.unlink(missing_ok=True)  # l'audio n'est plus utile une fois la transcription en cache
    return data, True

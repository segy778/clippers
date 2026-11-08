"""Titres, description, tags et texte de miniature (un seul appel à Claude par vidéo)."""
from __future__ import annotations

import re

from .llm import LLMError, load_prompt
from .log import log
from .transcript import Sentence, fmt_ts, render

MAX_TITLE = 60          # au-delà, les titres sont coupés dans les résultats YouTube
N_TITLES = 3
N_TAGS = 10
MAX_TAGS_CHARS = 450    # YouTube refuse au-delà de 500 caractères de tags au total
THUMB_WORDS = (3, 4)

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "titles": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "thumbnail_text": {"type": "string"},
                },
                "required": ["id", "titles", "description", "tags", "thumbnail_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


def clean_title(title: str) -> str:
    """Nettoie un titre et le ramène à 60 caractères en coupant sur un mot."""
    t = re.sub(r"\s+", " ", str(title)).strip().strip('"«» ').strip()
    if len(t) <= MAX_TITLE:
        return t
    cut = t[:MAX_TITLE + 1].rsplit(" ", 1)[0].rstrip(" ,;:–-")
    return cut[:MAX_TITLE]


def clean_tags(tags) -> list[str]:
    out, seen, total = [], set(), 0
    for tag in tags or []:
        t = re.sub(r"\s+", " ", str(tag)).strip().lstrip("#").replace("<", "").replace(">", "").strip()
        if not t or t.lower() in seen:
            continue
        if total + len(t) > MAX_TAGS_CHARS:
            break
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
        if len(out) == N_TAGS:
            break
    return out


def clean_thumb_text(text: str) -> str:
    words = re.sub(r"\s+", " ", str(text)).strip().strip('"«» ').split(" ")
    return " ".join(words[:THUMB_WORDS[1]])


def parse_titles(data, clip_ids: set[int]) -> dict[int, dict]:
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        raise LLMError("Sortie de titres invalide : clé « clips » absente.")
    out = {}
    for item in data["clips"]:
        try:
            cid = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid not in clip_ids:
            continue
        titles = [clean_title(t) for t in item.get("titles") or [] if str(t).strip()][:N_TITLES]
        if not titles:
            log.warning("Clip #%d : aucun titre valide reçu.", cid)
            continue
        out[cid] = {
            "titles": titles,
            "description": str(item.get("description", "")).strip(),
            "tags": clean_tags(item.get("tags")),
            "thumb_text": clean_thumb_text(item.get("thumbnail_text", "")),
        }
    return out


def source_footer(video, start: float) -> str:
    """Pied de description : lien vers la vidéo d'origine au bon timecode (calculé à la volée)."""
    title = video["title"] or "la vidéo complète"
    if video["local_path"]:
        return f"Extrait de « {title} » (à partir de {fmt_ts(start)})."
    return (f"Extrait de « {title} » — vidéo complète : "
            f"https://youtu.be/{video['source_id']}?t={int(start)} (à partir de {fmt_ts(start)}).")


def full_description(body: str, video, start: float) -> str:
    return f"{(body or '').strip()}\n\n{source_footer(video, start)}".strip()


def excerpt(sents: list[Sentence], start: float, end: float) -> str:
    return render([s for s in sents if s.start >= start - 0.5 and s.end <= end + 0.5])


def generate(video, clips: list, sents: list[Sentence], claude, cfg) -> dict[int, dict]:
    """Renvoie {clip_id: {titles, description, tags, thumb_text}}."""
    if not clips:
        return {}
    system = load_prompt(cfg, "titles", video_title=video["title"] or "", channel=video["channel"] or "inconnue",
                         max_title=MAX_TITLE, n_titles=N_TITLES, n_tags=N_TAGS,
                         thumb_min=THUMB_WORDS[0], thumb_max=THUMB_WORDS[1])
    parts = [f"## Clip id={c['id']} — sujet : {c['topic']}\n{excerpt(sents, c['start'], c['end'])}" for c in clips]
    log.info("Titres : %d clip(s)…", len(clips))
    data = claude.json_call(system=system, user="\n\n".join(parts), schema=SCHEMA,
                            effort=cfg["llm"]["titles_effort"], video_id=video["id"], kind="titles")
    result = parse_titles(data, {c["id"] for c in clips})
    missing = [c["id"] for c in clips if c["id"] not in result]
    if missing:
        log.warning("Pas de titres reçus pour les clips %s.", ", ".join(f"#{m}" for m in missing))
    return result

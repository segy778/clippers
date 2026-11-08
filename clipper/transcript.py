"""Mots horodatés → phrases, rendu texte pour Claude, découpage en blocs."""
from __future__ import annotations

from dataclasses import dataclass

SENTENCE_END = (".", "?", "!", "…")
CLOSERS = "»\"')]”’ "
MAX_GAP_S = 2.0       # un silence plus long coupe la phrase même sans ponctuation
MAX_SENTENCE_S = 45.0  # garde-fou si la ponctuation manque totalement


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class Sentence:
    start: float
    end: float
    speaker: int
    text: str


def words_from_json(data: dict) -> list[Word]:
    return [Word(w["w"], float(w["s"]), float(w["e"]), int(w.get("sp", 0))) for w in data["words"]]


def _ends_sentence(text: str) -> bool:
    return text.rstrip(CLOSERS).endswith(SENTENCE_END)


def sentences(words: list[Word]) -> list[Sentence]:
    """Regroupe les mots en phrases : ponctuation finale, changement de locuteur ou long silence."""
    out: list[Sentence] = []
    cur: list[Word] = []

    def flush():
        if cur:
            out.append(Sentence(cur[0].start, cur[-1].end, cur[0].speaker, " ".join(w.text for w in cur)))
            cur.clear()

    for w in words:
        if cur and (w.speaker != cur[-1].speaker or w.start - cur[-1].end > MAX_GAP_S
                    or w.end - cur[0].start > MAX_SENTENCE_S):
            flush()
        cur.append(w)
        if _ends_sentence(w.text):
            flush()
    flush()
    return out


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def render(sents: list[Sentence]) -> str:
    """Une ligne par phrase : `[début-fin] S<locuteur>: texte`, temps en secondes (1 décimale).

    Claude renvoie des secondes : les lui donner directement évite les erreurs de conversion.
    """
    return "\n".join(f"[{s.start:.1f}-{s.end:.1f}] S{s.speaker}: {s.text}" for s in sents)


def blocks(sents: list[Sentence], block_s: float, overlap_s: float) -> list[list[Sentence]]:
    """Découpe en blocs de `block_s` secondes qui se recouvrent de `overlap_s`.

    Une seule passe si la vidéo tient dans un bloc (+ recouvrement).
    """
    if not sents:
        return []
    total = sents[-1].end - sents[0].start
    if total <= block_s + overlap_s:
        return [sents]
    step = block_s - overlap_s
    out, t0 = [], sents[0].start
    while True:
        t1 = t0 + block_s
        if sents[-1].end - t1 < block_s * 0.3:  # pas de petit bloc final : on l'absorbe
            t1 = sents[-1].end + 1
        chunk = [s for s in sents if s.start >= t0 and s.start < t1]
        if chunk:
            out.append(chunk)
        if t1 >= sents[-1].end:
            break
        t0 += step
    return out

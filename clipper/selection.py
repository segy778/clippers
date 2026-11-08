"""Sélection des clips candidats par Claude, avec découpage en blocs et fusion."""
from __future__ import annotations

from dataclasses import dataclass

from .llm import LLMError, load_prompt
from .log import log
from .transcript import Sentence, blocks, fmt_ts, render

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "Début en secondes"},
                    "end": {"type": "number", "description": "Fin en secondes"},
                    "topic": {"type": "string", "description": "Sujet du clip en une phrase"},
                    "score": {"type": "integer", "description": "Note sur 100"},
                    "reason": {"type": "string", "description": "Pourquoi ce passage fait un bon clip"},
                },
                "required": ["start", "end", "topic", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


@dataclass
class Candidate:
    start: float
    end: float
    topic: str
    score: int
    reason: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def parse_candidates(data, lo: float, hi: float) -> list[Candidate]:
    """Valide la sortie de Claude. Les entrées invalides sont ignorées (avec un log), pas fatales.

    `lo`/`hi` : intervalle couvert par le bloc envoyé ; les bornes sont ramenées dedans.
    """
    if isinstance(data, list):  # tolère un tableau nu
        data = {"candidates": data}
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise LLMError("Sortie de sélection invalide : clé « candidates » absente.")
    out = []
    for i, item in enumerate(data["candidates"]):
        try:
            start, end = float(item["start"]), float(item["end"])
            topic = str(item["topic"]).strip()
            score = int(round(float(item["score"])))
            reason = str(item.get("reason", "")).strip()
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Candidat %d ignoré (champ manquant ou invalide : %s).", i + 1, exc)
            continue
        start, end = max(lo, start), min(hi, end)
        if end <= start or not topic:
            log.warning("Candidat %d ignoré (bornes incohérentes ou sujet vide).", i + 1)
            continue
        out.append(Candidate(start, end, topic, max(0, min(100, score)), reason))
    return out


def overlap_ratio(a, b) -> float:
    """Part du clip le plus court recouverte par l'autre (0 = disjoints, 1 = inclus)."""
    inter = min(a.end, b.end) - max(a.start, b.start)
    if inter <= 0:
        return 0.0
    shortest = min(a.end - a.start, b.end - b.start)
    return inter / shortest if shortest > 0 else 1.0


def dedupe(cands: list, max_overlap: float, limit: int | None = None) -> list:
    """Garde les meilleurs candidats tels que deux clips ne se recouvrent jamais de plus de `max_overlap`."""
    kept: list = []
    for c in sorted(cands, key=lambda c: (-c.score, c.start)):
        if all(overlap_ratio(c, k) < max_overlap for k in kept):
            kept.append(c)
        if limit and len(kept) >= limit:
            break
    return sorted(kept, key=lambda c: c.start)


def select(video, sents: list[Sentence], claude, cfg) -> list[Candidate]:
    sel, bnd = cfg["selection"], cfg["bounds"]
    parts = blocks(sents, sel["block_minutes"] * 60, sel["block_overlap_minutes"] * 60)
    all_cands: list[Candidate] = []
    for i, part in enumerate(parts, 1):
        lo, hi = part[0].start, part[-1].end
        if len(parts) > 1:
            info = (f"Ceci est le bloc {i}/{len(parts)} de la transcription "
                    f"({fmt_ts(lo)} → {fmt_ts(hi)}). Ne propose que des passages entièrement dans ce bloc.")
            # Proportionnel à la durée du bloc ; la fusion ramènera au maximum global.
            n_min = max(2, round(sel["min_clips"] * (hi - lo) / video["duration"]))
            n_max = max(n_min + 1, round(sel["max_clips"] * (hi - lo) / video["duration"]) + 2)
        else:
            info = "La transcription est complète."
            n_min, n_max = sel["min_clips"], sel["max_clips"]
        system = load_prompt(
            cfg, "selection",
            video_title=video["title"] or "", channel=video["channel"] or "inconnue",
            duration=fmt_ts(video["duration"] or hi), min_clips=n_min, max_clips=n_max,
            min_duration=bnd["min_duration_s"], max_duration=bnd["max_duration_s"],
            max_overlap=int(sel["max_overlap"] * 100), block_info=info,
        )
        log.info("Sélection : bloc %d/%d (%s → %s, %d phrases)…", i, len(parts), fmt_ts(lo), fmt_ts(hi), len(part))
        data = claude.json_call(system=system, user=render(part), schema=SCHEMA,
                                effort=cfg["llm"]["selection_effort"], video_id=video["id"], kind="selection")
        found = parse_candidates(data, lo, hi)
        log.info("  %d candidat(s) proposé(s).", len(found))
        all_cands.extend(found)

    merged = dedupe(all_cands, sel["max_overlap"], sel["max_clips"])
    if len(merged) < len(all_cands):
        log.info("Fusion : %d candidat(s) → %d (chevauchements ou excédent retirés).", len(all_cands), len(merged))
    if len(merged) < sel["min_clips"]:
        log.warning("Seulement %d candidat(s) (minimum souhaité : %d).", len(merged), sel["min_clips"])
    return merged

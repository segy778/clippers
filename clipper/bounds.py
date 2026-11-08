"""Recalage des bornes sur des frontières de phrase, marge, contrôle de durée."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from .transcript import Sentence, fmt_ts


@dataclass
class Bounds:
    start: float
    end: float
    reject_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reject_reason is None


def snap(raw_start: float, raw_end: float, sents: list[Sentence], cfg_bounds: dict,
         duration: float | None = None) -> Bounds:
    """Recale [raw_start, raw_end] : début sur le début de phrase le plus proche, fin sur la fin
    de phrase la plus proche, puis ajoute la marge sans empiéter sur les phrases voisines.

    Renvoie des bornes avec `reject_reason` renseigné si le clip doit être écarté.
    """
    window = cfg_bounds["snap_window_s"]
    margin = cfg_bounds["margin_s"]
    lo_d, hi_d = cfg_bounds["min_duration_s"], cfg_bounds["max_duration_s"]
    if not sents:
        return Bounds(raw_start, raw_end, "Transcription vide.")
    duration = duration or sents[-1].end

    # Phrase dont le début est le plus proche du début proposé.
    starts = [s.start for s in sents]
    k = bisect_left(starts, raw_start)
    i = min((x for x in (k - 1, k) if 0 <= x < len(sents)), key=lambda x: abs(sents[x].start - raw_start))
    if abs(sents[i].start - raw_start) > window:
        return Bounds(raw_start, raw_end, f"Aucun début de phrase à moins de {window:g} s de {fmt_ts(raw_start)}.")

    # Phrase (à partir de i) dont la fin est la plus proche de la fin proposée.
    j = min(range(i, len(sents)), key=lambda x: abs(sents[x].end - raw_end))
    if abs(sents[j].end - raw_end) > window:
        return Bounds(raw_start, raw_end, f"Aucune fin de phrase à moins de {window:g} s de {fmt_ts(raw_end)}.")

    first, last = sents[i], sents[j]
    # Marge, bornée par la fin de la phrase précédente et le début de la suivante.
    prev_end = min(sents[i - 1].end, first.start) if i > 0 else 0.0
    next_start = max(sents[j + 1].start, last.end) if j + 1 < len(sents) else duration
    start = max(0.0, first.start - margin, prev_end)
    end = min(duration, last.end + margin, next_start)
    start, end = round(start, 3), round(end, 3)

    d = end - start
    if d < lo_d:
        return Bounds(start, end, f"Trop court : {fmt_ts(d)} (minimum {fmt_ts(lo_d)}).")
    if d > hi_d:
        return Bounds(start, end, f"Trop long : {fmt_ts(d)} (maximum {fmt_ts(hi_d)}).")
    return Bounds(start, end)

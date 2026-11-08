"""Recalage des bornes : frontières de phrase, marge, voisins, durées."""
import pytest

from clipper.bounds import snap
from clipper.config import DEFAULTS
from clipper.transcript import Sentence

B = DEFAULTS["bounds"]  # min 120 s, max 300 s, marge 0,3 s, fenêtre 15 s


def sents_every(step=10.0, total=1000.0, gap=1.0):
    """Phrases de `step - gap` secondes séparées de `gap` secondes de silence."""
    out, t = [], 0.0
    while t < total:
        out.append(Sentence(t, t + step - gap, 0, "phrase."))
        t += step
    return out


def test_snaps_to_nearest_sentence_and_adds_margin():
    s = sents_every()
    b = snap(103.0, 248.0, s, B, 1000)
    # début de phrase la plus proche : 100 ; fin la plus proche : 249 (phrase 240-249)
    assert b.ok and b.start == pytest.approx(99.7) and b.end == pytest.approx(249.3)


def test_margin_never_bites_neighbour_words():
    s = [Sentence(0, 10, 0, "a."), Sentence(10.1, 140, 0, "b."), Sentence(140.05, 150, 1, "c.")]
    b = snap(10.1, 140, s, B, 150)
    assert b.ok and b.start == pytest.approx(10.0) and b.end == pytest.approx(140.05)


def test_clamped_to_video_limits():
    s = [Sentence(0.1, 150, 0, "a.")]
    b = snap(0, 150, s, B, 150.1)
    assert b.ok and b.start == 0.0 and b.end == pytest.approx(150.1)


def test_end_never_before_start():
    s = sents_every()
    b = snap(500, 380, s, B, 1000)  # fin proposée avant le début
    assert not b.ok


def test_too_short_rejected_with_reason():
    b = snap(100, 200, sents_every(), B, 1000)
    assert not b.ok and "Trop court" in b.reject_reason and "2:00" in b.reject_reason


def test_too_long_rejected_with_reason():
    b = snap(100, 459, sents_every(), B, 1000)
    assert not b.ok and "Trop long" in b.reject_reason


def test_exactly_at_limits_accepted():
    s = [Sentence(10, 20, 0, "a."), Sentence(30, 140, 0, "b."), Sentence(150, 160, 0, "c.")]
    cfg = dict(B, margin_s=0.0, min_duration_s=110)
    assert snap(30, 140, s, cfg, 200).ok


def test_no_sentence_near_start_rejected():
    s = [Sentence(0, 5, 0, "a."), Sentence(100, 400, 0, "b.")]
    b = snap(50, 400, s, B, 400)
    assert not b.ok and "début de phrase" in b.reject_reason


def test_empty_transcript():
    assert not snap(0, 200, [], B).ok

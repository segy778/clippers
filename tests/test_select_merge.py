"""Chevauchement, fusion des blocs et découpage de la transcription."""
from clipper.selection import Candidate, dedupe, overlap_ratio
from clipper.transcript import Sentence, Word, blocks, sentences


def C(start, end, score=50):
    return Candidate(start, end, "t", score, "")


def test_overlap_ratio():
    assert overlap_ratio(C(0, 100), C(100, 200)) == 0
    assert overlap_ratio(C(0, 100), C(50, 250)) == 0.5
    assert overlap_ratio(C(0, 300), C(100, 200)) == 1.0


def test_dedupe_keeps_best_score():
    out = dedupe([C(0, 200, 60), C(150, 350, 90), C(400, 600, 70)], 0.20)
    assert [(c.start, c.score) for c in out] == [(150, 90), (400, 70)]


def test_dedupe_allows_small_overlap():
    out = dedupe([C(0, 200, 60), C(170, 370, 90)], 0.20)  # 30 s / 200 s = 15 %
    assert len(out) == 2


def test_dedupe_limit_and_sorted_by_start():
    cands = [C(i * 300, i * 300 + 200, score=i) for i in range(20)]
    out = dedupe(cands, 0.2, limit=15)
    assert len(out) == 15
    assert min(c.score for c in out) == 5
    assert [c.start for c in out] == sorted(c.start for c in out)


def test_merge_of_overlapping_blocks_removes_duplicate():
    # Le même passage proposé par deux blocs consécutifs (zone de recouvrement).
    out = dedupe([C(5300, 5500, 80), C(5310, 5495, 85)], 0.2)
    assert len(out) == 1 and out[0].score == 85


def _sents(total_s, step=10):
    return [Sentence(t, t + step - 1, 0, "x.") for t in range(0, total_s, step)]


def test_blocks_single_when_short():
    assert len(blocks(_sents(3600), 5400, 300)) == 1


def test_blocks_overlap_and_cover_everything():
    sents = _sents(4 * 3600)
    parts = blocks(sents, 5400, 300)
    assert len(parts) == 3
    covered = {s.start for p in parts for s in p}
    assert covered == {s.start for s in sents}
    # recouvrement de 5 min entre blocs consécutifs
    assert parts[0][-1].start >= parts[1][0].start + 290


def test_sentences_split_on_punctuation_speaker_and_gap():
    words = [Word("Bonjour", 0, 0.5, 0), Word("à", 0.6, 0.7, 0), Word("tous.", 0.8, 1.0, 0),
             Word("Merci", 1.1, 1.4, 1), Word("bien", 1.5, 1.8, 1),
             Word("Alors", 5.0, 5.3, 1), Word("voilà", 5.4, 5.7, 1), Word("»", 5.7, 5.7, 1)]
    out = sentences(words)
    assert [s.text for s in out] == ["Bonjour à tous.", "Merci bien", "Alors voilà »"]
    assert out[1].speaker == 1 and out[0].end == 1.0

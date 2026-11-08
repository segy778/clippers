"""Analyse de la sortie de Claude : JSON brut, entouré de texte, ou partiellement invalide."""
import pytest

from clipper.llm import LLMError, extract_json
from clipper.selection import parse_candidates


def test_extract_plain_json():
    assert extract_json('{"candidates": []}') == {"candidates": []}


def test_extract_fenced_json():
    text = 'Voici :\n```json\n{"candidates": [{"start": 1}]}\n```\nBonne journée'
    assert extract_json(text) == {"candidates": [{"start": 1}]}


def test_extract_json_with_prose_around():
    assert extract_json('Résultat {"a": 1} fin') == {"a": 1}


def test_extract_bare_array():
    assert extract_json("[1, 2]") == [1, 2]


def test_extract_garbage_raises():
    with pytest.raises(LLMError):
        extract_json("pas de json ici")


def _item(**kw):
    base = {"start": 10, "end": 200, "topic": "Un sujet", "score": 80, "reason": "parce que"}
    base.update(kw)
    return base


def test_parse_valid():
    out = parse_candidates({"candidates": [_item()]}, 0, 1000)
    assert len(out) == 1
    assert (out[0].start, out[0].end, out[0].score) == (10, 200, 80)


def test_parse_skips_invalid_items():
    data = {"candidates": [_item(), {"start": "abc", "end": 5}, _item(topic=""), _item(start=300, end=100)]}
    assert len(parse_candidates(data, 0, 1000)) == 1


def test_parse_clamps_to_block_and_score():
    out = parse_candidates({"candidates": [_item(start=-5, end=5000, score=140)]}, 0, 600)
    assert (out[0].start, out[0].end, out[0].score) == (0, 600, 100)


def test_parse_numeric_strings_and_float_score():
    out = parse_candidates({"candidates": [_item(start="12.5", end="150", score=72.6)]}, 0, 1000)
    assert out[0].start == 12.5 and out[0].score == 73


def test_parse_accepts_bare_list():
    assert len(parse_candidates([_item()], 0, 1000)) == 1


def test_parse_missing_key_raises():
    with pytest.raises(LLMError):
        parse_candidates({"clips": []}, 0, 1000)

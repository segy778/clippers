"""Validation des titres, tags et texte de miniature ; pied de description."""
from clipper.titles import clean_tags, clean_thumb_text, clean_title, full_description, parse_titles


def test_title_cut_on_word_under_60():
    t = clean_title("Pourquoi la plupart des entrepreneurs échouent la première année d'activité")
    assert len(t) <= 60 and not t.endswith(" ") and t.startswith("Pourquoi")
    assert "activit" not in t


def test_title_strips_quotes_and_spaces():
    assert clean_title('  « Un   titre »  ') == "Un titre"


def test_tags_dedup_hash_and_limit():
    tags = clean_tags(["#IA", "ia", "  machine   learning ", ""] + [f"t{i}" for i in range(20)])
    assert tags[:2] == ["IA", "machine learning"] and len(tags) == 10


def test_thumb_text_max_4_words():
    assert clean_thumb_text("ne fais jamais cette erreur") == "ne fais jamais cette"


def test_parse_titles_ignores_unknown_ids_and_empty():
    data = {"clips": [
        {"id": 1, "titles": ["A", "B", "C", "D"], "description": "d", "tags": ["x"], "thumbnail_text": "t"},
        {"id": 2, "titles": [], "description": "", "tags": [], "thumbnail_text": ""},
        {"id": 99, "titles": ["Z"], "description": "", "tags": [], "thumbnail_text": ""},
    ]}
    out = parse_titles(data, {1, 2})
    assert list(out) == [1] and out[1]["titles"] == ["A", "B", "C"]


def test_description_footer_youtube_and_local():
    yt = {"title": "Épisode 12", "local_path": None, "source_id": "abcdefghijk"}
    d = full_description("Résumé.", yt, 754.6)
    assert d.startswith("Résumé.") and "https://youtu.be/abcdefghijk?t=754" in d and "12:34" in d
    loc = {"title": "Épisode 12", "local_path": "/x.mp4", "source_id": "local-1"}
    assert "youtu.be" not in full_description("R.", loc, 10)

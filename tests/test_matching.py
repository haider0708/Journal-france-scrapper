from app.matching import find_names, find_snippet, tokenize_name

NAMES = ["Lobna Marsaoui"]


def _level(text):
    res = find_names(text, NAMES)
    return res[0]["level"] if res else None


def test_full_reversed_order_uppercase_surname():
    assert _level("Nomination de Mme MARSAOUI Lobna au poste de...") == "full"


def test_full_lowercase_normal_order():
    assert _level("Mme lobna marsaoui est nommee") == "full"


def test_partial_surname_only():
    assert _level("Décret concernant M. Marsaoui, chef de service") == "partial"


def test_partial_first_name_only():
    assert _level("Texte mentionnant seulement Lobna parmi les candidats") == "partial"


def test_no_match():
    assert _level("Un texte qui ne parle de rien de pertinent") is None


def test_all_caps_both():
    assert _level("LOBNA MARSAOUI") == "full"


def test_accents_stripped():
    # Synthetic accented variant still matches.
    assert _level("Mme Lóbna Marsáoui") == "full"


def test_word_boundary_prevents_substring_false_positive():
    # "marsaouienne" should NOT match the token "marsaoui".
    assert _level("politique marsaouienne du quartier") is None


def test_tokenize_drops_short_connectors():
    assert tokenize_name("Jean de la Fontaine") == ["jean", "fontaine"]


def test_snippet_extracts_context():
    snip = find_snippet("Decret concernant M. Marsaoui, chef de service regional", "marsaoui")
    assert "Marsaoui" in snip


def test_snippet_empty_when_absent():
    assert find_snippet("nothing here", "marsaoui") == ""

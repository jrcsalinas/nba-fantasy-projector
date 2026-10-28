"""Tests for finding players by name."""

import pytest

from nbaproj.roster.directory import PlayerDirectory, default_directory, normalize_name

SMALL_LIST = [
    (1, "Luka Dončić"),
    (2, "Jaren Jackson Jr."),
    (3, "De'Aaron Fox"),
    (4, "Jayson Tatum"),
    (5, "Jabari Smith Jr."),
    (6, "Shai Gilgeous-Alexander"),
    (7, "Aaron Jackson"),
    (8, "Jackson Zed"),
]


@pytest.fixture(name="directory")
def directory_fixture() -> PlayerDirectory:
    """A small directory with accents, suffixes, apostrophes, and hyphens."""
    return PlayerDirectory(SMALL_LIST)


def _names(matches) -> list[str]:
    return [match.name for match in matches]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Luka Dončić", "luka doncic"),
        ("De'Aaron Fox", "de aaron fox"),
        ("Jaren Jackson Jr.", "jaren jackson jr"),
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        ("  Kyrie   Irving ", "kyrie irving"),
        ("", ""),
        ("...", ""),
    ],
)
def test_normalize_name_folds_case_accents_and_punctuation(raw, expected):
    """Names and queries are compared as lowercase ASCII words separated by single spaces."""
    assert normalize_name(raw) == expected


def test_search_matches_every_typed_word_as_a_word_start(directory):
    """Each typed word must start some word of the name, in any order; mid-word does not count."""
    assert _names(directory.search("jaren jackson")) == ["Jaren Jackson Jr."]
    assert _names(directory.search("jackson jaren")) == ["Jaren Jackson Jr."]
    assert _names(directory.search("jr")) == ["Jabari Smith Jr.", "Jaren Jackson Jr."]
    assert _names(directory.search("gilgeous")) == ["Shai Gilgeous-Alexander"]
    assert _names(directory.search("de'aaron")) == ["De'Aaron Fox"]
    assert directory.search("ackson") == []


def test_search_ignores_accents_and_case(directory):
    """"doncic", "DONCIC", and the accented spelling all find the same player."""
    for query in ("doncic", "DONCIC", "Dončić"):
        assert _names(directory.search(query)) == ["Luka Dončić"]


@pytest.mark.parametrize("query", ["", " ", "j", "'", "..."])
def test_search_needs_two_characters(directory, query):
    """Too short a query finds nothing, so a stray key does not list the whole league."""
    assert directory.search(query) == []


def test_search_caps_results_and_ranks_names_that_start_with_the_query(directory):
    """Names starting with the query come first, then alphabetical; results are capped."""
    ranked = ["Jackson Zed", "Aaron Jackson", "Jaren Jackson Jr."]
    assert _names(directory.search("jackson")) == ranked
    assert _names(directory.search("jackson", limit=2)) == ["Jackson Zed", "Aaron Jackson"]

    many = PlayerDirectory([(n, f"Jamal Player{n:02d}") for n in range(25)])
    assert len(many.search("jamal")) == 10


def test_search_finds_nothing_for_a_nickname(directory):
    """Only names are searched: a nickname matches nobody."""
    assert directory.search("wemby") == []


def test_matches_carry_the_player_id(directory):
    """A match gives the NBA player id the roster will store."""
    [match] = directory.search("tatum")

    assert (match.player_id, match.name) == (4, "Jayson Tatum")
    assert directory.get(4) == match
    assert directory.get(999) is None


def test_default_directory_is_the_nba_api_active_list():
    """The real directory has the league's active players, unique by id and by name."""
    directory = default_directory()
    players = directory.all_players()

    assert len(players) > 400
    assert len({p.player_id for p in players}) == len(players)
    assert len({normalize_name(p.name) for p in players}) == len(players)
    assert default_directory() is directory


def test_no_real_name_loses_a_letter_when_normalized():
    """Every letter in a real name survives folding, so nobody becomes unsearchable."""
    for player in default_directory().all_players():
        letters = sum(char.isalnum() for char in player.name)
        assert len(normalize_name(player.name).replace(" ", "")) >= letters, player.name


def test_real_list_finds_a_known_player_without_accents():
    """Typing "jokic" finds Nikola Jokić."""
    assert _names(default_directory().search("jokic")) == ["Nikola Jokić"]

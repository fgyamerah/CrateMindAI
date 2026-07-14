from __future__ import annotations

from modules.filename_parse import parse_filename_metadata


def test_clean_filename_parses_artist_and_title():
    result = parse_filename_metadata("C Minor - Kunapendeza feat. Alai K")

    assert result.accepted is True
    assert result.artist == "C Minor"
    assert result.title == "Kunapendeza feat. Alai K"
    assert result.parse_confidence in {"HIGH", "MEDIUM"}


def test_version_is_preserved_in_title():
    result = parse_filename_metadata("Javier Mio - Ampreiah (Original Mix)")

    assert result.accepted is True
    assert result.artist == "Javier Mio"
    assert result.title == "Ampreiah"
    assert result.version == "Original Mix"
    assert result.combined_title() == "Ampreiah (Original Mix)"


def test_single_hyphen_separator_is_recovered():
    result = parse_filename_metadata("Artist-Title")

    assert result.accepted is True
    assert result.artist == "Artist"
    assert result.title == "Title"
    assert result.parse_confidence in {"HIGH", "MEDIUM"}


def test_malformed_name_is_rejected_safely():
    result = parse_filename_metadata("including Manoo Remix, Original Instrumental...")

    assert result.accepted is False
    assert result.parse_confidence == "LOW"

"""
Unit tests for modules/sanitizer.py

Run:
    python3 -m pytest tests/test_sanitizer.py -v
    # or without pytest:
    python3 -m unittest tests.test_sanitizer -v
"""
import sys
import os
import unittest

# Make sure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.sanitizer import sanitize_text, sanitize_metadata


class TestSanitizeText(unittest.TestCase):

    # -----------------------------------------------------------------------
    # Fast path — nothing to remove
    # -----------------------------------------------------------------------

    def test_clean_title_unchanged(self):
        """Legitimate track title must pass through unmodified."""
        self.assertEqual(
            sanitize_text("Track Title (Original Mix)"),
            "Track Title (Original Mix)",
        )

    def test_remix_credit_preserved(self):
        """Remix credits are valid metadata — must never be removed."""
        self.assertEqual(
            sanitize_text("Don't Laugh (Manoo's Laugh Remix)"),
            "Don't Laugh (Manoo's Laugh Remix)",
        )

    def test_extended_mix_preserved(self):
        self.assertEqual(
            sanitize_text("Chime (Extended Mix)"),
            "Chime (Extended Mix)",
        )

    def test_club_mix_preserved(self):
        self.assertEqual(
            sanitize_text("Acid Rain (Club Mix)"),
            "Acid Rain (Club Mix)",
        )

    def test_radio_edit_preserved(self):
        self.assertEqual(
            sanitize_text("Strings of Life (Radio Edit)"),
            "Strings of Life (Radio Edit)",
        )

    def test_empty_string_unchanged(self):
        self.assertEqual(sanitize_text(""), "")

    def test_none_like_empty_unchanged(self):
        """None-equivalent empty string should pass through safely."""
        self.assertEqual(sanitize_text(""), "")

    # -----------------------------------------------------------------------
    # URL removal
    # -----------------------------------------------------------------------

    def test_https_url_removed(self):
        self.assertEqual(
            sanitize_text("Track Title https://fordjonly.com"),
            "Track Title",
        )

    def test_http_url_removed(self):
        self.assertEqual(
            sanitize_text("Track http://www.djcity.com Title"),
            "Track Title",
        )

    def test_www_url_removed(self):
        self.assertEqual(
            sanitize_text("Title www.zipdj.com"),
            "Title",
        )

    def test_plain_domain_com_removed(self):
        self.assertEqual(
            sanitize_text("Artist fordjonly.com"),
            "Artist",
        )

    def test_plain_domain_net_removed(self):
        self.assertEqual(
            sanitize_text("Title something.net"),
            "Title",
        )

    def test_plain_domain_org_removed(self):
        self.assertEqual(
            sanitize_text("Title promo.org"),
            "Title",
        )

    def test_plain_domain_dj_removed(self):
        self.assertEqual(
            sanitize_text("Title beatstars.dj"),
            "Title",
        )

    def test_domain_with_path_removed(self):
        self.assertEqual(
            sanitize_text("Track promo.net/downloads/track123"),
            "Track",
        )

    # -----------------------------------------------------------------------
    # Bracketed junk removal
    # -----------------------------------------------------------------------

    def test_bracketed_domain_square_removed(self):
        self.assertEqual(
            sanitize_text("Track Title [fordjonly.com]"),
            "Track Title",
        )

    def test_bracketed_domain_round_removed(self):
        self.assertEqual(
            sanitize_text("Track Title (djcity.com)"),
            "Track Title",
        )

    def test_bracketed_url_removed(self):
        self.assertEqual(
            sanitize_text("Title [https://promo.net]"),
            "Title",
        )

    def test_bracketed_www_removed(self):
        self.assertEqual(
            sanitize_text("Title [www.beatsource.com]"),
            "Title",
        )

    def test_useful_bracket_preserved(self):
        """Brackets containing version/remix info must not be touched."""
        self.assertEqual(
            sanitize_text("Strings of Life (Club Mix)"),
            "Strings of Life (Club Mix)",
        )

    # -----------------------------------------------------------------------
    # Promo phrase removal
    # -----------------------------------------------------------------------

    def test_for_dj_only_removed(self):
        self.assertEqual(
            sanitize_text("Track Title For DJ Only"),
            "Track Title",
        )

    def test_for_djs_only_removed(self):
        self.assertEqual(
            sanitize_text("Track Title for DJs Only"),
            "Track Title",
        )

    def test_for_dj_use_only_removed(self):
        self.assertEqual(
            sanitize_text("Track for DJ Use Only"),
            "Track",
        )

    def test_promo_only_removed(self):
        self.assertEqual(
            sanitize_text("Track Title Promo Only"),
            "Track Title",
        )

    def test_djcity_removed(self):
        self.assertEqual(
            sanitize_text("DJCity - Track Title"),
            "Track Title",
        )

    def test_djcity_case_insensitive(self):
        self.assertEqual(
            sanitize_text("djcity Track"),
            "Track",
        )

    def test_dj_city_with_space_removed(self):
        self.assertEqual(
            sanitize_text("Track DJ City"),
            "Track",
        )

    def test_zipdj_removed(self):
        self.assertEqual(
            sanitize_text("Track ZipDJ"),
            "Track",
        )

    def test_downloaded_from_removed(self):
        self.assertEqual(
            sanitize_text("Track downloaded from promo.net"),
            "Track",
        )

    def test_downloaded_from_url_removed(self):
        self.assertEqual(
            sanitize_text("Title Downloaded from https://djpool.com/track"),
            "Title",
        )

    def test_official_audio_removed(self):
        self.assertEqual(
            sanitize_text("Track Title (Official Audio)"),
            "Track Title",
        )

    def test_official_video_removed(self):
        self.assertEqual(
            sanitize_text("Track Title Official Video"),
            "Track Title",
        )

    def test_official_music_video_removed(self):
        self.assertEqual(
            sanitize_text("Track - Official Music Video"),
            "Track",
        )

    def test_free_download_removed(self):
        self.assertEqual(
            sanitize_text("Track Title - Free Download"),
            "Track Title",
        )

    def test_buy_on_beatport_removed(self):
        self.assertEqual(
            sanitize_text("Track Buy on Beatport"),
            "Track",
        )

    def test_out_now_on_removed(self):
        self.assertEqual(
            sanitize_text("Artist - Track Out Now on Toolroom"),
            "Artist - Track",
        )

    def test_exclusive_watermark_removed(self):
        self.assertEqual(
            sanitize_text("Track Title EXCLUSIVE"),
            "Track Title",
        )

    def test_exclusive_mix_preserved(self):
        """'Exclusive Mix' is a legitimate version name — must be preserved."""
        self.assertEqual(
            sanitize_text("Track Title (Exclusive Mix)"),
            "Track Title (Exclusive Mix)",
        )

    def test_exclusive_remix_preserved(self):
        self.assertEqual(
            sanitize_text("Track (Exclusive Remix)"),
            "Track (Exclusive Remix)",
        )

    # -----------------------------------------------------------------------
    # Multiple junk items in one string
    # -----------------------------------------------------------------------

    def test_multiple_junk_items_removed(self):
        result = sanitize_text("Track Title [djcity.com] For DJ Only")
        self.assertEqual(result, "Track Title")

    def test_url_and_phrase_removed(self):
        result = sanitize_text("Track www.fordjonly.com Promo Only")
        self.assertEqual(result, "Track")

    def test_domain_and_official_audio(self):
        result = sanitize_text("Track Official Audio fordjonly.com")
        self.assertEqual(result, "Track")

    # -----------------------------------------------------------------------
    # Artifact cleanup
    # -----------------------------------------------------------------------

    def test_empty_brackets_cleaned(self):
        """After removing URL content from brackets, empty brackets go too."""
        result = sanitize_text("Track Title []")
        self.assertEqual(result, "Track Title")

    def test_trailing_dash_cleaned(self):
        result = sanitize_text("Track Title - DJCity")
        self.assertEqual(result, "Track Title")

    def test_leading_separator_cleaned(self):
        result = sanitize_text("- Track Title")
        # Leading dash should be stripped
        self.assertFalse(result.startswith("-"))

    def test_double_space_collapsed(self):
        result = sanitize_text("Track  Title")
        self.assertNotIn("  ", result)

    def test_result_stripped(self):
        result = sanitize_text("  Track Title  ")
        self.assertEqual(result, "Track Title")

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_only_junk_returns_empty(self):
        result = sanitize_text("fordjonly.com")
        self.assertEqual(result, "")

    def test_all_junk_returns_empty(self):
        result = sanitize_text("DJCity For DJ Only promo.net")
        self.assertEqual(result, "")

    def test_unicode_artist_preserved(self):
        """Unicode artist names must pass through cleanly."""
        self.assertEqual(
            sanitize_text("Björk"),
            "Björk",
        )

    def test_unicode_with_junk(self):
        result = sanitize_text("Björk djcity.com")
        self.assertEqual(result, "Björk")

    def test_accented_chars_not_stripped(self):
        self.assertEqual(
            sanitize_text("Henrrï (Original Mix)"),
            "Henrrï (Original Mix)",
        )

    def test_numbers_in_title_preserved(self):
        self.assertEqual(
            sanitize_text("808 State"),
            "808 State",
        )

    def test_year_in_title_preserved(self):
        self.assertEqual(
            sanitize_text("Strings of Life (2023 Remaster)"),
            "Strings of Life (2023 Remaster)",
        )

    # -----------------------------------------------------------------------
    # Symbol removal (™ ® © $ etc.)
    # -----------------------------------------------------------------------

    def test_trademark_removed(self):
        self.assertEqual(sanitize_text("Artist™"), "Artist")

    def test_registered_removed(self):
        self.assertEqual(sanitize_text("Label® Presents"), "Label Presents")

    def test_dollar_removed(self):
        self.assertEqual(sanitize_text("DJ L.p.$"), "DJ L.p.")

    def test_multiple_symbols_removed(self):
        result = sanitize_text("[Artist]™® - Track")
        self.assertNotIn("™", result)
        self.assertNotIn("®", result)

    def test_copyright_removed(self):
        self.assertEqual(sanitize_text("Track © 2024"), "Track 2024")

    def test_clean_text_not_affected(self):
        """Text without symbols must pass through unchanged."""
        self.assertEqual(sanitize_text("Normal Track"), "Normal Track")

    # -----------------------------------------------------------------------
    # Leading junk prefix before pipe (track-index / numbering removal)
    # -----------------------------------------------------------------------

    def test_numeric_pipe_prefix_removed(self):
        """Single digit index before pipe must be stripped."""
        self.assertEqual(
            sanitize_text("2 | Heartbreaker"),
            "Heartbreaker",
        )

    def test_numeric_pipe_prefix_multi_digit(self):
        """Multi-digit index before pipe must be stripped."""
        self.assertEqual(
            sanitize_text("3 | Stay On The Scene"),
            "Stay On The Scene",
        )

    def test_symbol_and_numeric_pipe_prefix_removed(self):
        """Symbol(s) + digit prefix before pipe must be stripped."""
        self.assertEqual(
            sanitize_text("***2 | Love Will (feat. Bilal) [Breyth Remix]"),
            "Love Will (feat. Bilal) [Breyth Remix]",
        )

    def test_letter_pipe_prefix_removed(self):
        """Single letter prefix before pipe must be stripped."""
        self.assertEqual(
            sanitize_text("A | Charlotte (Original)"),
            "Charlotte (Original)",
        )

    def test_no_pipe_prefix_unchanged(self):
        """Title with no leading junk prefix must pass through unchanged."""
        self.assertEqual(
            sanitize_text("Heartbreaker"),
            "Heartbreaker",
        )

    def test_legitimate_pipe_in_middle_preserved(self):
        """Pipe in middle of a real title must NOT be touched."""
        self.assertEqual(
            sanitize_text("Rock | Roll"),
            "Rock | Roll",
        )

    def test_multi_word_before_pipe_preserved(self):
        """A real phrase before a mid-string pipe must not be stripped."""
        self.assertEqual(
            sanitize_text("Day | Night"),
            "Day | Night",
        )

    def test_pipe_prefix_with_no_junk_elsewhere(self):
        """Strip only the prefix; rest of clean title stays intact."""
        self.assertEqual(
            sanitize_text("12 | Something (Original Mix)"),
            "Something (Original Mix)",
        )


class TestSanitizeMetadata(unittest.TestCase):

    def test_clean_fields_no_changes(self):
        fields = {"title": "Chime (Original Mix)", "artist": "Orbital"}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(result["title"], "Chime (Original Mix)")
        self.assertEqual(result["artist"], "Orbital")
        self.assertEqual(changes, [])

    def test_dirty_title_cleaned(self):
        fields = {"title": "Track [fordjonly.com]", "artist": "Artist"}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(result["title"], "Track")
        self.assertEqual(len(changes), 1)
        self.assertIn("title", changes[0])

    def test_dirty_artist_cleaned(self):
        fields = {"artist": "Artist DJCity", "title": "Track"}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(result["artist"], "Artist")
        self.assertEqual(len(changes), 1)

    def test_multiple_fields_cleaned(self):
        fields = {
            "title": "Track [djcity.com]",
            "artist": "Artist",
            "genre": "House Promo Only",
            "comment": "Downloaded from promo.net",
        }
        result, changes = sanitize_metadata(fields)
        self.assertEqual(result["title"], "Track")
        self.assertEqual(result["genre"], "House")
        self.assertEqual(result["comment"], "")
        self.assertEqual(len(changes), 3)

    def test_none_fields_handled(self):
        """None and missing fields must not crash."""
        fields = {"title": None, "artist": None}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(changes, [])

    def test_empty_fields_handled(self):
        fields = {"title": "", "artist": ""}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(changes, [])

    def test_changes_list_content(self):
        """Changes list must show both old and new value."""
        fields = {"title": "Track [djcity.com]"}
        _, changes = sanitize_metadata(fields)
        self.assertTrue(any("djcity.com" in c for c in changes))
        self.assertTrue(any("Track" in c for c in changes))

    def test_partial_fields_dict(self):
        """Dict with only some fields must work — missing fields ignored."""
        fields = {"title": "Track [fordjonly.com]"}
        result, changes = sanitize_metadata(fields)
        self.assertEqual(result["title"], "Track")
        self.assertNotIn("artist", changes)


if __name__ == "__main__":
    unittest.main(verbosity=2)

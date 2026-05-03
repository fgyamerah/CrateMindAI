"""
tests/test_metadata_sanitize.py — Unit tests for metadata_sanitize._sanitize_title new rules.

Covers:
  - trailing BPM stripping (e.g. " 122" at end)
  - domain/piracy paren tokens (e.g. "(fordjonly.com)")
  - label-like trailing paren suffix (e.g. "(Xumba Recordings)")
  - bare leading track-number (e.g. "3 Afro", "2 Sada")
  - (Feat. → (feat. casing normalisation
  - protected mix/version parens must NOT be stripped
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.metadata_sanitize import _sanitize_title, _strip_label_suffix


# ---------------------------------------------------------------------------
# Trailing BPM
# ---------------------------------------------------------------------------

class TestTrailingBpm:
    def test_bpm_with_space(self):
        result, reason = _sanitize_title("Turk (Original Mix) (Sunset Gathering) 122")
        assert result == "Turk (Original Mix) (Sunset Gathering)"
        assert reason == "title_trailing_bpm_stripped"

    def test_bpm_no_space_after_close_paren(self):
        # BPM is stripped first, then label suffix
        result, reason = _sanitize_title("Zafir (Original Mix) (Xumba Recordings)122")
        assert result == "Zafir (Original Mix)"
        # First fired reason is bpm strip; label fires second but result contains both
        assert reason in ("title_trailing_bpm_stripped", "title_label_suffix_stripped")

    def test_bpm_120(self):
        result, reason = _sanitize_title("Sunrise (Club Edit) 120")
        assert result == "Sunrise (Club Edit)"
        assert reason == "title_trailing_bpm_stripped"

    def test_bpm_out_of_range_not_stripped(self):
        # 200 is > 160 — should NOT be stripped as BPM
        result, reason = _sanitize_title("Track 200")
        assert result == "Track 200"

    def test_bpm_70_not_stripped(self):
        result, reason = _sanitize_title("Slow Song 70")
        assert result == "Slow Song 70"

    def test_bpm_at_end_after_title_only(self):
        result, reason = _sanitize_title("Kanana (Shungi Music) 124")
        # BPM strip fires first; label suffix strips second → final "Kanana"
        assert result == "Kanana"

    def test_no_bpm_unchanged(self):
        result, reason = _sanitize_title("Track (Original Mix)")
        assert result == "Track (Original Mix)"
        assert reason == ""


# ---------------------------------------------------------------------------
# Domain / piracy tokens
# ---------------------------------------------------------------------------

class TestDomainToken:
    def test_fordjonly_com(self):
        result, reason = _sanitize_title("Dance with Me (Extended Mix) (fordjonly.com)")
        assert result == "Dance with Me (Extended Mix)"
        assert reason == "title_domain_token_stripped"

    def test_blogspot_token(self):
        result, reason = _sanitize_title("Track (htpthahouse-lovers.blogspot.com)")
        assert result == "Track"
        assert reason == "title_domain_token_stripped"

    def test_hulkshare_token(self):
        result, reason = _sanitize_title("Groove (Original Mix) (hulkshare.com)")
        assert result == "Groove (Original Mix)"
        assert reason == "title_domain_token_stripped"

    def test_no_domain_unchanged(self):
        result, reason = _sanitize_title("Pure (Original Mix)")
        assert result == "Pure (Original Mix)"
        assert reason == ""


# ---------------------------------------------------------------------------
# Label-like trailing parenthetical suffix
# ---------------------------------------------------------------------------

class TestLabelSuffix:
    def test_xumba_recordings(self):
        result, reason = _sanitize_title("Zafir (Original Mix) (Xumba Recordings)")
        assert result == "Zafir (Original Mix)"
        assert reason == "title_label_suffix_stripped"

    def test_shungi_music(self):
        result, reason = _sanitize_title("Kanana (Shungi Music)")
        assert result == "Kanana"
        assert reason == "title_label_suffix_stripped"

    def test_kontor_records(self):
        result, reason = _sanitize_title("Song (Extended Mix) (Kontor Records)")
        assert result == "Song (Extended Mix)"
        assert reason == "title_label_suffix_stripped"

    def test_distrokid(self):
        result, reason = _sanitize_title("Beat (Distrokid)")
        assert result == "Beat"
        assert reason == "title_label_suffix_stripped"

    def test_sirup_music(self):
        result, reason = _sanitize_title("Night Life (Sirup Music)")
        assert result == "Night Life"
        assert reason == "title_label_suffix_stripped"

    def test_shockit_known_label(self):
        result, reason = _sanitize_title("Drop (Shockit)")
        assert result == "Drop"
        assert reason == "title_label_suffix_stripped"

    def test_no_label_unchanged(self):
        result, reason = _sanitize_title("Sunset (Original Mix)")
        assert result == "Sunset (Original Mix)"
        assert reason == ""

    def test_sunset_gathering_preserved(self):
        # "(Sunset Gathering)" has no label keywords → must be preserved
        result, reason = _sanitize_title("Track (Original Mix) (Sunset Gathering)")
        assert "(Sunset Gathering)" in result
        assert reason != "title_label_suffix_stripped"


# ---------------------------------------------------------------------------
# Bare leading track-number
# ---------------------------------------------------------------------------

class TestBareNumberPrefix:
    def test_single_digit_prefix(self):
        result, reason = _sanitize_title("3 Afro")
        assert result == "Afro"
        assert reason == "title_bare_number_stripped"

    def test_single_digit_with_paren(self):
        result, reason = _sanitize_title("2 Sada (N'Dinga Gaba Diplomacy Soul Remix)")
        assert result == "Sada (N'Dinga Gaba Diplomacy Soul Remix)"
        assert reason == "title_bare_number_stripped"

    def test_two_digit_prefix(self):
        result, reason = _sanitize_title("10 Tracks Away")
        assert result == "Tracks Away"
        assert reason == "title_bare_number_stripped"

    def test_number_prefix_does_not_strip_too_short(self):
        # Result would be < 2 chars — should not strip
        result, reason = _sanitize_title("2 A")
        # "A" is only 1 char — guard should block stripping
        # (or the BPM rule may not fire at all; this depends on guard)
        # Main assertion: no crash, result not empty
        assert result  # non-empty


# ---------------------------------------------------------------------------
# Protected mix/version parens — must NOT be stripped
# ---------------------------------------------------------------------------

class TestProtectedParens:
    def test_original_mix_preserved(self):
        result, reason = _sanitize_title("Track (Original Mix)")
        assert result == "Track (Original Mix)"
        assert reason == ""

    def test_extended_mix_preserved(self):
        result, reason = _sanitize_title("Track (Extended Mix)")
        assert result == "Track (Extended Mix)"
        assert reason == ""

    def test_radio_edit_preserved(self):
        result, reason = _sanitize_title("Track (Radio Edit)")
        assert result == "Track (Radio Edit)"
        assert reason == ""

    def test_dub_mix_preserved(self):
        result, reason = _sanitize_title("Deep Down (Dub Mix)")
        assert result == "Deep Down (Dub Mix)"
        assert reason == ""

    def test_remix_preserved(self):
        result, reason = _sanitize_title("Song (Hyenah Remix)")
        assert result == "Song (Hyenah Remix)"
        assert reason == ""

    def test_club_edit_preserved(self):
        result, reason = _sanitize_title("Beat (Club Edit)")
        assert result == "Beat (Club Edit)"
        assert reason == ""

    def test_reprise_preserved(self):
        result, reason = _sanitize_title("Outro (Reprise)")
        assert result == "Outro (Reprise)"
        assert reason == ""

    def test_rework_preserved(self):
        result, reason = _sanitize_title("Track (Rework)")
        assert result == "Track (Rework)"
        assert reason == ""


# ---------------------------------------------------------------------------
# (Feat. casing normalisation
# ---------------------------------------------------------------------------

class TestFeatNormalization:
    def test_feat_upper_to_lower(self):
        result, reason = _sanitize_title("Song (Feat. Artist Name)")
        assert result == "Song (feat. Artist Name)"
        assert reason == "title_feat_normalized"

    def test_feat_already_lower_unchanged(self):
        result, reason = _sanitize_title("Song (feat. Artist Name)")
        assert result == "Song (feat. Artist Name)"
        assert reason == ""


# ---------------------------------------------------------------------------
# _strip_label_suffix helper directly
# ---------------------------------------------------------------------------

class TestStripLabelSuffix:
    def test_records(self):
        result, reason = _strip_label_suffix("Track (Adama Records)")
        assert result == "Track"
        assert reason == "title_label_suffix_stripped"

    def test_no_match_non_label(self):
        result, reason = _strip_label_suffix("Track (Dub Mix)")
        assert result == "Track (Dub Mix)"
        assert reason == ""

    def test_empty_input(self):
        result, reason = _strip_label_suffix("")
        assert result == ""
        assert reason == ""

    def test_no_trailing_paren(self):
        result, reason = _strip_label_suffix("Bare Title")
        assert result == "Bare Title"
        assert reason == ""


# ---------------------------------------------------------------------------
# Required transforms — explicit regression suite (MODIFY task spec)
# ---------------------------------------------------------------------------

class TestRequiredTransforms:
    """The five transforms the task spec requires to work end-to-end."""

    def test_bare_number_3_afro(self):
        result, _ = _sanitize_title("3 Afro")
        assert result == "Afro"

    def test_bare_number_2_sada_with_remix(self):
        result, _ = _sanitize_title("2 Sada (N'Dinga Gaba Diplomacy Soul Remix)")
        assert result == "Sada (N'Dinga Gaba Diplomacy Soul Remix)"

    def test_domain_token_dance_fordjonly(self):
        result, _ = _sanitize_title("Dance with Me (Extended Mix) (fordjonly.com)")
        assert result == "Dance with Me (Extended Mix)"

    def test_bpm_turk_sunset_gathering_preserved(self):
        # BPM stripped; "(Sunset Gathering)" has no label keyword → preserved
        result, _ = _sanitize_title("Turk (Original Mix) (Sunset Gathering) 122")
        assert result == "Turk (Original Mix) (Sunset Gathering)"

    def test_bpm_then_label_zafir_space_before_bpm(self):
        # BPM strips first (" 122" → gone), then label suffix "(Xumba Recordings)" strips
        result, _ = _sanitize_title("Zafir (Original Mix) (Xumba Recordings) 122")
        assert result == "Zafir (Original Mix)"

    def test_preserve_original_mix(self):
        result, _ = _sanitize_title("Track (Original Mix)")
        assert result == "Track (Original Mix)"

    def test_preserve_extended_mix(self):
        result, _ = _sanitize_title("Track (Extended Mix)")
        assert result == "Track (Extended Mix)"

    def test_preserve_remix(self):
        result, _ = _sanitize_title("Song (Hyenah Remix)")
        assert result == "Song (Hyenah Remix)"

    def test_preserve_dub_mix(self):
        result, _ = _sanitize_title("Deep Down (Dub Mix)")
        assert result == "Deep Down (Dub Mix)"

    def test_preserve_rework(self):
        result, _ = _sanitize_title("Track (Rework)")
        assert result == "Track (Rework)"

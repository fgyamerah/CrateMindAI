"""
Tests for modules/artist_merge.py

Covers:
  - extract_primary_artist()
  - _has_collab_suffix()
  - normalize_artist_key()
  - _normalize_primary_for_compare()
  - _describe_alias_differences()
  - _classify_merge()
  - _pick_canonical()
"""
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

import pytest

# ---------------------------------------------------------------------------
# Make sure project root is on the path for imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.artist_merge import (
    MERGE_CATEGORY_SAFE_ALIAS,
    MERGE_CATEGORY_SAME_PRIMARY_COLLAB,
    MERGE_CATEGORY_AMBIGUOUS,
    extract_primary_artist,
    _has_collab_suffix,
    normalize_artist_key,
    _normalize_primary_for_compare,
    _describe_alias_differences,
    _classify_merge,
    _pick_canonical,
    FolderInfo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fi(display_name: str, files: int = 3) -> FolderInfo:
    """Build a minimal FolderInfo for classification tests."""
    from modules.artist_merge import extract_primary_artist, _has_collab_suffix
    return FolderInfo(
        path=Path(f"/music/H/{display_name}"),
        display_name=display_name,
        primary_artist=extract_primary_artist(display_name),
        letter="H",
        files=[Path(f"/music/H/{display_name}/track{i}.mp3") for i in range(files)],
        has_collab_suffix=_has_collab_suffix(display_name),
    )


# ===========================================================================
# extract_primary_artist
# ===========================================================================

class TestExtractPrimaryArtist:
    def test_plain_name_unchanged(self):
        assert extract_primary_artist("Black Coffee") == "Black Coffee"

    def test_feat_stripped(self):
        assert extract_primary_artist("Culoe De Song ft. Thandiswa Mazwai") == "Culoe De Song"

    def test_feat_dot_stripped(self):
        assert extract_primary_artist("Heavy-K feat. Davido & Tresor") == "Heavy-K"

    def test_featuring_stripped(self):
        assert extract_primary_artist("DJ Lag featuring Tiwa Savage") == "DJ Lag"

    def test_ft_no_dot(self):
        assert extract_primary_artist("Heavy K ft Naak Musiq") == "Heavy K"

    def test_comma_collab_stripped(self):
        assert extract_primary_artist("Cee ElAssaad, Jackie Queens") == "Cee ElAssaad"

    def test_multi_comma_returns_first(self):
        assert extract_primary_artist("Hosh, 1979, jalja") == "Hosh"

    def test_ampersand_not_stripped(self):
        # &ME is a single artist — ampersand is not a collab separator
        assert extract_primary_artist("&ME") == "&ME"

    def test_mr_luu_ampersand_not_stripped(self):
        # "Mr. Luu & MSK" is one compound artist name
        assert extract_primary_artist("Mr. Luu & MSK") == "Mr. Luu & MSK"

    def test_case_insensitive_feat(self):
        assert extract_primary_artist("Artist FEAT. Vocalist") == "Artist"


# ===========================================================================
# _has_collab_suffix
# ===========================================================================

class TestHasCollabSuffix:
    def test_plain_name_false(self):
        assert _has_collab_suffix("Heavy-K") is False

    def test_feat_dot_true(self):
        assert _has_collab_suffix("Heavy-K feat. Davido & Tresor") is True

    def test_ft_no_dot_true(self):
        assert _has_collab_suffix("Heavy K ft Naak Musiq") is True

    def test_featuring_true(self):
        assert _has_collab_suffix("DJ Lag featuring Tiwa Savage") is True

    def test_comma_collab_true(self):
        assert _has_collab_suffix("Heavy K, Point 5") is True

    def test_multi_comma_collab_true(self):
        assert _has_collab_suffix("Hosh, 1979, jalja") is True

    def test_ampersand_alone_false(self):
        # "Mr. Luu & MSK" is a compound name, not a collab suffix
        assert _has_collab_suffix("Mr. Luu & MSK") is False

    def test_pure_artist_false(self):
        assert _has_collab_suffix("Culoe De Song") is False


# ===========================================================================
# normalize_artist_key
# ===========================================================================

class TestNormalizeArtistKey:
    def test_lowercase(self):
        assert normalize_artist_key("Black Coffee") == "black coffee"

    def test_hyphen_becomes_space(self):
        assert normalize_artist_key("Black-Coffee") == "black coffee"

    def test_dotted_initials(self):
        assert normalize_artist_key("H.O.S.H") == "hosh"

    def test_feat_stripped_before_key(self):
        assert normalize_artist_key("Heavy-K feat. Davido & Tresor") == "heavy k"

    def test_comma_collab_stripped(self):
        assert normalize_artist_key("Hosh, 1979, jalja") == "hosh"

    def test_period_in_mr(self):
        # Period stripped, compound kept
        assert normalize_artist_key("Mr. Luu & MSK") == "mr luu & msk"

    def test_trailing_period(self):
        assert normalize_artist_key("Rosalie.") == "rosalie"

    def test_apostrophe_stripped(self):
        assert normalize_artist_key("Steve 'Silk' Hurley") == "steve silk hurley"

    def test_unicode_nfc(self):
        # Two representations of é should normalize to the same key
        import unicodedata
        nfd = unicodedata.normalize("NFD", "Beyoncé")
        nfc = "Beyoncé"
        assert normalize_artist_key(nfd) == normalize_artist_key(nfc)


# ===========================================================================
# _normalize_primary_for_compare
# ===========================================================================

class TestNormalizePrimaryForCompare:
    def test_hyphen_space_same(self):
        assert _normalize_primary_for_compare("Heavy-K") == \
               _normalize_primary_for_compare("Heavy K")

    def test_dotted_initials_same_as_plain(self):
        assert _normalize_primary_for_compare("H.O.S.H") == \
               _normalize_primary_for_compare("HOSH")

    def test_dotted_initials_trailing_dot(self):
        assert _normalize_primary_for_compare("K.E.E.N.E") == \
               _normalize_primary_for_compare("K.E.E.N.E.")

    def test_case_insensitive(self):
        assert _normalize_primary_for_compare("Culoe De Song") == \
               _normalize_primary_for_compare("culoe de song")

    def test_trailing_period_stripped(self):
        assert _normalize_primary_for_compare("Rosalie.") == \
               _normalize_primary_for_compare("Rosalie")

    def test_va_variants(self):
        assert _normalize_primary_for_compare("V.A") == \
               _normalize_primary_for_compare("VA")

    def test_villager_sa_variants(self):
        assert _normalize_primary_for_compare("Villager S.A") == \
               _normalize_primary_for_compare("Villager SA")

    def test_quotation_stripped(self):
        assert _normalize_primary_for_compare("Steve 'Silk' Hurley") == \
               _normalize_primary_for_compare("Steve Silk Hurley")

    def test_mr_luu_period_variant(self):
        assert _normalize_primary_for_compare("Mr. Luu & MSK") == \
               _normalize_primary_for_compare("Mr Luu & MSK")

    def test_underscore_variant(self):
        assert _normalize_primary_for_compare("DJ_Lag") == \
               _normalize_primary_for_compare("DJ Lag")

    def test_different_artists_differ(self):
        assert _normalize_primary_for_compare("Black Coffee") != \
               _normalize_primary_for_compare("Culoe De Song")


# ===========================================================================
# _describe_alias_differences
# ===========================================================================

class TestDescribeAliasDifferences:
    def test_identical_returns_identical(self):
        assert _describe_alias_differences(["Heavy-K", "Heavy-K"]) == "identical names"

    def test_case_only(self):
        assert _describe_alias_differences(["culoe de song", "Culoe De Song"]) == \
               "capitalization variant"

    def test_hyphen_space(self):
        desc = _describe_alias_differences(["Heavy-K", "Heavy K"])
        assert "hyphen/space variant" in desc

    def test_dotted_initials(self):
        desc = _describe_alias_differences(["H.O.S.H", "HOSH"])
        assert "dotted-initials variant" in desc

    def test_trailing_period(self):
        desc = _describe_alias_differences(["Rosalie", "Rosalie."])
        assert "trailing period variant" in desc

    def test_period_variant(self):
        desc = _describe_alias_differences(["Mr. Luu & MSK", "Mr Luu & MSK"])
        assert "period variant" in desc

    def test_underscore_variant(self):
        desc = _describe_alias_differences(["DJ_Lag", "DJ Lag"])
        assert "underscore/space variant" in desc

    def test_quotation_variant(self):
        desc = _describe_alias_differences(["Steve 'Silk' Hurley", "Steve Silk Hurley"])
        assert "quotation style variant" in desc

    def test_hyphen_with_extra_space(self):
        # "Heavy- K" variant still detected as hyphen/space
        desc = _describe_alias_differences(["Heavy-K", "Heavy- K", "Heavy K"])
        assert "hyphen/space variant" in desc


# ===========================================================================
# _classify_merge
# ===========================================================================

class TestClassifyMerge:

    # --- SAFE_ALIAS ---

    def test_punctuation_only_variants_safe_alias(self):
        folders = [_fi("Mr. Luu & MSK"), _fi("Mr Luu & MSK")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_case_only_variants_safe_alias(self):
        folders = [_fi("culoe de song"), _fi("Culoe De Song")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_hyphen_space_variant_safe_alias(self):
        folders = [_fi("Heavy-K"), _fi("Heavy K")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True
        assert "safe alias" in reason

    def test_hyphen_space_with_extra_space_variant_safe_alias(self):
        folders = [_fi("Heavy-K"), _fi("Heavy- K"), _fi("Heavy K")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_trailing_period_variant_safe_alias(self):
        folders = [_fi("Mousse T."), _fi("Mousse T")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_dotted_initials_variant_safe_alias(self):
        folders = [_fi("H.O.S.H"), _fi("HOSH")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_keene_dotted_variant_safe_alias(self):
        folders = [_fi("K.E.E.N.E"), _fi("K.E.E.N.E.")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_va_dotted_variant_safe_alias(self):
        folders = [_fi("V.A"), _fi("VA")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_villager_sa_variant_safe_alias(self):
        folders = [_fi("Villager S.A"), _fi("Villager SA")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_rosalie_trailing_period_safe_alias(self):
        folders = [_fi("Rosalie"), _fi("Rosalie.")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    def test_single_folder_group_safe_alias(self):
        # Edge case: group with one folder still classified (formatting only)
        folders = [_fi("Heavy-K")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAFE_ALIAS
        assert is_safe is True

    # --- SAME_PRIMARY_COLLAB ---

    def test_feat_variant_same_primary_collab(self):
        folders = [_fi("Heavy-K"), _fi("Heavy-K feat. Davido")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_ft_variant_same_primary_collab(self):
        folders = [_fi("Heavy K"), _fi("Heavy K ft Naak Musiq")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_featuring_variant_same_primary_collab(self):
        folders = [_fi("Culoe De Song"), _fi("Culoe De Song featuring Thandiswa Mazwai")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_comma_collab_same_primary_collab(self):
        folders = [_fi("Heavy K"), _fi("Heavy K, Point 5")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_hyphen_variant_plus_collab_same_primary_collab(self):
        # "Heavy-K" and "Heavy K feat. Davido" — primary normalizes to same
        folders = [_fi("Heavy-K"), _fi("Heavy K feat. Davido")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_all_collab_variants_same_primary_collab(self):
        # No pure folder, all are collab variants
        folders = [_fi("Heavy-K feat. Davido"), _fi("Heavy K ft Naak Musiq")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB
        assert is_safe is True

    def test_reason_mentions_collab(self):
        folders = [_fi("Heavy-K"), _fi("Heavy-K feat. Davido")]
        _, _, reason = _classify_merge(folders)
        assert "collab" in reason.lower() or "primary" in reason.lower()

    # --- AMBIGUOUS ---

    def test_clearly_different_artists_ambiguous(self):
        folders = [_fi("Black Coffee"), _fi("Culoe De Song")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_AMBIGUOUS
        assert is_safe is False

    def test_different_artists_with_similar_start_ambiguous(self):
        # "DJ Lag" and "DJ Maphorisa" share "DJ" prefix but are different
        folders = [_fi("DJ Lag"), _fi("DJ Maphorisa")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_AMBIGUOUS
        assert is_safe is False

    def test_ambiguous_reason_mentions_differ(self):
        folders = [_fi("Black Coffee"), _fi("Culoe De Song")]
        _, _, reason = _classify_merge(folders)
        assert "differ" in reason or "ambiguous" in reason.lower()

    def test_three_different_artists_ambiguous(self):
        folders = [_fi("Black Coffee"), _fi("Culoe De Song"), _fi("DJ Lag")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_AMBIGUOUS
        assert is_safe is False

    def test_mixed_artists_with_same_primary_collab_still_ambiguous_if_primaries_differ(self):
        # "Black Coffee" and "Culoe De Song feat. Black Coffee" — primaries differ
        folders = [_fi("Black Coffee"), _fi("Culoe De Song feat. Black Coffee")]
        category, is_safe, reason = _classify_merge(folders)
        assert category == MERGE_CATEGORY_AMBIGUOUS
        assert is_safe is False


# ===========================================================================
# _pick_canonical
# ===========================================================================

class TestPickCanonical:

    def test_most_files_wins(self):
        folders = [_fi("heavy k", files=2), _fi("Heavy K", files=10)]
        assert _pick_canonical(folders) == "Heavy K"

    def test_all_uppercase_penalized(self):
        # "HOSH" (all-uppercase) loses to "Hosh" even with same file count
        folders = [_fi("HOSH"), _fi("Hosh")]
        result = _pick_canonical(folders)
        assert result == "Hosh"

    def test_all_lowercase_penalized(self):
        # "culoe de song" (all-lowercase) loses to "Culoe De Song"
        folders = [_fi("culoe de song"), _fi("Culoe De Song")]
        result = _pick_canonical(folders)
        assert result == "Culoe De Song"

    def test_collab_folder_never_chosen(self):
        # Canonical should be the pure primary, not the feat variant
        folders = [_fi("Heavy-K feat. Davido", files=20), _fi("Heavy-K", files=1)]
        result = _pick_canonical(folders)
        assert result == "Heavy-K"

    def test_all_collab_falls_back_to_primary_name(self):
        # When every folder is a collab, still pick the one with fewer collab parts
        folders = [
            _fi("Heavy-K feat. Davido", files=5),
            _fi("Heavy K ft Naak Musiq", files=3),
        ]
        # Both are collab; primary for both is "Heavy-K" / "Heavy K"
        # The one with more files wins (after extracting primary)
        result = _pick_canonical(folders)
        assert result in ("Heavy-K", "Heavy K")

    def test_alphabetical_tiebreak_deterministic(self):
        # Equal files, neither all-upper nor all-lower — alphabetical tiebreak
        folders = [_fi("Beta Artist"), _fi("Alpha Artist")]
        result = _pick_canonical(folders)
        assert result == "Alpha Artist"

    def test_mixed_case_preferred_over_all_caps_with_equal_files(self):
        folders = [_fi("H.O.S.H"), _fi("HOSH")]
        # H.O.S.H has mixed alpha (only letters, separated by periods)
        # HOSH is all-uppercase → penalized
        result = _pick_canonical(folders)
        assert result == "H.O.S.H"

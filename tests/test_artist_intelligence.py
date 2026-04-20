"""
tests/test_artist_intelligence.py — Unit tests for intelligence/artist/

Covers:
  - normalize_artist_string (feat variants, pollution, idempotency)
  - parse_artist_string (splitting, feat extraction, Heavy-K identity)
  - _try_personal_name_split (multi-artist heuristic)
  - ArtistAliasStore (lookup_with_method, conservative alias, unknown artist)
  - _propose_artist (direct == comparison, feat normalization surfaced)
  - _compute_change_reasons (all reason tokens)
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from intelligence.artist.artist_normalizer import normalize_artist_string
from intelligence.artist.artist_parser import (
    parse_artist_string,
    _try_personal_name_split,
    _is_personal_name_part,
)
from intelligence.artist.artist_alias_store import ArtistAliasStore
from intelligence.artist.artist_schema import ArtistParseResult, ArtistEntity
from intelligence.artist.runner import (
    _propose_artist,
    _compute_change_reasons,
    REASON_FEAT_NORMALIZED,
    REASON_FEAT_DEDUPED,
    REASON_ALIAS_RESOLVED,
    REASON_ALIAS_NORMALIZED,
    REASON_SEPARATOR_NORMALIZED,
    REASON_SPACING_FIXED,
    REASON_POLLUTION_REMOVED,
    REASON_MULTI_ARTIST_SPLIT,
    REASON_SPLIT_AMBIGUOUS,
    REASON_NESTED_SPLIT,
    REASON_CASING_NORMALIZED,
    REASON_CASING_SKIPPED_ACRONYM,
)


# ===========================================================================
# normalize_artist_string
# ===========================================================================

class TestNormalizeArtistString:
    def test_ft_dot_normalized_to_feat(self):
        assert normalize_artist_string("Black Coffee ft. Soulstar") == "Black Coffee feat. Soulstar"

    def test_featuring_normalized_to_feat(self):
        assert normalize_artist_string("DJ Lag featuring Tiwa Savage") == "DJ Lag feat. Tiwa Savage"

    def test_ft_without_dot_normalized(self):
        assert normalize_artist_string("Heavy K ft Naak Musiq") == "Heavy K feat. Naak Musiq"

    def test_feat_already_canonical_unchanged(self):
        assert normalize_artist_string("Artist feat. Other") == "Artist feat. Other"

    def test_clean_single_artist_unchanged(self):
        assert normalize_artist_string("Black Coffee") == "Black Coffee"

    def test_heavy_k_hyphen_preserved(self):
        # ASCII hyphen is identity character — must not be removed
        assert normalize_artist_string("Heavy-K") == "Heavy-K"

    def test_double_spaces_collapsed(self):
        assert normalize_artist_string("DJ  Maphorisa") == "DJ Maphorisa"

    def test_leading_trailing_whitespace_stripped(self):
        assert normalize_artist_string("  Black Coffee  ") == "Black Coffee"

    def test_unicode_dash_converted_to_ascii_hyphen(self):
        # en-dash (\u2013) → ASCII hyphen
        assert normalize_artist_string("Black\u2013Coffee") == "Black-Coffee"

    def test_smart_quotes_to_straight(self):
        assert normalize_artist_string("Steve \u2018Silk\u2019 Hurley") == "Steve 'Silk' Hurley"

    def test_idempotent_on_clean_name(self):
        name = "Above & Beyond feat. Richard Bedford"
        assert normalize_artist_string(normalize_artist_string(name)) == normalize_artist_string(name)

    def test_feat_missing_space_after_dot_fixed(self):
        assert normalize_artist_string("Artist feat.Other") == "Artist feat. Other"


# ===========================================================================
# parse_artist_string
# ===========================================================================

class TestParseArtistString:

    # --- single clean artists ---

    def test_single_clean_artist(self):
        result = parse_artist_string("Black Coffee")
        assert len(result.main_artists) == 1
        assert result.main_artists[0].normalized == "Black Coffee"
        assert result.confidence == 1.0

    def test_heavy_k_hyphen_single_artist(self):
        # "Heavy-K" must remain one entity — hyphen is part of identity, not a separator
        result = parse_artist_string("Heavy-K")
        assert len(result.main_artists) == 1
        assert result.main_artists[0].normalized == "Heavy-K"
        assert result.confidence == 1.0

    # --- feat extraction ---

    def test_feat_extracted_from_artist_when_title_clean(self):
        result = parse_artist_string("DJ Lag ft. Tiwa Savage", current_title="Track Name")
        assert len(result.main_artists) == 1
        assert result.main_artists[0].normalized == "DJ Lag"
        assert "Tiwa Savage" in result.featured_artists

    def test_featuring_extracted_from_artist(self):
        result = parse_artist_string("Heavy-K featuring Davido", current_title="Track")
        assert result.main_artists[0].normalized == "Heavy-K"
        assert "Davido" in result.featured_artists

    def test_feat_not_extracted_when_title_has_feat(self):
        # Title carries the feat — artist field feat is left alone
        result = parse_artist_string(
            "DJ Lag ft. Tiwa Savage",
            current_title="Track (feat. Tiwa Savage)",
        )
        assert result.featured_artists == []
        assert len(result.main_artists) == 1
        # Normalized form has feat. inside the single entity
        assert "feat." in result.main_artists[0].normalized

    # --- multi-artist splitting ---

    def test_comma_split_two_artists(self):
        result = parse_artist_string("Black Coffee, Culoe De Song")
        assert len(result.main_artists) == 2
        names = [e.normalized for e in result.main_artists]
        assert "Black Coffee" in names
        assert "Culoe De Song" in names

    def test_ampersand_split_two_artists(self):
        result = parse_artist_string("Hosh & Adana Twins")
        assert len(result.main_artists) == 2

    def test_x_separator_split(self):
        result = parse_artist_string("Russ Yallop x David Hasert")
        assert len(result.main_artists) == 2

    def test_vs_separator_split(self):
        result = parse_artist_string("Louie Vega vs. Masters At Work")
        assert len(result.main_artists) == 2

    def test_multi_artist_not_merged_into_one(self):
        # Core safety: multiple artists must never be flattened
        result = parse_artist_string("Black Coffee, Culoe De Song, DJ Lag")
        assert len(result.main_artists) == 3

    def test_heavy_k_with_feat_and_collab(self):
        # "Heavy-K feat. Davido" — single main artist plus feat
        result = parse_artist_string("Heavy-K feat. Davido", "Track Title")
        assert len(result.main_artists) == 1
        assert result.main_artists[0].normalized == "Heavy-K"
        assert result.featured_artists == ["Davido"]

    def test_above_and_beyond_conservative_and_split(self):
        # "Above & Beyond" uses & — will be split; but "and" inside a name is kept
        result = parse_artist_string("Above & Beyond")
        # & splits — this is the correct behaviour (two named artists when & present)
        # "Above and Beyond" (with "and") should be kept whole:
        result2 = parse_artist_string("Above and Beyond")
        # "and" split is only done when BOTH sides look like valid artist names
        # "Above" and "Beyond" are ambiguous short words — result may vary but
        # must not crash
        assert isinstance(result2, ArtistParseResult)


# ===========================================================================
# ArtistAliasStore — lookup_with_method and conservative alias behaviour
# ===========================================================================

@pytest.fixture
def alias_store(tmp_path):
    store_path = tmp_path / "aliases.json"
    store_path.write_text(
        '{"Heavy K": ["Heavy-K", "heavy k", "heavy-k"], "DJ Lag": []}',
        encoding="utf-8",
    )
    return ArtistAliasStore(store_path)


class TestArtistAliasStore:

    def test_lookup_heavy_k_hyphen_variant(self, alias_store):
        assert alias_store.lookup_any("Heavy-K") == "Heavy K"

    def test_lookup_heavy_k_lowercase_variant(self, alias_store):
        assert alias_store.lookup_any("heavy k") == "Heavy K"

    def test_canonical_resolves_to_itself(self, alias_store):
        assert alias_store.lookup_any("Heavy K") == "Heavy K"

    def test_unknown_artist_returns_none(self, alias_store):
        assert alias_store.lookup_any("Totally Unknown Artist") is None
        assert alias_store.lookup_with_method("Totally Unknown Artist") is None

    def test_lookup_with_method_normalized(self, alias_store):
        result = alias_store.lookup_with_method("Heavy-K")
        assert result is not None
        canonical, method = result
        assert canonical == "Heavy K"
        assert method in ("normalized", "exact")

    def test_lookup_with_method_canonical_self(self, alias_store):
        result = alias_store.lookup_with_method("Heavy K")
        assert result is not None
        canonical, method = result
        assert canonical == "Heavy K"

    def test_lookup_with_method_ci_only(self, alias_store):
        # "DJ Lag" has no variants; "DJ lag" (wrong case) falls through to ci
        result = alias_store.lookup_with_method("DJ lag")
        assert result is not None
        canonical, method = result
        assert canonical == "DJ Lag"
        # Should be ci or normalized (normalize lowercases and both normalize the same)
        # Either is acceptable — the key is we get a match
        assert method in ("ci", "normalized", "exact")

    def test_empty_store_returns_none(self, tmp_path):
        store_path = tmp_path / "empty.json"
        store_path.write_text("{}", encoding="utf-8")
        store = ArtistAliasStore(store_path)
        assert store.lookup_with_method("Heavy K") is None

    def test_missing_store_file_graceful(self, tmp_path):
        store = ArtistAliasStore(tmp_path / "nonexistent.json")
        assert store.lookup_any("Heavy K") is None
        assert len(store) == 0


# ===========================================================================
# _propose_artist — direct == comparison (feat normalization surfaced)
# ===========================================================================

def _make_result(artist_str: str, title: str = "") -> ArtistParseResult:
    return parse_artist_string(artist_str, current_title=title)


class TestProposeArtist:

    def test_clean_tag_no_change(self):
        result = _make_result("Black Coffee")
        assert _propose_artist("Black Coffee", result) is None

    def test_feat_ft_dot_change_proposed(self):
        # "ft." → "feat." should be surfaced, not suppressed
        result = _make_result("Black Coffee ft. Soulstar", title="Track")
        proposed = _propose_artist("Black Coffee ft. Soulstar", result)
        assert proposed is not None
        assert "feat." in proposed

    def test_feat_featuring_change_proposed(self):
        result = _make_result("DJ Lag featuring Tiwa Savage", title="Track")
        proposed = _propose_artist("DJ Lag featuring Tiwa Savage", result)
        assert proposed is not None
        assert "feat." in proposed

    def test_already_canonical_feat_no_change(self):
        result = _make_result("DJ Lag feat. Tiwa Savage", title="Track")
        proposed = _propose_artist("DJ Lag feat. Tiwa Savage", result)
        assert proposed is None

    def test_multi_artist_unchanged_when_same(self):
        result = _make_result("Black Coffee, Culoe De Song")
        proposed = _propose_artist("Black Coffee, Culoe De Song", result)
        assert proposed is None

    def test_empty_artist_returns_none(self):
        result = _make_result("")
        assert _propose_artist("", result) is None

    def test_no_main_artists_returns_none(self):
        result = ArtistParseResult(main_artists=[], confidence=0.0, notes="empty")
        assert _propose_artist("Something", result) is None


# ===========================================================================
# _compute_change_reasons
# ===========================================================================

class TestComputeChangeReasons:

    def test_feat_normalized_reason(self):
        result = _make_result("Black Coffee ft. Soulstar", title="Track")
        reasons = _compute_change_reasons(
            "Black Coffee ft. Soulstar",
            "Black Coffee feat. Soulstar",
            result,
        )
        assert REASON_FEAT_NORMALIZED in reasons

    def test_feat_featuring_normalized_reason(self):
        result = _make_result("DJ Lag featuring Tiwa Savage", title="Track")
        reasons = _compute_change_reasons(
            "DJ Lag featuring Tiwa Savage",
            "DJ Lag feat. Tiwa Savage",
            result,
        )
        assert REASON_FEAT_NORMALIZED in reasons

    def test_alias_resolved_reason(self, alias_store):
        result = _make_result("Heavy-K")
        for entity in result.main_artists:
            match = alias_store.lookup_with_method(entity.normalized)
            if match:
                canonical, method = match
                entity.canonical = canonical
                entity.source    = f"alias_store:{method}"
        reasons = _compute_change_reasons("Heavy-K", "Heavy K", result)
        assert REASON_ALIAS_RESOLVED in reasons

    def test_separator_normalized_reason_ampersand_to_comma(self):
        result = _make_result("Black Coffee & Culoe De Song")
        reasons = _compute_change_reasons(
            "Black Coffee & Culoe De Song",
            "Black Coffee, Culoe De Song",
            result,
        )
        assert REASON_SEPARATOR_NORMALIZED in reasons

    def test_spacing_fixed_reason(self):
        result = _make_result("Black Coffee , Culoe De Song")
        reasons = _compute_change_reasons(
            "Black Coffee , Culoe De Song",
            "Black Coffee, Culoe De Song",
            result,
        )
        # Punctuation stripped both become "BlackCoffeeCuloeDeSong" — same → spacing_fixed
        assert REASON_SPACING_FIXED in reasons

    def test_ci_alias_does_not_produce_alias_resolved(self, alias_store):
        # CI-only matches should not generate alias_resolved — they stay as ambiguous
        result = _make_result("dj lag")  # lowercase
        for entity in result.main_artists:
            match = alias_store.lookup_with_method(entity.normalized)
            if match:
                canonical, method = match
                entity.canonical = canonical
                entity.source    = f"alias_store:{method}"
        # If method was ci, alias_resolved must not appear
        reasons = _compute_change_reasons("dj lag", "DJ Lag", result)
        has_ci = any(
            e.source.endswith(":ci")
            for e in result.main_artists
            if e.source.startswith("alias_store")
        )
        if has_ci:
            assert REASON_ALIAS_RESOLVED not in reasons

    def test_no_duplicate_reasons(self):
        result = _make_result("Artist ft. Feat")
        reasons = _compute_change_reasons(
            "Artist ft. Feat",
            "Artist feat. Feat",
            result,
        )
        assert len(reasons) == len(set(reasons))


# ===========================================================================
# normalize_artist_string — pollution guards (new steps 5b / 6 / 7)
# ===========================================================================

class TestNormalizerPollution:

    def test_bpm_token_stripped(self):
        assert normalize_artist_string("Black Coffee 128 BPM") == "Black Coffee"

    def test_bpm_at_prefix_stripped(self):
        assert normalize_artist_string("Black Coffee @ 130 BPM") == "Black Coffee"

    def test_bpm_lowercase_stripped(self):
        assert normalize_artist_string("Black Coffee 128bpm") == "Black Coffee"

    def test_version_bracket_square_stripped(self):
        assert normalize_artist_string("Artist [Original Mix]") == "Artist"

    def test_version_bracket_paren_stripped(self):
        assert normalize_artist_string("Artist (Extended Mix)") == "Artist"

    def test_radio_edit_bracket_stripped(self):
        assert normalize_artist_string("Artist [Radio Edit]") == "Artist"

    def test_clean_artist_not_affected(self):
        assert normalize_artist_string("Black Coffee") == "Black Coffee"

    def test_feat_duplicate_collapsed(self):
        result = normalize_artist_string("Artist feat. feat. Another")
        assert result.count("feat.") == 1
        assert "Another" in result

    def test_feat_featuring_then_feat_collapsed(self):
        # "featuring" normalizes to "feat." first; then duplicate collapses
        result = normalize_artist_string("Artist featuring feat. Another")
        assert result.count("feat.") == 1

    def test_idempotent_after_pollution_removal(self):
        cleaned = normalize_artist_string("Black Coffee 128 BPM")
        assert normalize_artist_string(cleaned) == cleaned


# ===========================================================================
# _is_personal_name_part
# ===========================================================================

class TestIsPersonalNamePart:

    def test_two_word_name(self):
        assert _is_personal_name_part("Mark Francis") is True

    def test_name_with_initial(self):
        assert _is_personal_name_part("Aaron K Gray") is True

    def test_single_word_dj_name(self):
        assert _is_personal_name_part("Keinemusik") is True

    def test_short_single_word(self):
        assert _is_personal_name_part("Hosh") is True

    def test_rejects_article_the(self):
        assert _is_personal_name_part("The") is False

    def test_rejects_presents(self):
        assert _is_personal_name_part("Presents") is False

    def test_rejects_featuring(self):
        assert _is_personal_name_part("Featuring Artist") is False

    def test_rejects_all_caps_short(self):
        # "SBCR", "MAW" are abbreviations, not personal names
        assert _is_personal_name_part("SBCR") is False

    def test_rejects_number(self):
        assert _is_personal_name_part("128") is False

    def test_rejects_four_word_string(self):
        # 4-word strings are too ambiguous to classify as a single name
        assert _is_personal_name_part("Adam Port Keinemusik Stryv") is False

    def test_rejects_dj_prefix(self):
        # "DJ" is all-caps prefix — doesn't match title-case pattern
        assert _is_personal_name_part("DJ Lag") is False


# ===========================================================================
# _try_personal_name_split
# ===========================================================================

class TestPersonalNameSplit:

    def test_two_full_names(self):
        result = _try_personal_name_split("Aaron K Gray Mark Francis")
        assert result is not None
        parts, conf = result
        assert len(parts) == 2
        assert "Aaron K Gray" in parts
        assert "Mark Francis" in parts
        assert conf >= 0.80

    def test_two_simple_names(self):
        result = _try_personal_name_split("Adri Block Paul Parsons")
        assert result is not None
        parts, conf = result
        assert "Adri Block" in parts
        assert "Paul Parsons" in parts

    def test_three_words_not_attempted(self):
        # Only 3 words — minimum is 4
        assert _try_personal_name_split("Black Coffee Remix") is None

    def test_two_words_not_attempted(self):
        assert _try_personal_name_split("Black Coffee") is None

    def test_single_word_not_attempted(self):
        assert _try_personal_name_split("Hosh") is None

    def test_dj_prefix_not_split(self):
        # "DJ Lag" starts with "DJ" which fails the personal name check
        result = _try_personal_name_split("DJ Lag Black Coffee")
        # "DJ Lag" → _is_personal_name_part → False (DJ is all-caps abbreviation)
        assert result is None

    def test_ambiguous_long_string_returns_none(self):
        # "Adam Port Keinemusik Stryv Malachiii" — no clean binary personal-name split
        # "Keinemusik Stryv Malachiii" is 3 words with no initial → fails 3-word check
        result = _try_personal_name_split("Adam Port Keinemusik Stryv Malachiii")
        # May or may not find a split — the important thing is it doesn't crash
        # and if it does split, both parts must pass _is_personal_name_part
        if result is not None:
            parts, _ = result
            for p in parts:
                assert _is_personal_name_part(p), f"{p!r} failed personal name check"

    def test_clean_two_word_artist_unchanged(self):
        # "Black Coffee" — only 2 words, not attempted
        assert _try_personal_name_split("Black Coffee") is None

    def test_returns_confidence_in_valid_range(self):
        result = _try_personal_name_split("Mark Francis Aaron K Gray")
        assert result is not None
        _, conf = result
        assert 0.0 < conf <= 1.0


# ===========================================================================
# Feat dedup — normalize + parser strip
# ===========================================================================

class TestFeatDedup:

    def test_normalizer_collapses_duplicate_feat(self):
        normalized = normalize_artist_string("Artist feat. feat. Another")
        assert "feat. feat." not in normalized
        assert normalized.count("feat.") == 1

    def test_parser_strips_leading_feat_from_feat_part(self):
        # "Artist feat. feat. Another" — the extracted feat_part should be "Another"
        result = parse_artist_string("Artist feat. feat. Another", current_title="Track")
        assert len(result.main_artists) == 1
        # Featured artists should not contain a "feat." prefix
        for fa in result.featured_artists:
            assert not fa.lower().startswith("feat")

    def test_feat_deduped_reason(self):
        # Simulate the case where current had two feat tokens
        result = _make_result("Artist feat. feat. Another")
        reasons = _compute_change_reasons(
            "Artist feat. feat. Another",   # current (2 feat tokens)
            "Artist feat. Another",          # proposed (1 feat token)
            result,
        )
        assert REASON_FEAT_DEDUPED in reasons


# ===========================================================================
# Pollution removal reason
# ===========================================================================

class TestPollutionRemovedReason:

    def test_bpm_produces_pollution_reason(self):
        result = _make_result("Black Coffee 128 BPM")
        reasons = _compute_change_reasons(
            "Black Coffee 128 BPM",
            "Black Coffee",
            result,
        )
        assert REASON_POLLUTION_REMOVED in reasons

    def test_version_bracket_produces_pollution_reason(self):
        result = _make_result("Artist [Original Mix]")
        reasons = _compute_change_reasons(
            "Artist [Original Mix]",
            "Artist",
            result,
        )
        assert REASON_POLLUTION_REMOVED in reasons

    def test_clean_artist_no_pollution_reason(self):
        result = _make_result("Black Coffee")
        reasons = _compute_change_reasons(
            "Black Coffee",
            "Black Coffee",  # no change case — should not be called but safe to test
            result,
        )
        assert REASON_POLLUTION_REMOVED not in reasons


# ===========================================================================
# alias_normalized reason (case-only alias change)
# ===========================================================================

class TestAliasNormalized:

    def test_alias_normalized_reason_for_case_only(self, alias_store):
        # "heavy k" → "Heavy K" — case-only alias change
        result = _make_result("heavy k")
        for entity in result.main_artists:
            match = alias_store.lookup_with_method(entity.normalized)
            if match:
                canonical, method = match
                entity.canonical = canonical
                entity.source    = f"alias_store:{method}"
        reasons = _compute_change_reasons("heavy k", "Heavy K", result)
        # Should be alias_normalized (case only) not alias_resolved (structural)
        assert REASON_ALIAS_NORMALIZED in reasons
        assert REASON_ALIAS_RESOLVED not in reasons


# ===========================================================================
# Safety: already-clean artist tags must remain unchanged
# ===========================================================================

class TestAlreadyCleanUnchanged:

    def test_single_clean_artist_no_change(self):
        result = _make_result("Black Coffee")
        assert _propose_artist("Black Coffee", result) is None

    def test_multi_artist_with_comma_no_change(self):
        result = _make_result("Black Coffee, Culoe De Song")
        proposed = _propose_artist("Black Coffee, Culoe De Song", result)
        assert proposed is None

    def test_artist_with_feat_canonical_no_change(self):
        result = _make_result("DJ Lag feat. Tiwa Savage", title="Track")
        proposed = _propose_artist("DJ Lag feat. Tiwa Savage", result)
        assert proposed is None

    def test_heavy_k_hyphen_stays_without_alias_store(self):
        # Without alias store, "Heavy-K" stays as "Heavy-K" — no change
        result = _make_result("Heavy-K")
        proposed = _propose_artist("Heavy-K", result)
        assert proposed is None   # no alias store populated, nothing to change


# ===========================================================================
# Phase 4: Multi-level (nested) separator splitting
# ===========================================================================

class TestNestedSplit:

    def test_comma_then_ampersand_three_artists(self):
        # "AC Slater, Chris Lorenzo & Fly With Us" → 3 artists
        result = parse_artist_string("AC Slater, Chris Lorenzo & Fly With Us")
        names = [e.normalized for e in result.main_artists]
        assert len(result.main_artists) == 3
        assert "AC Slater" in names
        assert "Chris Lorenzo" in names
        assert "Fly With Us" in names

    def test_adam_port_me_rampa(self):
        # "Adam Port, M.E. & Rampa" → 3 artists
        result = parse_artist_string("Adam Port, M.E. & Rampa")
        names = [e.normalized for e in result.main_artists]
        assert len(result.main_artists) == 3
        assert "Adam Port" in names
        assert "M.E." in names
        assert "Rampa" in names

    def test_plain_comma_unchanged(self):
        # Comma without nested & → still works correctly
        result = parse_artist_string("Black Coffee, Culoe De Song, DJ Lag")
        assert len(result.main_artists) == 3

    def test_plain_ampersand_unchanged(self):
        # No comma → & still splits correctly
        result = parse_artist_string("Hosh & Adana Twins")
        assert len(result.main_artists) == 2

    def test_two_comma_segments_both_with_ampersand(self):
        # "A & B, C & D" → 4 artists
        result = parse_artist_string("Hosh & Adana Twins, Black Coffee & Culoe De Song")
        assert len(result.main_artists) == 4

    def test_nested_split_notes_recorded(self):
        # Parser notes should mention 'nested' separator
        result = parse_artist_string("AC Slater, Chris Lorenzo & Fly With Us")
        assert result.notes is not None
        assert "nested" in result.notes

    def test_nested_split_reason_in_change_reasons(self):
        result = parse_artist_string("AC Slater, Chris Lorenzo & Fly With Us")
        current = "AC Slater, Chris Lorenzo & Fly With Us"
        proposed = _propose_artist(current, result)
        if proposed is not None:
            reasons = _compute_change_reasons(current, proposed, result)
            assert REASON_NESTED_SPLIT in reasons

    def test_confidence_not_degraded_by_nested_split(self):
        result = parse_artist_string("AC Slater, Chris Lorenzo & Fly With Us")
        assert result.confidence >= 0.90

    def test_personal_name_split_confidence_boosted(self):
        # Personal-name heuristic now returns 0.90 (was 0.85)
        from intelligence.artist.artist_parser import _try_personal_name_split
        split = _try_personal_name_split("Mark Francis Aaron K Gray")
        assert split is not None
        _, conf = split
        assert conf >= 0.90


# ===========================================================================
# Phase 4: Casing normalization
# ===========================================================================

class TestCasingNormalization:

    # --- conversions that SHOULD happen ---

    def test_all_caps_multiword_to_title_case(self):
        # Multi-word all-caps personal name → title case
        assert normalize_artist_string("LISA MILLET") == "Lisa Millet"

    def test_all_caps_dj_prefix_preserved_in_multiword(self):
        # "DJ" is a known abbreviation — must not become "Dj"
        assert normalize_artist_string("DJ SPEN") == "DJ Spen"

    def test_all_caps_mc_prefix_preserved_in_multiword(self):
        assert normalize_artist_string("MC HAMMER") == "MC Hammer"

    def test_all_caps_long_single_word_converted(self):
        # Long single-word (>6 chars) all-caps → title case
        assert normalize_artist_string("MAPHORISA") == "Maphorisa"
        assert normalize_artist_string("KEINEMUSIK") == "Keinemusik"

    # --- acronyms / short brands that must NOT be converted ---

    def test_acraze_preserved(self):
        assert normalize_artist_string("ACRAZE") == "ACRAZE"

    def test_anotr_preserved(self):
        assert normalize_artist_string("ANOTR") == "ANOTR"

    def test_atfc_preserved(self):
        assert normalize_artist_string("ATFC") == "ATFC"

    def test_avg_preserved(self):
        assert normalize_artist_string("AVG") == "AVG"

    def test_short_single_word_preserved(self):
        # Any single-word all-caps ≤ 6 chars → kept (HOSH, etc.)
        assert normalize_artist_string("HOSH") == "HOSH"

    # --- country code parentheticals ---

    def test_avg_it_country_code_preserved(self):
        # Country code in parens stays uppercase; short main body → unchanged
        assert normalize_artist_string("AVG (IT)") == "AVG (IT)"

    def test_country_code_forms_preserved(self):
        for code in ["(UK)", "(SA)", "(DE)", "(FR)"]:
            result = normalize_artist_string(f"AVG {code}")
            assert code in result, f"Country code {code!r} was lowercased in {result!r}"

    # --- mixed-case / already-correct strings must not be touched ---

    def test_mixed_case_unchanged(self):
        assert normalize_artist_string("Black Coffee") == "Black Coffee"
        assert normalize_artist_string("DJ Maphorisa") == "DJ Maphorisa"

    def test_heavy_k_unchanged(self):
        assert normalize_artist_string("Heavy-K") == "Heavy-K"

    def test_dotted_initial_preserved(self):
        assert normalize_artist_string("M.E.") == "M.E."

    # --- idempotency ---

    def test_all_caps_multiword_idempotent(self):
        result = normalize_artist_string("LISA MILLET")
        assert normalize_artist_string(result) == result

    def test_acronym_idempotent(self):
        assert normalize_artist_string(normalize_artist_string("ACRAZE")) == "ACRAZE"

    # --- change reason detection ---

    def test_casing_reason_emitted_for_multiword(self):
        result = _make_result("LISA MILLET")
        proposed = _propose_artist("LISA MILLET", result)
        assert proposed is not None
        reasons = _compute_change_reasons("LISA MILLET", proposed, result)
        assert REASON_CASING_NORMALIZED in reasons

    def test_no_casing_reason_for_acronym(self):
        # ACRAZE is kept unchanged → _propose_artist returns None → no reasons
        result = _make_result("ACRAZE")
        proposed = _propose_artist("ACRAZE", result)
        assert proposed is None   # no change → no reason to compute

    def test_mixed_case_no_casing_reason(self):
        result = _make_result("Black Coffee")
        reasons = _compute_change_reasons("Black Coffee", "Black Coffee", result)
        assert REASON_CASING_NORMALIZED not in reasons

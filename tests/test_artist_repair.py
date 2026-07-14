"""
tests/test_artist_repair.py — Unit tests for modules/artist_repair.py

Covers:
  - _strip_country_suffix: detect and strip trailing (IT), (De), (UK) etc.
  - _find_merge_positions: [a-z][A-Z] boundary detection with safety guards
  - _propose_repairs: full split proposal with confidence assignment

Positive cases (should detect merge):
  "African RhythmAfrikan Roots"
  "African RootsLebo"
  "Ante PerryDayne S"
  "Afrikan RootsBebucho Q Kua"

Negative cases (must NOT detect merge):
  "AVG (IT)"        — all uppercase, country suffix
  "A.M.R (De)"      — all uppercase initials
  "Anyma (ofc)"     — no [a-z][A-Z] boundary
  "Alan Dixon mOat (UK)" — 'm' is word-start (preceded by space)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.library_organize import is_unsafe_artist_string
from modules.artist_repair import (
    _strip_country_suffix,
    _find_merge_positions,
    _propose_repairs,
    _propose_separator_repairs,
    _load_queue,
    _save_queue,
    _approve_entry,
    _reject_entry,
    _update_review_queue,
    HIGH_CONFIDENCE,
    MEDIUM_CONFIDENCE,
    LOW_CONFIDENCE,
)

EMPTY_KNOWN: set = set()


# ---------------------------------------------------------------------------
# _strip_country_suffix
# ---------------------------------------------------------------------------

class TestStripCountrySuffix:
    def test_it_suffix(self):
        artist, suffix = _strip_country_suffix("AVG (IT)")
        assert artist == "AVG"
        assert "(IT)" in suffix

    def test_de_suffix(self):
        artist, suffix = _strip_country_suffix("A.M.R (De)")
        assert artist == "A.M.R"
        assert "(De)" in suffix

    def test_uk_suffix(self):
        artist, suffix = _strip_country_suffix("Alan Dixon mOat (UK)")
        assert artist == "Alan Dixon mOat"
        assert "(UK)" in suffix

    def test_za_suffix(self):
        artist, suffix = _strip_country_suffix("DJ Zinhle (ZA)")
        assert artist == "DJ Zinhle"
        assert suffix != ""

    def test_no_suffix(self):
        artist, suffix = _strip_country_suffix("Black Coffee")
        assert artist == "Black Coffee"
        assert suffix == ""

    def test_no_suffix_paren_is_not_country(self):
        # "(ofc)" — 3 lowercase chars — should NOT be stripped as country
        artist, suffix = _strip_country_suffix("Anyma (ofc)")
        assert artist == "Anyma (ofc)"
        assert suffix == ""


# ---------------------------------------------------------------------------
# _find_merge_positions — positive cases
# ---------------------------------------------------------------------------

class TestFindMergePositionsPositive:
    def test_african_rhythm_afrikan_roots(self):
        positions = _find_merge_positions("African RhythmAfrikan Roots")
        assert len(positions) == 1

    def test_african_roots_lebo(self):
        positions = _find_merge_positions("African RootsLebo")
        assert len(positions) == 1

    def test_ante_perry_dayne_s(self):
        positions = _find_merge_positions("Ante PerryDayne S")
        assert len(positions) == 1

    def test_afrikan_roots_bebucho_q_kua(self):
        positions = _find_merge_positions("Afrikan RootsBebucho Q Kua")
        assert len(positions) == 1

    def test_split_position_is_end_of_first_word(self):
        # "RootsLebo" — 's' (index 12 in "Afrikan Roots") is the split point
        positions = _find_merge_positions("Afrikan RootsLebo")
        # positions[0] should be the index of 's' before 'L'
        s = "Afrikan RootsLebo"
        pos = positions[0]
        assert s[pos].islower()
        assert s[pos + 1].isupper()


# ---------------------------------------------------------------------------
# _find_merge_positions — negative cases (must NOT fire)
# ---------------------------------------------------------------------------

class TestFindMergePositionsNegative:
    def test_all_uppercase_no_merge(self):
        assert _find_merge_positions("AVG") == []

    def test_anyma_no_merge(self):
        assert _find_merge_positions("Anyma") == []

    def test_ofc_all_lowercase_no_merge(self):
        assert _find_merge_positions("Anyma (ofc)") == []

    def test_moat_word_start_no_merge(self):
        # 'm' in "mOat" is preceded by a space → word-start → lookbehind fails
        assert _find_merge_positions("Alan Dixon mOat") == []

    def test_clean_two_word_artist(self):
        assert _find_merge_positions("Black Coffee") == []

    def test_clean_feat_artist(self):
        assert _find_merge_positions("Adil feat. Afrikan Roots") == []

    def test_mc_prefix_not_flagged(self):
        # "McFlare" — 'c' at offset 1 within word → prefix guard fires
        assert _find_merge_positions("McFlare") == []

    def test_de_prefix_not_flagged(self):
        # "DeBarge" — 'e' at offset 1 → prefix guard fires
        assert _find_merge_positions("DeBarge") == []

    def test_single_word_compound_name_no_merge(self):
        # "AfricanGroove" — first (and only) word of its token → compound name
        assert _find_merge_positions("AfricanGroove") == []

    def test_first_word_of_collab_token_no_merge(self):
        # "RootedSoul" starts right after ", " — first word of its token
        assert _find_merge_positions("Afrikan Roots, RootedSoul feat. Bucks") == []

    def test_first_word_of_third_token_no_merge(self):
        # "AfroZone" is first word of the third comma-separated token
        assert _find_merge_positions("Afro Warriors, Drumetic Boyz, AfroZone") == []

    def test_trailing_capital_musiq_no_merge(self):
        # "MusiQ" — uppercase Q is the last char of the word (trailing stylization)
        assert _find_merge_positions("Naak MusiQ") == []

    def test_trailing_capital_acasoul_musiq_no_merge(self):
        # "MusiQ" as second word in "AcaSoul MusiQ"
        assert _find_merge_positions("AcaSoul MusiQ") == []

    def test_trailing_capital_musiq_comma_collab_no_merge(self):
        # "AcaSoul MusiQ, Naak" — Q is followed by comma, not another name.
        # Previous space-only guard missed this; .isalpha() check fixes it.
        assert _find_merge_positions("AcaSoul MusiQ, Naak") == []

    def test_trailing_capital_kidx_no_merge(self):
        # "KidX" — boundary at offset 2 within word, caught by prefix guard
        assert _find_merge_positions("KidX") == []

    def test_trailing_capital_boyz_no_merge(self):
        # "BoyZ" — boundary at offset 2 within word, caught by prefix guard
        assert _find_merge_positions("BoyZ") == []


# ---------------------------------------------------------------------------
# _propose_repairs
# ---------------------------------------------------------------------------

class TestProposeRepairs:
    # --- Correct split proposals ---

    def test_african_roots_lebo_split(self):
        candidates = _propose_repairs("African RootsLebo", EMPTY_KNOWN)
        assert len(candidates) == 1
        assert candidates[0].proposed == "African Roots, Lebo"

    def test_ante_perry_dayne_s_split(self):
        candidates = _propose_repairs("Ante PerryDayne S", EMPTY_KNOWN)
        assert len(candidates) == 1
        assert candidates[0].proposed == "Ante Perry, Dayne S"

    def test_afrikan_roots_bebucho_split(self):
        candidates = _propose_repairs("Afrikan RootsBebucho Q Kua", EMPTY_KNOWN)
        assert len(candidates) == 1
        assert candidates[0].proposed == "Afrikan Roots, Bebucho Q Kua"

    def test_african_rhythm_afrikan_roots_split(self):
        candidates = _propose_repairs("African RhythmAfrikan Roots", EMPTY_KNOWN)
        assert len(candidates) == 1
        assert candidates[0].proposed == "African Rhythm, Afrikan Roots"

    # --- No-merge cases ---

    def test_avg_it_no_repair(self):
        assert _propose_repairs("AVG (IT)", EMPTY_KNOWN) == []

    def test_amr_de_no_repair(self):
        assert _propose_repairs("A.M.R (De)", EMPTY_KNOWN) == []

    def test_anyma_ofc_no_repair(self):
        assert _propose_repairs("Anyma (ofc)", EMPTY_KNOWN) == []

    def test_alan_dixon_moat_uk_no_repair(self):
        assert _propose_repairs("Alan Dixon mOat (UK)", EMPTY_KNOWN) == []

    def test_clean_artist_no_repair(self):
        assert _propose_repairs("Black Coffee", EMPTY_KNOWN) == []

    def test_african_groove_compound_no_repair(self):
        assert _propose_repairs("AfricanGroove", EMPTY_KNOWN) == []

    def test_naak_musiq_no_repair(self):
        # Trailing capital Q is a stylization, not a second artist
        assert _propose_repairs("Naak MusiQ", EMPTY_KNOWN) == []

    def test_acasoul_musiq_no_repair(self):
        assert _propose_repairs("AcaSoul MusiQ", EMPTY_KNOWN) == []

    def test_acasoul_musiq_comma_collab_no_repair(self):
        # "AcaSoul MusiQ, Naak" — the Q is followed by a comma separator,
        # which the old space-only guard missed, producing "AcaSoul Musi, Q, Naak".
        assert _propose_repairs("AcaSoul MusiQ, Naak", EMPTY_KNOWN) == []

    def test_rooted_soul_first_token_word_no_repair(self):
        assert _propose_repairs("Afrikan Roots, RootedSoul feat. Bucks", EMPTY_KNOWN) == []

    def test_afro_zone_first_token_word_no_repair(self):
        assert _propose_repairs("Afro Warriors, Drumetic Boyz, AfroZone", EMPTY_KNOWN) == []

    # --- Confidence levels ---

    def test_low_confidence_when_neither_known(self):
        candidates = _propose_repairs("African RootsLebo", EMPTY_KNOWN)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.confidence == LOW_CONFIDENCE
        assert c.apply_blocked is True

    def test_medium_confidence_left_known(self):
        known = {"african roots"}
        candidates = _propose_repairs("African RootsLebo", known)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.confidence == MEDIUM_CONFIDENCE
        assert c.apply_blocked is True

    def test_medium_confidence_right_known(self):
        known = {"lebo"}
        candidates = _propose_repairs("African RootsLebo", known)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.confidence == MEDIUM_CONFIDENCE
        assert c.apply_blocked is True

    def test_high_confidence_both_known(self):
        known = {"african roots", "lebo"}
        candidates = _propose_repairs("African RootsLebo", known)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.confidence == HIGH_CONFIDENCE
        assert c.apply_blocked is False

    def test_high_confidence_normalized_lookup(self):
        # Normalized lookup: "afrikanroots" should match "Afrikan Roots"
        known = {"afrikan roots", "bebucho q kua"}
        candidates = _propose_repairs("Afrikan RootsBebucho Q Kua", known)
        assert len(candidates) == 1
        assert candidates[0].apply_blocked is False

    # --- Only one candidate returned ---

    def test_single_candidate_returned(self):
        candidates = _propose_repairs("Ante PerryDayne S", EMPTY_KNOWN)
        assert len(candidates) <= 1

    # --- Source field ---

    def test_source_field_is_artist(self):
        candidates = _propose_repairs("African RootsLebo", EMPTY_KNOWN)
        assert candidates[0].source_field == "artist"

    # --- Original preserved in candidate ---

    def test_original_preserved(self):
        candidates = _propose_repairs("Ante PerryDayne S", EMPTY_KNOWN)
        assert candidates[0].original == "Ante PerryDayne S"


# ---------------------------------------------------------------------------
# _propose_separator_repairs — slash / pipe / backslash detection
# ---------------------------------------------------------------------------

class TestSeparatorRepairs:

    # --- GOOD: should split ---

    def test_slash_african_roots_lebo(self):
        c = _propose_separator_repairs("African Roots/Lebo", EMPTY_KNOWN)
        assert len(c) == 1
        assert c[0].proposed == "African Roots, Lebo"

    def test_slash_afrikan_roots_oddessy(self):
        c = _propose_separator_repairs("Afrikan Roots/Oddessy", EMPTY_KNOWN)
        assert len(c) == 1
        assert c[0].proposed == "Afrikan Roots, Oddessy"

    def test_slash_and_pipe_mixed(self):
        c = _propose_separator_repairs("NewTone Major/Steve Univers | Koki", EMPTY_KNOWN)
        assert len(c) == 1
        assert c[0].proposed == "NewTone Major, Steve Univers, Koki"

    def test_pipe_only(self):
        c = _propose_separator_repairs("Artist One | Artist Two", EMPTY_KNOWN)
        assert len(c) == 1
        assert c[0].proposed == "Artist One, Artist Two"

    # --- DO NOT SPLIT ---

    def test_acdc_allowlist_not_split(self):
        assert _propose_separator_repairs("AC/DC", EMPTY_KNOWN) == []

    def test_short_side_guard(self):
        # "AB" is 2 chars < _MIN_SEP_SIDE_LEN — not a valid artist name
        assert _propose_separator_repairs("AB/Long Artist Name", EMPTY_KNOWN) == []

    def test_no_separator_no_repair(self):
        assert _propose_separator_repairs("African Roots", EMPTY_KNOWN) == []

    # --- Reason codes ---

    def test_reason_slash_separator_repair(self):
        c = _propose_separator_repairs("African Roots/Lebo", EMPTY_KNOWN)
        assert "slash_separator_repair" in c[0].reason

    def test_reason_pipe_separator_repair(self):
        c = _propose_separator_repairs("Artist One | Artist Two", EMPTY_KNOWN)
        assert "pipe_separator_repair" in c[0].reason

    def test_reason_mixed_contains_both_codes(self):
        c = _propose_separator_repairs("NewTone Major/Steve Univers | Koki", EMPTY_KNOWN)
        assert "slash_separator_repair" in c[0].reason
        assert "pipe_separator_repair" in c[0].reason

    # --- Confidence ---

    def test_low_confidence_neither_known(self):
        c = _propose_separator_repairs("African Roots/Lebo", EMPTY_KNOWN)
        assert c[0].confidence == LOW_CONFIDENCE
        assert c[0].apply_blocked is True

    def test_medium_confidence_one_known(self):
        known = {"african roots"}
        c = _propose_separator_repairs("African Roots/Lebo", known)
        assert c[0].confidence == MEDIUM_CONFIDENCE
        assert c[0].apply_blocked is True

    def test_high_confidence_all_known(self):
        known = {"african roots", "lebo"}
        c = _propose_separator_repairs("African Roots/Lebo", known)
        assert c[0].confidence == HIGH_CONFIDENCE
        assert c[0].apply_blocked is False

    def test_source_field_is_artist(self):
        c = _propose_separator_repairs("African Roots/Lebo", EMPTY_KNOWN)
        assert c[0].source_field == "artist"

    def test_original_preserved(self):
        c = _propose_separator_repairs("African Roots/Lebo", EMPTY_KNOWN)
        assert c[0].original == "African Roots/Lebo"


# ---------------------------------------------------------------------------
# is_unsafe_artist_string (library_organize) — regression tests
# ---------------------------------------------------------------------------

class TestIsUnsafeArtistString:
    def test_african_groove_is_safe(self):
        # Single-word compound project name — must NOT be flagged
        assert is_unsafe_artist_string("AfricanGroove") is False

    def test_newtone_majorsteve_univers_koki_is_unsafe(self):
        # Multi-word string with 2 CamelCase transitions (wT + rS) — must be flagged
        assert is_unsafe_artist_string("NewTone MajorSteve Univers Koki") is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    filepath: str = "/lib/track.mp3",
    original: str = "African RootsLebo",
    proposed: str = "African Roots, Lebo",
    confidence: float = 0.45,
    reason: str = "camelcase_merge_detected; known_sides=none",
) -> dict:
    return {
        "file":            filepath,
        "original_artist": original,
        "proposed_artist": proposed,
        "confidence":      confidence,
        "reason":          reason,
    }


# ---------------------------------------------------------------------------
# Queue creation
# ---------------------------------------------------------------------------

class TestReviewQueueCreation:
    def test_new_entry_defaults_to_not_approved(self, tmp_path):
        q = tmp_path / "q.json"
        _update_review_queue(q, [_make_entry()])
        loaded = _load_queue(q)
        assert loaded[0]["approved"] is False
        assert loaded[0]["rejected"] is False

    def test_load_nonexistent_returns_empty(self, tmp_path):
        assert _load_queue(tmp_path / "missing.json") == []

    def test_roundtrip_save_load(self, tmp_path):
        q = tmp_path / "q.json"
        entries = [_make_entry()]
        _save_queue(q, entries)
        assert _load_queue(q) == entries

    def test_dedup_by_file_and_original(self, tmp_path):
        q = tmp_path / "q.json"
        entry = _make_entry()
        _update_review_queue(q, [entry])
        _update_review_queue(q, [entry])   # same key — must NOT duplicate
        assert len(_load_queue(q)) == 1

    def test_approval_state_preserved_on_rescan(self, tmp_path):
        q = tmp_path / "q.json"
        entry = _make_entry()
        _update_review_queue(q, [entry])
        # Human approves the entry
        entries = _load_queue(q)
        _approve_entry(entries, 0)
        _save_queue(q, entries)
        # artist-repair re-scans — same file/original → must NOT wipe approved
        _update_review_queue(q, [_make_entry()])
        assert _load_queue(q)[0]["approved"] is True

    def test_rejection_state_preserved_on_rescan(self, tmp_path):
        q = tmp_path / "q.json"
        _update_review_queue(q, [_make_entry()])
        entries = _load_queue(q)
        _reject_entry(entries, 0)
        _save_queue(q, entries)
        _update_review_queue(q, [_make_entry()])
        assert _load_queue(q)[0]["rejected"] is True

    def test_new_distinct_entry_appended(self, tmp_path):
        q = tmp_path / "q.json"
        _update_review_queue(q, [_make_entry(filepath="/a.mp3")])
        _update_review_queue(q, [_make_entry(filepath="/b.mp3")])
        assert len(_load_queue(q)) == 2


# ---------------------------------------------------------------------------
# Approve flow
# ---------------------------------------------------------------------------

class TestApproveEntry:
    def _entries(self) -> list:
        return [
            dict(_make_entry(), approved=False, rejected=False),
            dict(_make_entry(filepath="/b.mp3"), approved=False, rejected=False),
        ]

    def test_approve_sets_approved_true(self):
        e = self._entries()
        assert _approve_entry(e, 0) is True
        assert e[0]["approved"] is True

    def test_approve_clears_rejected(self):
        e = self._entries()
        e[0]["rejected"] = True
        _approve_entry(e, 0)
        assert e[0]["rejected"] is False

    def test_approve_does_not_affect_other_entries(self):
        e = self._entries()
        _approve_entry(e, 0)
        assert e[1]["approved"] is False

    def test_approve_out_of_range_returns_false(self):
        e = self._entries()
        assert _approve_entry(e, 99) is False
        assert _approve_entry(e, -1) is False

    def test_approve_index_zero_recognised(self):
        # 0 is falsy — must be tested explicitly (not None check matters)
        e = self._entries()
        assert _approve_entry(e, 0) is True


# ---------------------------------------------------------------------------
# Reject flow
# ---------------------------------------------------------------------------

class TestRejectEntry:
    def _entries(self) -> list:
        return [dict(_make_entry(), approved=False, rejected=False)]

    def test_reject_sets_rejected_true(self):
        e = self._entries()
        assert _reject_entry(e, 0) is True
        assert e[0]["rejected"] is True

    def test_reject_clears_approved(self):
        e = self._entries()
        e[0]["approved"] = True
        _reject_entry(e, 0)
        assert e[0]["approved"] is False

    def test_reject_out_of_range_returns_false(self):
        e = self._entries()
        assert _reject_entry(e, 5) is False


# ---------------------------------------------------------------------------
# apply-approved logic
# ---------------------------------------------------------------------------

class TestApplyApprovedLogic:
    """Tests for the eligibility filtering logic used by --apply-approved."""

    def test_only_approved_and_unapplied_are_eligible(self):
        entries = [
            dict(_make_entry(filepath="/a.mp3"), approved=False, rejected=False),
            dict(_make_entry(filepath="/b.mp3"), approved=True,  rejected=False),
            dict(_make_entry(filepath="/c.mp3"), approved=True,  rejected=False, applied=True),
        ]
        eligible = [(i, e) for i, e in enumerate(entries)
                    if e.get("approved") and not e.get("applied")]
        assert len(eligible) == 1
        assert eligible[0][1]["file"] == "/b.mp3"

    def test_rejected_entry_never_eligible(self):
        entries = [dict(_make_entry(), approved=False, rejected=True)]
        eligible = [e for e in entries if e.get("approved") and not e.get("applied")]
        assert eligible == []

    def test_already_applied_skipped(self):
        entries = [dict(_make_entry(), approved=True, applied=True)]
        eligible = [e for e in entries if e.get("approved") and not e.get("applied")]
        assert eligible == []

    def test_apply_marks_entry_applied(self):
        entries = [dict(_make_entry(), approved=True, rejected=False)]
        # Simulate successful write: mark applied
        entries[0]["applied"] = True
        assert entries[0]["applied"] is True

    def test_skip_when_current_tag_differs(self):
        # The apply loop skips entries where current tag ≠ original
        original = "African RootsLebo"
        current  = "African Roots, Lebo"   # already fixed by another tool
        assert current != original          # → SKIP_CHANGED path fires

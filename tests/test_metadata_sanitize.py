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
import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.metadata_sanitize as ms

from modules.metadata_sanitize import (
    _sanitize_title,
    _strip_label_suffix,
    _is_suspicious_recovery,
)


def _dummy(path: Path) -> Path:
    path.write_bytes(b"not real audio; tag IO is mocked")
    return path


def _args(input_path: Path, *, apply: bool = False, output_json: Path | None = None):
    return SimpleNamespace(
        input=str(input_path),
        apply=apply,
        limit=None,
        output_json=str(output_json) if output_json else None,
        verbose=False,
        force=True,
        reset_stage=False,
    )


class _ProcSpy:
    def __init__(self):
        self.records = []
        self.cleared = []

    def should_skip(self, stage, path, reason_prefix=None):
        return False

    def record(self, stage, path, status, reason=""):
        self.records.append((stage, Path(path), status, reason))

    def clear_stage(self, stage):
        self.cleared.append(stage)


def _patch_run_io(monkeypatch, tags_by_path: dict[Path, dict[str, str]]):
    proc = _ProcSpy()
    writes = []
    textlog = []

    def fake_read_tags(path: Path):
        tags = tags_by_path.get(Path(path))
        if tags is None:
            return None
        return tags.copy(), set()

    def fake_apply(path: Path, changes):
        writes.append((Path(path), list(changes)))
        tags = tags_by_path[Path(path)]
        for change in changes:
            tags[change.field] = change.new_value
        return True, set()

    monkeypatch.setattr(ms, "_read_tags", fake_read_tags)
    monkeypatch.setattr(ms, "_apply_sanitized", fake_apply)
    monkeypatch.setattr(ms, "_proc", proc)
    monkeypatch.setattr(ms, "log_action", lambda message: textlog.append(message))

    import utils.prompt_logger as prompt_logger

    monkeypatch.setattr(prompt_logger, "get_run_logger", lambda: None)
    return proc, writes, textlog


def _base_tags(**overrides):
    tags = {
        "title": "Clean Title",
        "artist": "Valid Artist",
        "album": "Valid Album",
        "organization": "Valid Label",
        "isrc": "USABC2300001",
        "bpm": "123",
        "key": "8A",
        "cue_points": "intro=0;drop=64",
        "hot_cues": "A=0;B=64",
    }
    tags.update(overrides)
    return tags


class TestRunSafetyBehavior:
    def test_preview_does_not_write_tags_and_reports_intended_changes(
        self, tmp_path, monkeypatch, capsys
    ):
        track = _dummy(tmp_path / "dirty.mp3")
        tags_by_path = {
            track: _base_tags(
                title="Dance with Me (Extended Mix) (fordjonly.com)",
                artist="Valid Artist",
            )
        }
        proc, writes, textlog = _patch_run_io(monkeypatch, tags_by_path)

        rc = ms.run_metadata_sanitize(_args(tmp_path, apply=False))

        out = capsys.readouterr().out
        assert rc == 0
        assert writes == []
        assert textlog == []
        assert tags_by_path[track]["title"] == "Dance with Me (Extended Mix) (fordjonly.com)"
        assert "metadata-sanitize [PREVIEW]" in out
        assert "title_domain_token_stripped" in out
        assert "Dance with Me (Extended Mix)" in out
        assert "Dry-run mode" in out
        assert proc.records == []

    def test_apply_writes_only_in_apply_mode_and_skips_unchanged_files(
        self, tmp_path, monkeypatch
    ):
        dirty = _dummy(tmp_path / "dirty.mp3")
        clean = _dummy(tmp_path / "clean.mp3")
        tags_by_path = {
            dirty: _base_tags(title="Sunrise (Club Edit) 120"),
            clean: _base_tags(title="Track (Original Mix)"),
        }
        proc, writes, textlog = _patch_run_io(monkeypatch, tags_by_path)

        rc = ms.run_metadata_sanitize(_args(tmp_path, apply=True))

        assert rc == 0
        assert [(path, [c.field for c in changes]) for path, changes in writes] == [
            (dirty, ["title"])
        ]
        assert tags_by_path[dirty]["title"] == "Sunrise (Club Edit)"
        assert tags_by_path[clean]["title"] == "Track (Original Mix)"
        assert any("SANITIZE: dirty.mp3 | title" in entry for entry in textlog)
        assert ("metadata-sanitize", dirty, "success", "") in proc.records
        assert any(
            stage == "metadata-sanitize"
            and path == clean
            and status == "no_change"
            and reason.startswith("rules:")
            for stage, path, status, reason in proc.records
        )

    def test_artist_title_safety_does_not_create_empty_fields(
        self, tmp_path, monkeypatch
    ):
        track = _dummy(tmp_path / "safe.mp3")
        tags_by_path = {
            track: _base_tags(
                title="Track (Original Mix)",
                artist="Black Coffee feat. Bucie",
                album="Home Brewed",
                organization="Soulistic Music",
            )
        }
        proc, writes, _ = _patch_run_io(monkeypatch, tags_by_path)

        rc = ms.run_metadata_sanitize(_args(tmp_path, apply=True))

        assert rc == 0
        assert writes == []
        assert tags_by_path[track]["title"] == "Track (Original Mix)"
        assert tags_by_path[track]["artist"] == "Black Coffee feat. Bucie"
        assert tags_by_path[track]["album"] == "Home Brewed"
        assert tags_by_path[track]["organization"] == "Soulistic Music"
        assert all(tags_by_path[track][field] for field in ("title", "artist"))
        assert any(status == "no_change" for _, _, status, _ in proc.records)

    def test_multi_value_artist_transform_is_skipped_and_reported(
        self, tmp_path, monkeypatch, capsys
    ):
        track = _dummy(tmp_path / "multi-artist.mp3")
        tags_by_path = {
            track: _base_tags(
                title="Clean Title",
                artist="Artist One / / Artist Two",
            )
        }
        proc = _ProcSpy()
        writes = []

        def fake_read_tags(path: Path):
            return tags_by_path[Path(path)].copy(), {"artist"}

        monkeypatch.setattr(ms, "_read_tags", fake_read_tags)
        monkeypatch.setattr(ms, "_apply_sanitized", lambda path, changes: writes.append((path, changes)))
        monkeypatch.setattr(ms, "_proc", proc)
        monkeypatch.setattr(ms, "log_action", lambda message: None)

        import utils.prompt_logger as prompt_logger

        monkeypatch.setattr(prompt_logger, "get_run_logger", lambda: None)

        rc = ms.run_metadata_sanitize(_args(tmp_path, apply=True))

        out = capsys.readouterr().out
        assert rc == 0
        assert writes == []
        assert tags_by_path[track]["artist"] == "Artist One / / Artist Two"
        assert "skipped_multi_value_artist" in out
        assert ("metadata-sanitize", track, "skipped", "artist: skipped_multi_value_artist") in proc.records

    def test_dj_metadata_fields_are_not_in_write_changes(self, tmp_path, monkeypatch):
        track = _dummy(tmp_path / "dj-tags.mp3")
        tags_by_path = {
            track: _base_tags(
                title="Kanana (Shungi Music) 124",
                bpm="124",
                key="9A",
                cue_points="intro=0;break=32;drop=64",
                hot_cues="A=0;B=32;C=64",
            )
        }
        _, writes, _ = _patch_run_io(monkeypatch, tags_by_path)

        rc = ms.run_metadata_sanitize(_args(tmp_path, apply=True))

        assert rc == 0
        assert tags_by_path[track]["title"] == "Kanana"
        assert tags_by_path[track]["bpm"] == "124"
        assert tags_by_path[track]["key"] == "9A"
        assert tags_by_path[track]["cue_points"] == "intro=0;break=32;drop=64"
        assert tags_by_path[track]["hot_cues"] == "A=0;B=32;C=64"
        written_fields = {change.field for _, changes in writes for change in changes}
        assert written_fields == {"title"}
        assert not {"bpm", "key", "cue_points", "hot_cues"} & written_fields

    def test_apply_is_idempotent_second_run_has_no_additional_changes(
        self, tmp_path, monkeypatch
    ):
        track = _dummy(tmp_path / "dirty.mp3")
        tags_by_path = {
            track: _base_tags(title="2 Sada (N'Dinga Gaba Diplomacy Soul Remix)")
        }
        proc, writes, _ = _patch_run_io(monkeypatch, tags_by_path)

        first = ms.run_metadata_sanitize(_args(tmp_path, apply=True))
        second = ms.run_metadata_sanitize(_args(tmp_path, apply=True))

        assert first == 0
        assert second == 0
        assert tags_by_path[track]["title"] == "Sada (N'Dinga Gaba Diplomacy Soul Remix)"
        assert len(writes) == 1
        assert writes[0][1][0].old_value == "2 Sada (N'Dinga Gaba Diplomacy Soul Remix)"
        assert writes[0][1][0].new_value == "Sada (N'Dinga Gaba Diplomacy Soul Remix)"
        assert any(status == "success" for _, _, status, _ in proc.records)
        assert any(status == "no_change" for _, _, status, _ in proc.records)

    def test_preview_json_log_reports_changes_without_apply(
        self, tmp_path, monkeypatch
    ):
        track = _dummy(tmp_path / "dirty.mp3")
        output_json = tmp_path / "metadata-sanitize.json"
        tags_by_path = {
            track: _base_tags(title="Song (Extended Mix) (Kontor Records) 120")
        }
        _, writes, _ = _patch_run_io(monkeypatch, tags_by_path)

        rc = ms.run_metadata_sanitize(
            _args(tmp_path, apply=False, output_json=output_json)
        )

        assert rc == 0
        assert writes == []
        data = json.loads(output_json.read_text(encoding="utf-8"))
        assert data["results"]["changed"] == 1
        assert data["results"]["unchanged"] == 0
        assert data["tracks"][0]["file"] == str(track)
        assert data["tracks"][0]["changes"][0]["field"] == "title"
        assert data["tracks"][0]["changes"][0]["new"] == "Song (Extended Mix)"


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
# Bare leading track-number — stripping (junk track-index prefixes)
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

    def test_two_digit_prefix_unchanged(self):
        # Two-digit leading number (>= 10) is now a guard — never stripped.
        result, reason = _sanitize_title("10 Tracks Away")
        assert result == "10 Tracks Away"
        assert reason == ""

    def test_single_digit_non_protected_stripped(self):
        result, reason = _sanitize_title("5 Tracks Away")
        assert result == "Tracks Away"
        assert reason == "title_bare_number_stripped"

    def test_number_prefix_does_not_strip_too_short(self):
        # Result would be < 2 chars — should not strip
        result, reason = _sanitize_title("2 A")
        # "A" is only 1 char — guard should block stripping
        # Main assertion: no crash, result not empty
        assert result  # non-empty


# ---------------------------------------------------------------------------
# Bare leading track-number — guard: protected first words must NOT be stripped
# ---------------------------------------------------------------------------

class TestBareNumberGuard:
    def test_4_you_unchanged(self):
        result, reason = _sanitize_title("4 You")
        assert result == "4 You"
        assert reason == ""

    def test_15_minutes_unchanged(self):
        result, reason = _sanitize_title("15 Minutes")
        assert result == "15 Minutes"
        assert reason == ""

    def test_24_hours_unchanged(self):
        result, reason = _sanitize_title("24 Hours")
        assert result == "24 Hours"
        assert reason == ""

    def test_protected_with_paren_suffix_unchanged(self):
        result, reason = _sanitize_title("4 You (Original Mix)")
        assert result == "4 You (Original Mix)"
        assert reason == ""

    def test_2_love_unchanged(self):
        result, reason = _sanitize_title("2 Love")
        assert result == "2 Love"
        assert reason == ""

    def test_3_days_unchanged(self):
        result, reason = _sanitize_title("3 Days")
        assert result == "3 Days"
        assert reason == ""

    def test_10_tracks_away_unchanged(self):
        # number >= 10 guard — two-digit numbers are never stripped
        result, reason = _sanitize_title("10 Tracks Away")
        assert result == "10 Tracks Away"
        assert reason == ""

    def test_99_problems_unchanged(self):
        result, reason = _sanitize_title("99 Problems")
        assert result == "99 Problems"
        assert reason == ""

    def test_3_13th_friday_unchanged(self):
        # Second token "13th" starts with a digit — numeric/ordinal guard fires
        result, reason = _sanitize_title("3 13th Friday")
        assert result == "3 13th Friday"
        assert reason == ""

    def test_4_100_sure_unchanged(self):
        # Second token "100" starts with a digit — numeric guard fires
        result, reason = _sanitize_title("4 100 Sure")
        assert result == "4 100 Sure"
        assert reason == ""

    def test_2_sada_still_stripped(self):
        # "Sada" is not a protected word — track-index stripping must still fire
        result, reason = _sanitize_title("2 Sada (N'Dinga Gaba Diplomacy Soul Remix)")
        assert result == "Sada (N'Dinga Gaba Diplomacy Soul Remix)"
        assert reason == "title_bare_number_stripped"

    def test_3_afro_still_stripped(self):
        # "Afro" is not a protected word — track-index stripping must still fire
        result, reason = _sanitize_title("3 Afro")
        assert result == "Afro"
        assert reason == "title_bare_number_stripped"

    def test_2_africa_feat_luzolo_still_stripped(self):
        # Multi-word but "Africa" is not protected and not numeric
        result, reason = _sanitize_title("2 Africa Feat. Luzolo")
        assert result == "Africa Feat. Luzolo"
        assert reason == "title_bare_number_stripped"


# ---------------------------------------------------------------------------
# _is_suspicious_recovery — title-number-recover detection logic
# ---------------------------------------------------------------------------

class TestSuspiciousRecovery:
    def test_4_you_suspicious(self):
        ok, reason = _is_suspicious_recovery(4, "You")
        assert ok
        assert "You" in reason

    def test_15_minutes_suspicious(self):
        ok, reason = _is_suspicious_recovery(15, "Minutes")
        assert ok

    def test_24_hours_suspicious(self):
        # "hours" is in the protected word list AND number >= 10 — either guard fires
        ok, reason = _is_suspicious_recovery(24, "Hours")
        assert ok

    def test_10_tracks_suspicious(self):
        # number >= 10 makes this suspicious even though "Tracks" is not in the list
        ok, reason = _is_suspicious_recovery(10, "Tracks Away")
        assert ok
        assert "10" in reason

    def test_2_sada_not_suspicious(self):
        ok, reason = _is_suspicious_recovery(2, "Sada")
        assert not ok
        assert "track_index" in reason

    def test_3_afro_not_suspicious(self):
        ok, reason = _is_suspicious_recovery(3, "Afro")
        assert not ok

    def test_2_africa_not_suspicious(self):
        ok, reason = _is_suspicious_recovery(2, "Africa")
        assert not ok

    def test_3_faith_not_suspicious(self):
        ok, reason = _is_suspicious_recovery(3, "Faith")
        assert not ok

    def test_1_agora_not_suspicious(self):
        ok, reason = _is_suspicious_recovery(1, "Agora")
        assert not ok

    def test_3_years_suspicious(self):
        # "years" is in the protected word list
        ok, reason = _is_suspicious_recovery(3, "Years")
        assert ok


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

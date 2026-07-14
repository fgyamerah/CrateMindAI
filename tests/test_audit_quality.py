"""
Unit tests for modules/audit_quality.py

Run:
    python3 -m pytest tests/test_audit_quality.py -v
    python3 -m unittest tests.test_audit_quality -v
"""
import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.audit_quality import (
    QualityTier,
    AuditResult,
    classify_tier,
    _probe_file,
    _move_file,
    _write_quality_tag,
    run,
)


# ---------------------------------------------------------------------------
# classify_tier — pure function tests
# ---------------------------------------------------------------------------

class TestClassifyTier(unittest.TestCase):
    """Tests for the pure classify_tier() function (no I/O)."""

    # --- Lossless ---

    def test_flac_is_lossless(self):
        self.assertEqual(classify_tier("flac", None), QualityTier.LOSSLESS)

    def test_flac_with_bitrate_still_lossless(self):
        """Bitrate arg is irrelevant for lossless codecs."""
        self.assertEqual(classify_tier("flac", 1000), QualityTier.LOSSLESS)

    def test_alac_is_lossless(self):
        self.assertEqual(classify_tier("alac", None), QualityTier.LOSSLESS)

    def test_alac_uppercase_is_lossless(self):
        """Codec names are case-normalised inside classify_tier."""
        self.assertEqual(classify_tier("ALAC", None), QualityTier.LOSSLESS)

    def test_pcm_s16le_is_lossless(self):
        """WAV (PCM) is always LOSSLESS."""
        self.assertEqual(classify_tier("pcm_s16le", None), QualityTier.LOSSLESS)

    def test_pcm_s16be_is_lossless(self):
        """AIFF (PCM big-endian) is always LOSSLESS."""
        self.assertEqual(classify_tier("pcm_s16be", None), QualityTier.LOSSLESS)

    def test_pcm_prefix_is_lossless(self):
        """Any pcm_* codec is LOSSLESS regardless of exact variant."""
        self.assertEqual(classify_tier("pcm_f64le", None), QualityTier.LOSSLESS)

    # --- HIGH (lossy >= 256 kbps) ---

    def test_mp3_320_is_high(self):
        self.assertEqual(classify_tier("mp3", 320), QualityTier.HIGH)

    def test_aac_256_is_high(self):
        self.assertEqual(classify_tier("aac", 256), QualityTier.HIGH)

    def test_aac_320_is_high(self):
        self.assertEqual(classify_tier("aac", 320), QualityTier.HIGH)

    def test_mp3_exactly_256_is_high(self):
        """Boundary: 256 kbps is HIGH."""
        self.assertEqual(classify_tier("mp3", 256), QualityTier.HIGH)

    # --- MEDIUM (lossy 192–255 kbps) ---

    def test_mp3_192_is_medium(self):
        """Boundary: 192 kbps == min_lossy_kbps default → MEDIUM."""
        self.assertEqual(classify_tier("mp3", 192), QualityTier.MEDIUM)

    def test_mp3_255_is_medium(self):
        self.assertEqual(classify_tier("mp3", 255), QualityTier.MEDIUM)

    def test_aac_200_is_medium(self):
        self.assertEqual(classify_tier("aac", 200), QualityTier.MEDIUM)

    # --- LOW (lossy < 192 kbps) ---

    def test_aac_128_is_low(self):
        self.assertEqual(classify_tier("aac", 128), QualityTier.LOW)

    def test_mp3_160_is_low(self):
        self.assertEqual(classify_tier("mp3", 160), QualityTier.LOW)

    def test_mp3_191_is_low(self):
        """Just below the default threshold → LOW."""
        self.assertEqual(classify_tier("mp3", 191), QualityTier.LOW)

    def test_aac_96_is_low(self):
        self.assertEqual(classify_tier("aac", 96), QualityTier.LOW)

    # --- Custom min_lossy_kbps threshold ---

    def test_custom_threshold_lower(self):
        """min_lossy_kbps=128: 160 kbps is MEDIUM, not LOW."""
        self.assertEqual(classify_tier("mp3", 160, min_lossy_kbps=128), QualityTier.MEDIUM)

    def test_custom_threshold_higher(self):
        """min_lossy_kbps=224: 192 kbps is LOW, not MEDIUM."""
        self.assertEqual(classify_tier("mp3", 192, min_lossy_kbps=224), QualityTier.LOW)

    # --- UNKNOWN ---

    def test_none_codec_is_unknown(self):
        self.assertEqual(classify_tier(None, None), QualityTier.UNKNOWN)

    def test_empty_codec_is_unknown(self):
        self.assertEqual(classify_tier("", None), QualityTier.UNKNOWN)

    def test_mp3_without_bitrate_is_unknown(self):
        """Lossy codec with no bitrate info → UNKNOWN."""
        self.assertEqual(classify_tier("mp3", None), QualityTier.UNKNOWN)

    def test_aac_without_bitrate_is_unknown(self):
        self.assertEqual(classify_tier("aac", None), QualityTier.UNKNOWN)

    def test_unrecognized_codec_is_unknown(self):
        self.assertEqual(classify_tier("weird_codec_xyz", 320), QualityTier.UNKNOWN)


# ---------------------------------------------------------------------------
# _move_file — structure-preserving move logic
# ---------------------------------------------------------------------------

class TestMoveFile(unittest.TestCase):
    """Tests for _move_file() — folder-structure preservation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Create a nested source structure:  tmp/music/Artist/Album/track.mp3
        self.scan_root = self.tmp / "music"
        self.artist_dir = self.scan_root / "Artist" / "Album"
        self.artist_dir.mkdir(parents=True)
        self.track = self.artist_dir / "track.mp3"
        self.track.write_bytes(b"fake mp3 data")
        self.low_dir = self.tmp / "low_quality"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_move_preserves_folder_structure(self):
        dest = _move_file(self.track, self.scan_root, self.low_dir)
        expected = self.low_dir / "Artist" / "Album" / "track.mp3"
        self.assertEqual(dest, expected)
        self.assertTrue(expected.exists())
        self.assertFalse(self.track.exists())

    def test_move_creates_parent_dirs(self):
        """Destination directories are created as needed."""
        _move_file(self.track, self.scan_root, self.low_dir)
        self.assertTrue((self.low_dir / "Artist" / "Album").is_dir())

    def test_dry_run_does_not_move(self):
        dest = _move_file(self.track, self.scan_root, self.low_dir, dry_run=True)
        self.assertIsNotNone(dest)
        # File should still be in original location
        self.assertTrue(self.track.exists())
        # Destination should NOT exist
        self.assertFalse(dest.exists())

    def test_dry_run_returns_expected_dest(self):
        dest = _move_file(self.track, self.scan_root, self.low_dir, dry_run=True)
        expected = self.low_dir / "Artist" / "Album" / "track.mp3"
        self.assertEqual(dest, expected)

    def test_invalid_relative_path_returns_none(self):
        """Path outside scan_root → returns None, no crash."""
        outside = self.tmp / "other" / "file.mp3"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"data")
        result = _move_file(outside, self.scan_root, self.low_dir)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# run() — integration tests with mocked ffprobe
# ---------------------------------------------------------------------------

class TestRunFunction(unittest.TestCase):
    """Integration tests for run() using a temporary directory + mocked _probe_file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.scan_root = self.tmp / "library"
        self.scan_root.mkdir()
        # Create dummy audio files
        self._make_file("high_track.mp3")
        self._make_file("medium_track.mp3")
        self._make_file("low_track.mp3")
        self._make_file("lossless_track.flac")
        self._make_file("alac_track.m4a")
        self._make_file("corrupt_track.mp3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_file(self, name: str) -> Path:
        p = self.scan_root / name
        p.write_bytes(b"fake audio data")
        return p

    def _mock_probe(self, path: Path, ffprobe_bin: str = "ffprobe"):
        """Return (codec, bitrate_kbps) based on filename."""
        name = path.name
        if "high" in name:
            return "mp3", 320
        if "medium" in name:
            return "mp3", 192
        if "low" in name:
            return "aac", 128
        if "lossless" in name:
            return "flac", None
        if "alac" in name:
            return "alac", None
        if "corrupt" in name:
            return None, None
        return "mp3", 256

    @patch("modules.audit_quality._probe_file")
    def test_tier_classification_in_run(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        results, _ = run(
            scan_root=self.scan_root,
            dry_run=True,
            report_formats=[],  # no file writes
            store_in_db=False,
        )

        tiers = {r.filepath.name: r.quality_tier for r in results}
        self.assertEqual(tiers["high_track.mp3"],    QualityTier.HIGH)
        self.assertEqual(tiers["medium_track.mp3"],  QualityTier.MEDIUM)
        self.assertEqual(tiers["low_track.mp3"],     QualityTier.LOW)
        self.assertEqual(tiers["lossless_track.flac"], QualityTier.LOSSLESS)
        self.assertEqual(tiers["alac_track.m4a"],    QualityTier.LOSSLESS)
        self.assertEqual(tiers["corrupt_track.mp3"], QualityTier.UNKNOWN)

    @patch("modules.audit_quality._probe_file")
    def test_unreadable_file_marked_unreadable(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        results, _ = run(
            scan_root=self.scan_root,
            dry_run=True,
            report_formats=[],
            store_in_db=False,
        )
        corrupt = next(r for r in results if r.filepath.name == "corrupt_track.mp3")
        self.assertEqual(corrupt.quality_tier,  QualityTier.UNKNOWN)
        self.assertEqual(corrupt.action_taken,  "unreadable")

    @patch("modules.audit_quality._probe_file")
    def test_move_low_quality_preserves_structure(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        low_dir = self.tmp / "low_quality"

        results, _ = run(
            scan_root=self.scan_root,
            dry_run=False,
            move_low_dir=low_dir,
            report_formats=[],
            store_in_db=False,
        )

        low_results = [r for r in results if r.quality_tier == QualityTier.LOW]
        self.assertEqual(len(low_results), 1)
        self.assertEqual(low_results[0].action_taken, "moved")

        # The file should have been moved
        expected_dest = low_dir / "low_track.mp3"
        self.assertTrue(expected_dest.exists())
        self.assertFalse((self.scan_root / "low_track.mp3").exists())

    @patch("modules.audit_quality._probe_file")
    def test_dry_run_does_not_move_files(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        low_dir = self.tmp / "low_quality"

        run(
            scan_root=self.scan_root,
            dry_run=True,
            move_low_dir=low_dir,
            report_formats=[],
            store_in_db=False,
        )

        # Original file must still exist
        self.assertTrue((self.scan_root / "low_track.mp3").exists())

    @patch("modules.audit_quality._probe_file")
    def test_write_tags_off_by_default(self, mock_probe):
        """With write_tags=False (default), no tag-writing should occur."""
        mock_probe.side_effect = self._mock_probe
        with patch("modules.audit_quality._write_quality_tag") as mock_tag:
            run(
                scan_root=self.scan_root,
                dry_run=False,
                write_tags=False,
                report_formats=[],
                store_in_db=False,
            )
            mock_tag.assert_not_called()

    @patch("modules.audit_quality._probe_file")
    def test_write_tags_called_when_enabled(self, mock_probe):
        """With write_tags=True, _write_quality_tag is called for known-tier files."""
        mock_probe.side_effect = self._mock_probe
        with patch("modules.audit_quality._write_quality_tag", return_value=True) as mock_tag:
            run(
                scan_root=self.scan_root,
                dry_run=False,
                write_tags=True,
                report_formats=[],
                store_in_db=False,
            )
        # Should be called for every file that has a known tier (not UNKNOWN)
        called_tiers = {call[0][1] for call in mock_tag.call_args_list}
        self.assertIn(QualityTier.HIGH,     called_tiers)
        self.assertIn(QualityTier.MEDIUM,   called_tiers)
        self.assertIn(QualityTier.LOW,      called_tiers)
        self.assertIn(QualityTier.LOSSLESS, called_tiers)
        # UNKNOWN must NOT have been passed
        self.assertNotIn(QualityTier.UNKNOWN, called_tiers)

    @patch("modules.audit_quality._probe_file")
    def test_csv_report_written(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        report_dir = self.tmp / "reports"

        _, report_paths = run(
            scan_root=self.scan_root,
            dry_run=False,
            report_formats=["csv"],
            store_in_db=False,
            report_dir=report_dir,
        )

        self.assertIn("csv", report_paths)
        self.assertTrue(report_paths["csv"].exists())

    @patch("modules.audit_quality._probe_file")
    def test_json_report_written(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        report_dir = self.tmp / "reports"

        _, report_paths = run(
            scan_root=self.scan_root,
            dry_run=False,
            report_formats=["json"],
            store_in_db=False,
            report_dir=report_dir,
        )

        self.assertIn("json", report_paths)
        json_path = report_paths["json"]
        self.assertTrue(json_path.exists())

        import json as _json
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)
        first = data[0]
        self.assertIn("filepath",     first)
        self.assertIn("codec",        first)
        self.assertIn("bitrate_kbps", first)
        self.assertIn("quality_tier", first)
        self.assertIn("action_taken", first)

    @patch("modules.audit_quality._probe_file")
    def test_dry_run_skips_report_files(self, mock_probe):
        mock_probe.side_effect = self._mock_probe
        report_dir = self.tmp / "reports"

        _, report_paths = run(
            scan_root=self.scan_root,
            dry_run=True,
            report_formats=["csv", "json"],
            store_in_db=False,
            report_dir=report_dir,
        )
        # dry_run → no report files written
        self.assertEqual(report_paths, {})

    @patch("modules.audit_quality._probe_file")
    def test_custom_min_lossy_kbps(self, mock_probe):
        """Changing min_lossy_kbps shifts the LOW/MEDIUM boundary."""
        def probe_160(path, ffprobe_bin="ffprobe"):
            return "mp3", 160

        mock_probe.side_effect = probe_160

        # With default 192: 160 kbps → LOW
        results_default, _ = run(
            scan_root=self.scan_root,
            dry_run=True,
            report_formats=[],
            store_in_db=False,
            min_lossy_kbps=192,
        )
        all_default = {r.quality_tier for r in results_default if r.quality_tier != QualityTier.UNKNOWN}
        self.assertTrue(all(t == QualityTier.LOW for t in all_default))

        # With 128: 160 kbps → MEDIUM
        results_custom, _ = run(
            scan_root=self.scan_root,
            dry_run=True,
            report_formats=[],
            store_in_db=False,
            min_lossy_kbps=128,
        )
        all_custom = {r.quality_tier for r in results_custom if r.quality_tier != QualityTier.UNKNOWN}
        self.assertTrue(all(t == QualityTier.MEDIUM for t in all_custom))

    def test_empty_directory_returns_no_results(self):
        empty_dir = self.tmp / "empty"
        empty_dir.mkdir()
        results, _ = run(
            scan_root=empty_dir,
            dry_run=True,
            report_formats=[],
            store_in_db=False,
        )
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Edge cases for classify_tier
# ---------------------------------------------------------------------------

class TestClassifyTierEdgeCases(unittest.TestCase):

    def test_ogg_vorbis_low_bitrate(self):
        self.assertEqual(classify_tier("vorbis", 128), QualityTier.LOW)

    def test_opus_high_bitrate(self):
        self.assertEqual(classify_tier("opus", 320), QualityTier.HIGH)

    def test_zero_bitrate_is_low(self):
        """0 kbps counts as below any threshold → LOW."""
        self.assertEqual(classify_tier("mp3", 0), QualityTier.LOW)

    def test_very_high_bitrate_is_high(self):
        self.assertEqual(classify_tier("mp3", 9999), QualityTier.HIGH)

    def test_codec_case_insensitive(self):
        self.assertEqual(classify_tier("MP3", 320), QualityTier.HIGH)
        self.assertEqual(classify_tier("FLAC", None), QualityTier.LOSSLESS)
        self.assertEqual(classify_tier("AAC", 128), QualityTier.LOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)

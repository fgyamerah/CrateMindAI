from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline


@pytest.fixture(autouse=True)
def quiet_pipeline(monkeypatch):
    monkeypatch.setattr(pipeline, "_setup_logging", lambda verbose=False: None)
    monkeypatch.setattr(pipeline, "log_action", lambda message: None)
    monkeypatch.setattr(pipeline.db, "init_db", lambda: None)


def _audio_file(tmp_path: Path, name: str = "track.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(b"audio")
    return path


def _base_args(command: str, tmp_path: Path, **overrides):
    values = {
        "command": command,
        "dry_run": False,
        "apply": False,
        "yes": False,
        "force": False,
        "verbose": False,
        "path": str(tmp_path),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _metadata_clean_case(monkeypatch, tmp_path, calls):
    audio = _audio_file(tmp_path)
    monkeypatch.setattr(pipeline, "_collect_audio_from_dir", lambda path: [audio])

    def fake_run(paths, dry_run=False):
        calls.append(("metadata-clean", dry_run, list(paths)))
        return 1, 1, 1

    monkeypatch.setattr(pipeline.metadata_clean, "run", fake_run)
    return pipeline.run_metadata_clean, _base_args("metadata-clean", tmp_path)


def _tag_normalize_case(monkeypatch, tmp_path, calls):
    audio = _audio_file(tmp_path)
    monkeypatch.setattr(pipeline, "_collect_audio_from_dir", lambda path: [audio])

    def fake_run(paths, dry_run=False, verbose=False):
        calls.append(("tag-normalize", dry_run, list(paths)))
        return 1, 1, 1, 0

    monkeypatch.setattr(pipeline.tag_normalize, "run", fake_run)
    return pipeline.run_tag_normalize, _base_args("tag-normalize", tmp_path)


def _analyze_missing_case(monkeypatch, tmp_path, calls):
    from modules import analyze_missing

    def fake_run(**kwargs):
        calls.append(("analyze-missing", kwargs["dry_run"], kwargs))
        return 0

    monkeypatch.setattr(analyze_missing, "run", fake_run)
    return pipeline.run_analyze_missing, _base_args(
        "analyze-missing",
        tmp_path,
        limit=None,
        timeout_sec=None,
        min_confidence=0.0,
        file_timeout_sec=10.0,
        isolate_corrupt=True,
        corrupt_dir=None,
    )


def _cue_suggest_case(monkeypatch, tmp_path, calls):
    audio = _audio_file(tmp_path)
    monkeypatch.setattr(pipeline, "_collect_audio_from_dir", lambda path: [audio])
    from modules import cue_suggest

    def fake_run(paths, dry_run=False, min_conf=0.0, export_formats=None):
        calls.append(("cue-suggest", dry_run, list(paths)))
        return 1, 1 if not dry_run else 0

    monkeypatch.setattr(cue_suggest, "run", fake_run)
    return pipeline.run_cue_suggest, _base_args(
        "cue-suggest",
        tmp_path,
        min_confidence=0.5,
        limit=None,
        track=None,
        export_format=None,
    )


def _db_prune_case(monkeypatch, tmp_path, calls):
    def fake_prune(root, dry_run=False):
        calls.append(("db-prune-stale", dry_run, root))
        return 2, 1

    monkeypatch.setattr(pipeline.db, "prune_stale_tracks", fake_prune)
    return pipeline.run_db_prune_stale, _base_args("db-prune-stale", tmp_path)


def _convert_audio_case(monkeypatch, tmp_path, calls):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    archive = tmp_path / "archive"
    src.mkdir()
    dst.mkdir()
    archive.mkdir()
    from modules import convert_audio

    def fake_run(**kwargs):
        calls.append(("convert-audio", kwargs["dry_run"], kwargs))
        return 0

    monkeypatch.setattr(convert_audio, "run", fake_run)
    return pipeline.run_convert_audio, _base_args(
        "convert-audio",
        tmp_path,
        path=None,
        src=str(src),
        dst=str(dst),
        archive=str(archive),
        workers=1,
        overwrite=False,
        verify_tolerance_sec=1.0,
        no_progress=True,
    )


def _review_queue_case(monkeypatch, tmp_path, calls):
    import intelligence.enrichment.runner as runner

    def fake_review_queue(args):
        calls.append(("review-queue", getattr(args, "dry_run", None), getattr(args, "list_only", None)))
        return 0

    monkeypatch.setattr(runner, "run_review_queue", fake_review_queue)
    return pipeline.run_review_queue_command, _base_args(
        "review-queue",
        tmp_path,
        path=None,
        list_only=False,
    )


COMMAND_CASES = [
    _metadata_clean_case,
    _tag_normalize_case,
    _analyze_missing_case,
    _cue_suggest_case,
    _db_prune_case,
    _convert_audio_case,
    _review_queue_case,
]


@pytest.mark.parametrize("case_factory", COMMAND_CASES)
def test_write_capable_commands_default_to_dry_run(case_factory, monkeypatch, tmp_path, capsys):
    calls = []
    handler, args = case_factory(monkeypatch, tmp_path, calls)

    rc = handler(args)

    assert rc == 0
    assert "MODE: DRY-RUN" in capsys.readouterr().out
    assert calls
    assert calls[0][1] is True
    if args.command == "review-queue":
        assert calls[0][2] is True


@pytest.mark.parametrize("case_factory", COMMAND_CASES)
def test_apply_requires_confirmation_and_does_not_call_runtime(
    case_factory, monkeypatch, tmp_path, capsys
):
    calls = []
    handler, args = case_factory(monkeypatch, tmp_path, calls)
    args.apply = True

    rc = handler(args)

    assert rc == 2
    assert calls == []
    assert "--apply requires --yes or --force" in capsys.readouterr().err


@pytest.mark.parametrize("case_factory", COMMAND_CASES)
def test_apply_with_confirmation_passes_write_mode(case_factory, monkeypatch, tmp_path, capsys):
    calls = []
    handler, args = case_factory(monkeypatch, tmp_path, calls)
    args.apply = True
    args.yes = True

    rc = handler(args)

    assert rc == 0
    assert "MODE: APPLY" in capsys.readouterr().out
    assert calls
    assert calls[0][1] is False
    if args.command == "review-queue":
        assert calls[0][2] is False


def test_apply_and_dry_run_are_rejected(monkeypatch, tmp_path, capsys):
    calls = []
    handler, args = _metadata_clean_case(monkeypatch, tmp_path, calls)
    args.apply = True
    args.yes = True
    args.dry_run = True

    rc = handler(args)

    assert rc == 2
    assert calls == []
    assert "--apply cannot be combined with --dry-run" in capsys.readouterr().err

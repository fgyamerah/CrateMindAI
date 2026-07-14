import json
from types import SimpleNamespace

import pipeline


def _write_queue(root, entries):
    queue_path = root / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return queue_path


def _make_entry(filepath, confidence, action, score, provider="spotify", title="Song"):
    return {
        "filepath": filepath,
        "query": {"artist": "Artist", "title": title},
        "best_match": {"provider": provider, "artist": "Artist", "title": title},
        "score": score,
        "confidence": confidence,
        "provider": provider,
        "action_suggestion": action,
        "timestamp": "2026-05-05T17:00:00",
    }


def test_summary_counts_are_correct(tmp_path, capsys):
    _write_queue(
        tmp_path,
        [
            _make_entry("/music/high.mp3", "HIGH", "auto_candidate", 0.99),
            _make_entry("/music/review-1.mp3", "MEDIUM", "review", 0.84, provider="deezer"),
            _make_entry("/music/review-2.mp3", "MEDIUM", "review", 0.80),
            _make_entry("/music/ignore.mp3", "LOW", "ignore", 0.20, provider=""),
        ],
    )

    rc = pipeline.run_enrichment_review(SimpleNamespace(root=str(tmp_path)))

    assert rc == 0
    out = capsys.readouterr().out
    assert "Total entries    : 4" in out
    assert "auto_candidate   : 1" in out
    assert "review           : 2" in out
    assert "ignore           : 1" in out


def test_filtering_and_limit_work(tmp_path, capsys):
    _write_queue(
        tmp_path,
        [
            _make_entry("/music/high.mp3", "HIGH", "auto_candidate", 0.99),
            _make_entry("/music/review-1.mp3", "MEDIUM", "review", 0.84, provider="deezer"),
            _make_entry("/music/review-2.mp3", "MEDIUM", "review", 0.80),
            _make_entry("/music/ignore.mp3", "LOW", "ignore", 0.20, provider=""),
        ],
    )

    rc = pipeline.run_enrichment_review(
        SimpleNamespace(root=str(tmp_path), confidence="MEDIUM", action="review", limit=1)
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Filter confidence: MEDIUM" in out
    assert "Filter action    : review" in out
    assert "Display count    : 1" in out
    assert "/music/review-1.mp3" in out or "/music/review-2.mp3" in out
    assert "/music/high.mp3" not in out
    assert "/music/ignore.mp3" not in out


def test_top_high_shows_best_high_entries(tmp_path, capsys):
    _write_queue(
        tmp_path,
        [
            _make_entry("/music/high-1.mp3", "HIGH", "auto_candidate", 0.99),
            _make_entry("/music/high-2.mp3", "HIGH", "auto_candidate", 0.96, title="Song B"),
            _make_entry("/music/review.mp3", "MEDIUM", "review", 0.80),
        ],
    )

    rc = pipeline.run_enrichment_review(
        SimpleNamespace(root=str(tmp_path), top_high=1)
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Top HIGH candidates (1)" in out
    assert "/music/high-1.mp3" in out
    assert "Display count    : 2" in out


def test_no_side_effects(tmp_path):
    queue_path = _write_queue(
        tmp_path,
        [_make_entry("/music/high.mp3", "HIGH", "auto_candidate", 0.99)],
    )
    before = queue_path.read_bytes()
    before_files = sorted(str(p) for p in tmp_path.rglob("*"))

    rc = pipeline.run_enrichment_review(SimpleNamespace(root=str(tmp_path)))

    after_files = sorted(str(p) for p in tmp_path.rglob("*"))

    assert rc == 0
    assert queue_path.read_bytes() == before
    assert after_files == before_files

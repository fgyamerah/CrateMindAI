import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pipeline
from modules import metadata_enrich_online as meo


def _create_tracks_db(root, rows):
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE tracks (
                filepath TEXT PRIMARY KEY,
                filename TEXT,
                artist TEXT,
                title TEXT,
                duration_sec REAL,
                label TEXT,
                isrc TEXT,
                status TEXT
            )
            """
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO tracks(
                    filepath, filename, artist, title, duration_sec, label, isrc, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("filepath"),
                    row.get("filename"),
                    row.get("artist"),
                    row.get("title"),
                    row.get("duration_sec"),
                    row.get("label"),
                    row.get("isrc"),
                    row.get("status", "ok"),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_scoring_logic_weights_title_artist_duration_and_label():
    track = meo.TrackInput(
        filepath="/music/Black Coffee - Superman.mp3",
        filename="Black Coffee - Superman.mp3",
        artist="Black Coffee",
        title="Superman",
        duration_sec=300,
        label="Soulistic",
    )
    scored = meo.score_candidate(
        track,
        {
            "provider": "spotify",
            "artist": "Black Coffee",
            "title": "Superman",
            "duration_sec": 300,
            "label": "Soulistic",
        },
    )

    assert scored["score"] == 1.0
    assert scored["confidence"] == "HIGH"
    assert scored["signals"]["title_similarity"] == 1.0
    assert scored["signals"]["artist_similarity"] == 1.0
    assert scored["signals"]["duration_similarity"] == 1.0
    assert scored["signals"]["label_similarity"] == 1.0


def test_exact_isrc_overrides_score_to_one():
    track = meo.TrackInput(
        filepath="/music/a.mp3",
        filename="a.mp3",
        artist="Different Artist",
        title="Different Title",
        isrc="ZA1234567890",
    )

    scored = meo.score_candidate(track, {"artist": "No", "title": "Match", "isrc": "ZA1234567890"})

    assert scored["score"] == 1.0
    assert scored["confidence"] == "HIGH"
    assert scored["signals"]["exact_isrc"] is True


def test_confidence_thresholds():
    assert meo.confidence_tier(0.92) == "HIGH"
    assert meo.confidence_tier(0.75) == "MEDIUM"
    assert meo.confidence_tier(0.7499) == "LOW"


def test_exact_mock_match_scores_high():
    track = meo.TrackInput(
        filepath="/music/Black Coffee - Superman.mp3",
        filename="Black Coffee - Superman.mp3",
        artist="Black Coffee",
        title="Superman",
        duration_sec=300,
        label="Soulistic",
    )
    query = {
        "artist": track.artist,
        "title": track.title,
        "filename": track.filename,
        "duration_sec": track.duration_sec,
        "label": track.label,
        "isrc": "",
    }

    exact = meo.search_spotify(query)[0]
    scored = meo.score_candidate(track, exact)

    assert scored["confidence"] == "HIGH"
    assert scored["score"] >= 0.92


def test_slight_mock_variation_scores_medium():
    track = meo.TrackInput(
        filepath="/music/Black Coffee - Superman.mp3",
        filename="Black Coffee - Superman.mp3",
        artist="Black Coffee",
        title="Superman",
        duration_sec=300,
        label="Soulistic",
    )
    query = {
        "artist": track.artist,
        "title": track.title,
        "filename": track.filename,
        "duration_sec": track.duration_sec,
        "label": track.label,
        "isrc": "",
    }

    noisy = meo.search_spotify(query)[1]
    scored = meo.score_candidate(track, noisy)

    assert scored["confidence"] == "MEDIUM"
    assert 0.75 <= scored["score"] < 0.92


def test_unrelated_mock_match_scores_low():
    track = meo.TrackInput(
        filepath="/music/Black Coffee - Superman.mp3",
        filename="Black Coffee - Superman.mp3",
        artist="Black Coffee",
        title="Superman",
        duration_sec=300,
        label="Soulistic",
    )
    query = {
        "artist": track.artist,
        "title": track.title,
        "filename": track.filename,
        "duration_sec": track.duration_sec,
        "label": track.label,
        "isrc": "",
    }

    unrelated = meo.search_spotify(query)[2]
    scored = meo.score_candidate(track, unrelated)

    assert scored["confidence"] == "LOW"
    assert scored["score"] < 0.75


def test_run_writes_jsonl_without_db_side_effects(tmp_path):
    track_path = tmp_path / "library" / "Black Coffee - Superman.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    db_path = _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "Black Coffee",
                "title": "Superman",
                "duration_sec": 300,
                "label": "Soulistic",
            }
        ],
    )
    before = db_path.read_bytes()

    def spotify(query):
        return [
            {
                "artist": "Black Coffee",
                "title": "Superman",
                "album": "Home Brewed",
                "label": "Soulistic",
                "duration_sec": 300,
            }
        ]

    result = meo.run(
        tmp_path,
        providers={"spotify": spotify, "deezer": lambda query: []},
        now=lambda: datetime(2026, 5, 5, 12, 0, 0),
    )

    assert db_path.read_bytes() == before
    assert result["tracks_scored"] == 1
    log_path = tmp_path / "logs" / "enrichment" / "20260505_120000_enrich_online.jsonl"
    assert result["log_path"] == str(log_path)
    assert result["queue_path"] == str(
        tmp_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    )
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["filepath"] == str(track_path)
    assert entry["query"] == {
        "artist": "Black Coffee",
        "title": "Superman",
        "filename": "Black Coffee - Superman.mp3",
        "duration_sec": 300.0,
        "label": "Soulistic",
        "isrc": "",
    }
    assert entry["best_match"]["album"] == "Home Brewed"
    assert entry["score"] >= 0.92
    assert entry["confidence"] == "HIGH"


def test_handles_missing_metadata_by_parsing_filename(tmp_path):
    track_path = tmp_path / "library" / "Caiiro - The Akan.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "",
                "title": "",
                "duration_sec": None,
                "label": "",
            }
        ],
    )

    seen_queries = []

    def deezer(query):
        seen_queries.append(query)
        return [{"artist": "Caiiro", "title": "The Akan", "provider": "deezer"}]

    result = meo.run(
        tmp_path,
        providers={"spotify": lambda query: [], "deezer": deezer},
        now=lambda: datetime(2026, 5, 5, 13, 0, 0),
    )

    assert seen_queries == [
        {
            "artist": "Caiiro",
            "title": "The Akan",
            "filename": "Caiiro - The Akan.mp3",
            "duration_sec": None,
            "label": "",
            "isrc": "",
        }
    ]
    assert result["entries"][0]["best_match"]["provider"] == "deezer"
    assert result["entries"][0]["confidence"] == "HIGH"


def test_missing_db_is_safe_and_creates_empty_log(tmp_path):
    result = meo.run(tmp_path, now=lambda: datetime(2026, 5, 5, 14, 0, 0))

    assert result["tracks_scored"] == 0
    log_path = tmp_path / "logs" / "enrichment" / "20260505_140000_enrich_online.jsonl"
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == ""
    queue_path = tmp_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    assert queue_path.exists()
    assert queue_path.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "logs" / "processed.db").exists()


def test_action_suggestions_follow_confidence_tiers():
    entries = [
        {
            "filepath": "/music/high.mp3",
            "query": {"artist": "A", "title": "T"},
            "best_match": {"provider": "spotify"},
            "score": 0.99,
            "confidence": "HIGH",
        },
        {
            "filepath": "/music/medium.mp3",
            "query": {"artist": "A", "title": "T"},
            "best_match": {"provider": "deezer"},
            "score": 0.85,
            "confidence": "MEDIUM",
        },
        {
            "filepath": "/music/low.mp3",
            "query": {"artist": "A", "title": "T"},
            "best_match": None,
            "score": 0.2,
            "confidence": "LOW",
        },
    ]

    queue = meo.build_review_queue_entries(entries, "2026-05-05T15:00:00")

    assert [entry["action_suggestion"] for entry in queue] == [
        "auto_candidate",
        "review",
        "ignore",
    ]
    assert queue[0]["provider"] == "spotify"
    assert queue[1]["provider"] == "deezer"
    assert queue[2]["provider"] == ""
    assert all(entry["timestamp"] == "2026-05-05T15:00:00" for entry in queue)


def test_mock_providers_return_deterministic_candidates():
    query = {
        "artist": "Black Coffee",
        "title": "Superman",
        "filename": "Black Coffee - Superman.mp3",
        "duration_sec": 300,
        "label": "Soulistic",
        "isrc": "",
    }

    first = meo.search_spotify(query) + meo.search_deezer(query)
    second = meo.search_spotify(query) + meo.search_deezer(query)

    assert first == second
    assert len(first) == 6
    assert {candidate["provider"] for candidate in first} == {"spotify", "deezer"}
    assert all({"artist", "title", "duration", "label", "isrc"} <= set(candidate) for candidate in first)


def test_default_provider_mode_returns_no_candidates(tmp_path):
    track_path = tmp_path / "library" / "Black Coffee - Superman.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    db_path = _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "Black Coffee",
                "title": "Superman",
                "duration_sec": 300,
                "label": "Soulistic",
            }
        ],
    )
    before = db_path.read_bytes()

    result = meo.run(tmp_path, now=lambda: datetime(2026, 5, 5, 15, 0, 0))

    assert db_path.read_bytes() == before
    assert result["entries"][0]["candidates"] == []
    assert result["entries"][0]["best_match"] is None
    assert result["entries"][0]["confidence"] == "LOW"


def test_mock_provider_mode_produces_high_medium_and_low(tmp_path):
    track_path = tmp_path / "library" / "Black Coffee - Superman.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    db_path = _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "Black Coffee",
                "title": "Superman",
                "duration_sec": 300,
                "label": "Soulistic",
            }
        ],
    )
    before = db_path.read_bytes()

    result = meo.run(
        tmp_path,
        mock_providers=True,
        now=lambda: datetime(2026, 5, 5, 15, 0, 0),
    )

    assert db_path.read_bytes() == before
    candidates = result["entries"][0]["candidates"]
    tiers = {candidate["confidence"] for candidate in candidates}
    assert {"HIGH", "MEDIUM", "LOW"} <= tiers
    assert result["entries"][0]["best_match"]["confidence"] == "HIGH"
    assert result["entries"][0]["confidence"] == "HIGH"


def test_review_queue_is_jsonl_and_preserves_read_only_behavior(tmp_path):
    track_path = tmp_path / "library" / "Black Coffee - Superman.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    db_path = _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "Black Coffee",
                "title": "Superman",
                "duration_sec": 300,
                "label": "Soulistic",
            }
        ],
    )
    before = db_path.read_bytes()

    result = meo.run(
        tmp_path,
        mock_providers=True,
        now=lambda: datetime(2026, 5, 5, 16, 0, 0),
    )

    assert db_path.read_bytes() == before
    queue_path = tmp_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    assert result["queue_path"] == str(queue_path)
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["filepath"] == str(track_path)
    assert entry["query"]["artist"] == "Black Coffee"
    assert entry["best_match"]["provider"] in {"spotify", "deezer"}
    assert entry["score"] >= 0.92
    assert entry["confidence"] == "HIGH"
    assert entry["action_suggestion"] == "auto_candidate"
    assert entry["timestamp"] == "2026-05-05T16:00:00"


def test_metadata_score_online_command_calls_scorer(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run(root, mock_providers=False):
        calls.append((root, mock_providers))
        return {
            "tracks_scored": 2,
            "log_path": str(root / "logs" / "enrichment" / "fake_enrich_online.jsonl"),
            "queue_path": str(root / "data" / "intelligence" / "enrichment_review_queue.jsonl"),
            "entries": [],
            "queue_entries": [],
        }

    monkeypatch.setattr(meo, "run", fake_run)

    rc = pipeline.run_metadata_score_online(
        SimpleNamespace(root=str(tmp_path), mock_providers=True)
    )

    assert rc == 0
    assert calls == [(tmp_path.resolve(), True)]
    out = capsys.readouterr().out
    assert "metadata-score-online" in out
    assert "Tracks scored : 2" in out


def test_metadata_score_online_command_passes_mock_flag(tmp_path, monkeypatch):
    calls = []

    def fake_run(root, mock_providers=False):
        calls.append(mock_providers)
        return {
            "tracks_scored": 0,
            "log_path": str(root / "logs" / "enrichment" / "fake.jsonl"),
            "queue_path": str(root / "data" / "intelligence" / "enrichment_review_queue.jsonl"),
            "entries": [],
            "queue_entries": [],
        }

    monkeypatch.setattr(meo, "run", fake_run)

    rc = pipeline.run_metadata_score_online(
        SimpleNamespace(root=str(tmp_path), mock_providers=True)
    )

    assert rc == 0
    assert calls == [True]


def test_metadata_score_online_command_preserves_read_only_behavior_and_writes_log(tmp_path):
    track_path = tmp_path / "library" / "Black Coffee - Superman.mp3"
    track_path.parent.mkdir()
    track_path.write_bytes(b"audio")
    db_path = _create_tracks_db(
        tmp_path,
        [
            {
                "filepath": str(track_path),
                "filename": track_path.name,
                "artist": "Black Coffee",
                "title": "Superman",
                "duration_sec": 300,
            }
        ],
    )
    before = db_path.read_bytes()

    rc = pipeline.run_metadata_score_online(
        SimpleNamespace(root=str(tmp_path), mock_providers=False)
    )

    assert rc == 0
    assert db_path.read_bytes() == before
    logs = list((tmp_path / "logs" / "enrichment").glob("*_enrich_online.jsonl"))
    assert len(logs) == 1
    entry = json.loads(logs[0].read_text(encoding="utf-8").strip())
    assert entry["filepath"] == str(track_path)
    assert entry["candidates"] == []
    assert entry["best_match"] is None
    assert entry["confidence"] == "LOW"
    queue = tmp_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    assert queue.exists()
    queue_entry = json.loads(queue.read_text(encoding="utf-8").strip())
    assert queue_entry["filepath"] == str(track_path)
    assert queue_entry["action_suggestion"] == "ignore"

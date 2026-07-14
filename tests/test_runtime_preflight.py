"""Tests for the read-only runtime preflight endpoint."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main


@pytest.fixture()
def client() -> TestClient:
    return TestClient(backend_main.app)


VALID_CHECK_STATUSES = {"pass", "warn", "fail"}
VALID_OVERALL = {"ready", "degraded", "unsafe"}
EXPECTED_CHECK_IDS = {
    "library_root",
    "pipeline_py",
    "jobs_storage",
    "rsync",
    "sync_source",
    "sync_dest",
    "provider_spotify",
    "provider_ollama",
}


def test_preflight_shape(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(tmp_path))
    resp = client.get("/api/runtime/preflight")
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] in VALID_OVERALL
    assert body["library_root"] == str(tmp_path)
    assert body["generated_at"]

    ids = {check["id"] for check in body["checks"]}
    assert EXPECTED_CHECK_IDS <= ids
    for check in body["checks"]:
        assert check["status"] in VALID_CHECK_STATUSES
        assert check["label"]
        assert isinstance(check["optional"], bool)


def test_preflight_missing_root_is_unsafe(client, monkeypatch):
    monkeypatch.setenv(
        "CRATEMINDAI_LIBRARY_ROOT", "/nonexistent-cratemindai-preflight-root"
    )
    body = client.get("/api/runtime/preflight").json()
    assert body["status"] == "unsafe"
    root_check = next(c for c in body["checks"] if c["id"] == "library_root")
    assert root_check["status"] == "fail"
    assert root_check["remediation"]


def test_preflight_missing_db_is_warn_not_fail(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(tmp_path))
    body = client.get("/api/runtime/preflight").json()
    db_check = next(c for c in body["checks"] if c["id"] == "pipeline_db")
    assert db_check["status"] == "warn"
    assert "not been scanned" in db_check["detail"]


def test_preflight_valid_db_passes(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(tmp_path))
    db_path = tmp_path / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    body = client.get("/api/runtime/preflight").json()
    db_check = next(c for c in body["checks"] if c["id"] == "pipeline_db")
    assert db_check["status"] == "pass"


def test_preflight_never_exposes_secret_values(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-id-value")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "super-secret-value")
    raw = client.get("/api/runtime/preflight").text
    assert "super-secret-value" not in raw
    assert "test-id-value" not in raw
    body = client.get("/api/runtime/preflight").json()
    spotify = next(c for c in body["checks"] if c["id"] == "provider_spotify")
    assert spotify["status"] == "pass"

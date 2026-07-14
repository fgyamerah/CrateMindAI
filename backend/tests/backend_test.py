"""CrateMindAI backend API smoke tests - iteration 1"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://dj-library-ops.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def test_health(s):
    r = s.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["db_exists"] is True


def test_preflight(s):
    r = s.get(f"{BASE_URL}/api/runtime/preflight", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] in {"ready", "degraded", "unsafe"}
    assert d["status"] == "ready"
    assert d["library_root"] == "/app/fixture_library"
    assert isinstance(d["checks"], list) and len(d["checks"]) > 0
    for c in d["checks"]:
        for k in ("id", "label", "status", "detail", "remediation", "optional"):
            assert k in c, f"missing {k} in check {c}"
        assert c["status"] in {"pass", "warn", "fail"}
    # No secret values leakage - check no actual key values (env var names in remediation are OK)
    import re
    body = r.text
    # Look for patterns like "value":"<long random string>" for known key names
    assert not re.search(r'"(api_key|access_token|refresh_token|password)"\s*:\s*"[^"]+"', body, re.I)


def test_stats(s):
    r = s.get(f"{BASE_URL}/api/stats", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["tracks_count"] == 310
    assert d["missing_files"] == 10
    assert d["untracked_files"] == 8


def test_tracks(s):
    r = s.get(f"{BASE_URL}/api/tracks?limit=5", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 310
    assert len(d["items"]) == 5


def test_metadata_repair_summary(s):
    r = s.get(f"{BASE_URL}/api/metadata-repair/summary", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["queue_total"] > 0


def test_enrichment_queue(s):
    r = s.get(f"{BASE_URL}/api/enrichment/queue?limit=1", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 24


def test_library_overview(s):
    r = s.get(f"{BASE_URL}/api/library/overview", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert "total_tracks" in d
    assert "tracks_with_bpm" in d
    assert "tracks_with_camelot_key" in d


def test_jobs(s):
    r = s.get(f"{BASE_URL}/api/jobs", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
